#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=scripts/browser-control-env.sh
source "$SCRIPT_DIR/browser-control-env.sh"
# shellcheck source=scripts/browser-maintenance-lease.sh
source "$SCRIPT_DIR/browser-maintenance-lease.sh"
RELEASE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly RELEASE_DIR
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly RELEASE_ROOT="$ROOT_DIR/releases"
readonly STATE_DIR="$ROOT_DIR/shared"
readonly APP_ENV="$RELEASE_DIR/production.env"
readonly DESIRED_APP_ENV="$STATE_DIR/.env"
readonly BACKUP_ENV="$STATE_DIR/pgbackrest.env"
readonly PGBACKREST_CONFIG="$STATE_DIR/pgbackrest.conf"
readonly ALLOY_ENV="$STATE_DIR/alloy-agent.env"
readonly BOOTSTRAP_SECRETS="$STATE_DIR/bootstrap-secrets.env"
readonly BROWSER_CONTROL_ENV="$STATE_DIR/browser-control.env"
readonly BROWSER_MAINTENANCE_ENV="$STATE_DIR/browser-maintenance.env"
readonly BROWSER_AUTOPAUSE_ENV="$STATE_DIR/browser-autopause.env"
readonly BROWSER_META_API_ENV="$STATE_DIR/browser-meta-api.env"
readonly BROWSER_CAMPAIGN_CREATOR_ENV="$STATE_DIR/browser-campaign-creator.env"
readonly BROWSER_AUTHORITY_ENV="$STATE_DIR/browser-authority.env"
readonly BOOTSTRAP_STATE="$STATE_DIR/bootstrap-state.json"
readonly DESKTOP_PROFILE_SEED_DIR="${FB_AGENT_DESKTOP_PROFILE_SEED_DIR:-$STATE_DIR/desktop-profile-seed}"
readonly VISION_WEBTOP_ROOT_DIR="${VISION_WEBTOP_ROOT:-/opt/vision-webtop}"
readonly RELEASE_ENV="$RELEASE_DIR/release-images.env"
readonly EFFECTIVE_CONFIG_FINGERPRINT="$RELEASE_DIR/.fb-agent-effective-config.sha256"
readonly RELEASE_CHECKSUMS="$RELEASE_DIR/.fb-agent-release"
readonly LOCK_FILE="$STATE_DIR/deploy.lock"
readonly VERIFIED_RELEASE_EXEC="/usr/local/libexec/fb-agent-release-verifier/current/verified-release-exec.py"
readonly ROLLBACK_DEADLINE_SECONDS=180
FIRST_RELEASE=false
ROLLBACK_ARMED=false
ROLLBACK_IN_PROGRESS=false
PRESERVE_MAINTENANCE_LEASE=false
ALLOY_CANDIDATE_STARTED=false
DESKTOP_CUTOVER_STARTED=false
DESKTOP_CUTOVER_COMPLETED=false
CANDIDATE_STATE=""
PREVIOUS_DESKTOP_STATE=""
EXPECTED_DESKTOP_CANDIDATE_STATE=""
CUTOVER_DEADLINE_EPOCH=""
ACTIVE_CHILD_PID=""
ACTIVE_CHILD_PGID=""
TEMP_DIR=""

die() {
  printf 'ERROR: %s\n' "$*" >&2
  if [[ "$ROLLBACK_ARMED" == true ]]; then
    rollback_parent_release 1
  fi
  exit 1
}
log() { printf '[server-platform-release] %s\n' "$*" >&2; }
cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ "$PRESERVE_MAINTENANCE_LEASE" == true ]]; then
    # A non-convergent desktop/app rollback must remain fail-closed. Stop the
    # process-local renewer, but leave the durable row to expire naturally so
    # no browser worker can claim during an ambiguous runtime state.
    browser_maintenance_stop_renewal
    if [[ -n "$BROWSER_MAINTENANCE_RUNTIME_DIR" ]]; then
      rm -rf -- "$BROWSER_MAINTENANCE_RUNTIME_DIR"
      BROWSER_MAINTENANCE_RUNTIME_DIR=""
    fi
  else
    if ! browser_maintenance_leave; then
      logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
        "CRITICAL coordinated browser maintenance lease could not be released"
      if ((exit_code == 0)); then
        exit_code=70
      fi
    fi
  fi
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

dotenv_value() {
  local -r file="$1"
  local -r key="$2"
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

state() {
  python3 "$SCRIPT_DIR/release-state.py" get --state-root "$STATE_DIR" "$@"
}

load_cutover_deadline() {
  local deadline=""
  deadline="$(state --source journal --field cutover_deadline_epoch 2>/dev/null)" \
    || return 1
  [[ "$deadline" =~ ^[0-9]+$ ]] || return 1
  CUTOVER_DEADLINE_EPOCH="$deadline"
}

cutover_remaining_seconds() {
  local -r now="$(date +%s)"
  local -r remaining=$((CUTOVER_DEADLINE_EPOCH - now))
  ((remaining > 0)) || return 1
  printf '%s\n' "$remaining"
}

run_cutover_bounded() {
  local -r label="$1"
  shift
  local remaining=""
  local soft_timeout=0
  local status=0
  remaining="$(cutover_remaining_seconds)" \
    || {
      printf 'ERROR: cutover step %s has no remaining absolute deadline budget\n' \
        "$label" >&2
      return 70
    }
  soft_timeout=$((remaining - 5))
  ((soft_timeout > 0)) \
    || {
      printf 'ERROR: cutover step %s has no shutdown grace before deadline\n' \
        "$label" >&2
      return 70
    }
  if timeout --signal=TERM --kill-after=5 "${soft_timeout}s" "$@"; then
    return 0
  else
    status=$?
  fi
  if ((status == 124 || status == 137)); then
    printf 'ERROR: cutover step %s exhausted the absolute deadline\n' "$label" >&2
    return 70
  fi
  return "$status"
}

require_cutover_reserve() {
  local -r required="$1"
  local -r label="$2"
  local remaining=""
  remaining="$(cutover_remaining_seconds)" || return 70
  if ((remaining < required)); then
    printf 'ERROR: cutover step %s requires %ss but only %ss remain\n' \
      "$label" "$required" "$remaining" >&2
    # The terminal epoch is still live: this is a deliberate forward cutoff,
    # not rollback exhaustion. A normal failure lets rollback_parent_release
    # restore/reconcile the app within the remaining immutable budget.
    return 1
  fi
}

run_supervised_child() {
  local status=0
  local remaining=""
  local soft_timeout=0
  [[ -z "$ACTIVE_CHILD_PID" ]] \
    || die "another supervised release child is already running"
  # A separate process group lets TERM/HUP stop the complete nested
  # Docker/Vision command tree before parent reconciliation can move pointers.
  if [[ "$CUTOVER_DEADLINE_EPOCH" =~ ^[0-9]+$ ]]; then
    remaining="$(cutover_remaining_seconds)" || return 70
    soft_timeout=$((remaining - 5))
    ((soft_timeout > 0)) || return 70
    python3 -c '
import os, sys
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
' timeout --signal=TERM --kill-after=5 "${soft_timeout}s" "$@" &
  else
    python3 -c '
import os, sys
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
' "$@" &
  fi
  ACTIVE_CHILD_PID=$!
  ACTIVE_CHILD_PGID="$ACTIVE_CHILD_PID"
  if wait "$ACTIVE_CHILD_PID"; then
    status=0
  else
    status=$?
  fi
  ACTIVE_CHILD_PID=""
  ACTIVE_CHILD_PGID=""
  if ((status == 124 || status == 137)); then
    return 70
  fi
  return "$status"
}

terminate_supervised_child() {
  local wait_deadline=0
  local now=0
  local observed_pgid=""
  local attempt=0
  [[ -n "$ACTIVE_CHILD_PID" && -n "$ACTIVE_CHILD_PGID" ]] || return 0
  observed_pgid="$(
    ps -o pgid= -p "$ACTIVE_CHILD_PID" 2>/dev/null \
      | tr -d '[:space:]'
  )"
  if [[ "$observed_pgid" == "$ACTIVE_CHILD_PGID" ]]; then
    # One group TERM stops the current nested command and lets the desktop
    # shell run its rollback trap. Repeated TERM would also kill rollback
    # commands, so all later waiting is passive.
    kill -TERM -- "-$ACTIVE_CHILD_PGID" >/dev/null 2>&1 || true
  else
    # Close the fork -> setsid race. Before the child owns an isolated process
    # group, terminating its exact PID is safer than signaling the parent's
    # group. If it establishes the group concurrently, send exactly one group
    # TERM as soon as that identity becomes observable.
    kill -TERM "$ACTIVE_CHILD_PID" >/dev/null 2>&1 || true
    for attempt in 1 2 3 4 5; do
      kill -0 "$ACTIVE_CHILD_PID" >/dev/null 2>&1 || break
      observed_pgid="$(
        ps -o pgid= -p "$ACTIVE_CHILD_PID" 2>/dev/null \
          | tr -d '[:space:]'
      )"
      if [[ "$observed_pgid" == "$ACTIVE_CHILD_PGID" ]]; then
        kill -TERM -- "-$ACTIVE_CHILD_PGID" >/dev/null 2>&1 || true
        break
      fi
      sleep 0.1
    done
  fi
  if [[ "$CUTOVER_DEADLINE_EPOCH" =~ ^[0-9]+$ ]]; then
    wait_deadline="$CUTOVER_DEADLINE_EPOCH"
  else
    # Preflight precedes public mutation and has no durable cutover epoch.
    wait_deadline=$(( $(date +%s) + 15 ))
  fi
  while kill -0 "$ACTIVE_CHILD_PID" >/dev/null 2>&1; do
    now="$(date +%s)"
    ((now < wait_deadline)) || break
    sleep 1
  done
  if kill -0 "$ACTIVE_CHILD_PID" >/dev/null 2>&1; then
    kill -KILL -- "-$ACTIVE_CHILD_PGID" >/dev/null 2>&1 || true
  fi
  wait "$ACTIVE_CHILD_PID" >/dev/null 2>&1 || true
  ACTIVE_CHILD_PID=""
  ACTIVE_CHILD_PGID=""
}

classify_desktop_release_outcome() {
  local -r transaction_status="$1"
  local -r active_state="$2"
  local -r candidate_state="$3"
  local -r previous_state="$4"
  local -r journal_candidate="$5"
  local -r journal_previous="$6"
  local candidate_name=""
  local previous_name=""

  candidate_name="${candidate_state##*/}"
  if [[ -n "$previous_state" ]]; then
    previous_name="${previous_state##*/}"
  fi

  case "$transaction_status" in
    none)
      if [[ -n "$active_state" && "$active_state" == "$candidate_state" ]]; then
        printf 'candidate_final\n'
      elif [[ -n "$previous_state" && "$active_state" == "$previous_state" ]]; then
        printf 'previous_final\n'
      elif [[ -z "$previous_state" && -z "$active_state" ]]; then
        printf 'absent_final\n'
      else
        printf 'invalid\n'
      fi
      ;;
    candidate)
      if [[ "$journal_candidate" == "$candidate_name" \
        && "$journal_previous" == "$previous_name" \
        && "$active_state" == "$candidate_state" ]]; then
        printf 'candidate_pending\n'
      else
        printf 'invalid\n'
      fi
      ;;
    previous)
      if [[ -n "$previous_state" \
        && "$journal_candidate" == "$candidate_name" \
        && "$journal_previous" == "$previous_name" \
        && "$active_state" == "$previous_state" ]]; then
        printf 'previous_pending\n'
      else
        printf 'invalid\n'
      fi
      ;;
    absent)
      if [[ -z "$previous_state" \
        && "$journal_candidate" == "$candidate_name" \
        && -z "$journal_previous" \
        && -z "$active_state" ]]; then
        printf 'absent_pending\n'
      else
        printf 'invalid\n'
      fi
      ;;
    *) printf 'invalid\n' ;;
  esac
}

desktop_release_outcome() {
  local active_desktop_state=""
  local journal_candidate=""
  local journal_previous=""
  local remaining=""
  local transaction_status=""
  local verification_timeout=10

  [[ -n "$EXPECTED_DESKTOP_CANDIDATE_STATE" ]] \
    || {
      printf 'invalid\n'
      return 0
    }
  if [[ "$CUTOVER_DEADLINE_EPOCH" =~ ^[0-9]+$ ]]; then
    remaining="$(cutover_remaining_seconds)" \
      || {
        printf 'invalid\n'
        return 0
      }
    if ((remaining < verification_timeout)); then
      verification_timeout="$remaining"
    fi
  fi

  transaction_status="$(
    timeout --foreground --signal=KILL "${verification_timeout}s" \
      "$SCRIPT_DIR/platform-desktop-transaction.sh" status
  )" \
    || {
      printf 'invalid\n'
      return 0
    }
  case "$transaction_status" in
    none) ;;
    candidate|previous|absent)
      [[ -f "$STATE_DIR/desktop-transaction.env" \
        && ! -L "$STATE_DIR/desktop-transaction.env" ]] \
        || {
          printf 'invalid\n'
          return 0
        }
      journal_candidate="$(
        dotenv_value "$STATE_DIR/desktop-transaction.env" candidate_state
      )"
      journal_previous="$(
        dotenv_value "$STATE_DIR/desktop-transaction.env" previous_state
      )"
      ;;
    *)
      printf 'invalid\n'
      return 0
      ;;
  esac

  if [[ -e "$STATE_DIR/active-desktop-state" \
    || -L "$STATE_DIR/active-desktop-state" ]]; then
    [[ -L "$STATE_DIR/active-desktop-state" ]] \
      || {
        printf 'invalid\n'
        return 0
      }
    active_desktop_state="$(
      readlink -f "$STATE_DIR/active-desktop-state"
    )" \
      || {
        printf 'invalid\n'
        return 0
      }
    [[ -d "$active_desktop_state" \
      && "$(dirname -- "$active_desktop_state")" == \
        "$STATE_DIR/desktop-states" ]] \
      || {
        printf 'invalid\n'
        return 0
      }
    remaining="$(cutover_remaining_seconds)" \
      || {
        printf 'invalid\n'
        return 0
      }
    verification_timeout=10
    if ((remaining < verification_timeout)); then
      verification_timeout="$remaining"
    fi
    timeout --foreground --signal=KILL "${verification_timeout}s" \
      python3 "$SCRIPT_DIR/release-state.py" desktop-verify \
        --state-root "$STATE_DIR" --state-dir "$active_desktop_state" \
        >/dev/null \
      || {
        printf 'invalid\n'
        return 0
      }
  fi

  classify_desktop_release_outcome \
    "$transaction_status" \
    "$active_desktop_state" \
    "$EXPECTED_DESKTOP_CANDIDATE_STATE" \
    "$PREVIOUS_DESKTOP_STATE" \
    "$journal_candidate" \
    "$journal_previous"
}

desktop_cutover_snapshot_is_current() {
  local active_desktop_state=""
  local transaction_status=""

  # The maintenance lease is the serialization boundary for browser-backed
  # work. Re-read the independently owned desktop control plane only after
  # that fence has quiesced, immediately before preflight/application cutover.
  transaction_status="$(
    "$SCRIPT_DIR/platform-desktop-transaction.sh" status
  )" || return 1
  [[ "$transaction_status" == none ]] || return 1

  if [[ -e "$STATE_DIR/active-desktop-state" \
    || -L "$STATE_DIR/active-desktop-state" ]]; then
    [[ "$FIRST_RELEASE" == false \
      && -n "$PREVIOUS_DESKTOP_STATE" \
      && -L "$STATE_DIR/active-desktop-state" ]] \
      || return 1
    active_desktop_state="$(
      readlink -f "$STATE_DIR/active-desktop-state"
    )" || return 1
    [[ "$active_desktop_state" == "$PREVIOUS_DESKTOP_STATE" \
      && -d "$active_desktop_state" \
      && "$(dirname -- "$active_desktop_state")" == \
        "$STATE_DIR/desktop-states" ]] \
      || return 1
    python3 "$SCRIPT_DIR/release-state.py" desktop-verify \
      --state-root "$STATE_DIR" \
      --state-dir "$active_desktop_state" >/dev/null \
      || return 1
  else
    [[ "$FIRST_RELEASE" == true && -z "$PREVIOUS_DESKTOP_STATE" ]] \
      || return 1
  fi

  # Detect a transaction that appeared while the pointer identity was being
  # verified. Legitimate independent actors remain fenced by maintenance.
  transaction_status="$(
    "$SCRIPT_DIR/platform-desktop-transaction.sh" status
  )" || return 1
  [[ "$transaction_status" == none ]]
}

initial_forward_resume_matches_current_release() {
  local active_app_env=""
  local active_release_dir=""
  local active_release_env=""
  local active_state=""
  local candidate_state=""
  local journal_stage=""

  [[ -f "$STATE_DIR/release-transaction.json" \
    && ! -L "$STATE_DIR/release-transaction.json" \
    && -L "$STATE_DIR/active-state" \
    && ! -e "$STATE_DIR/active-desktop-state" \
    && ! -L "$STATE_DIR/active-desktop-state" ]] \
    || return 1
  [[ "$(state --source journal --field recovery_policy)" == \
    initial_forward_only ]] \
    || return 1
  journal_stage="$(state --source journal --field stage)" || return 1
  case "$journal_stage" in
    committed|alloy_adopted|timers_adopted|systemd_adopted) ;;
    *) return 1 ;;
  esac
  active_state="$(state --source active --field state_dir)" || return 1
  candidate_state="$(state --source candidate --field state_dir)" || return 1
  [[ "$active_state" == "$candidate_state" ]] || return 1
  if state --source previous --field state_dir >/dev/null 2>&1; then
    return 1
  fi
  if state --source journal --field rollback_requested_at >/dev/null 2>&1; then
    return 1
  fi
  [[ "$(state --source active --field color)" == blue \
    && "$(state --source active --field release_id)" == "$release_id" ]] \
    || return 1
  active_release_dir="$(state --source active --field release_dir)" || return 1
  active_app_env="$(state --source active --field app_env)" || return 1
  active_release_env="$(state --source active --field release_env)" || return 1
  [[ "$active_release_dir" == "$RELEASE_DIR" ]] || return 1
  cmp -s -- "$active_app_env" "$APP_ENV" || return 1
  cmp -s -- "$active_release_env" "$RELEASE_ENV" || return 1
  [[ "$("$SCRIPT_DIR/platform-desktop-transaction.sh" status)" == none ]]
}

resume_exact_initial_forward_release() {
  local status=0
  if timeout --foreground --signal=TERM --kill-after=5 185s \
    env FB_AGENT_ROOT="$ROOT_DIR" \
    "$SCRIPT_DIR/reconcile-platform-release.sh" \
      --resume-initial-forward \
      --deadline-seconds "$ROLLBACK_DEADLINE_SECONDS" \
      --expected-release-dir "$RELEASE_DIR" \
      --expected-app-env "$APP_ENV" \
      --expected-release-env "$RELEASE_ENV"; then
    return 0
  else
    status=$?
  fi
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
    "CRITICAL exact initial forward recovery failed (exit $status)"
  return 70
}

initial_forward_resume_is_complete() {
  local active_app_env=""
  local active_desktop_state=""
  local active_release_dir=""
  local active_release_env=""
  local desktop_fingerprint=""
  local expected_desktop_state=""

  [[ ! -e "$STATE_DIR/release-transaction.json" \
    && ! -L "$STATE_DIR/release-transaction.json" ]] \
    || return 1
  [[ "$(state --source active --field color)" == blue \
    && "$(state --source active --field release_id)" == "$release_id" ]] \
    || return 1
  active_release_dir="$(state --source active --field release_dir)" || return 1
  active_app_env="$(state --source active --field app_env)" || return 1
  active_release_env="$(state --source active --field release_env)" || return 1
  [[ "$active_release_dir" == "$RELEASE_DIR" ]] || return 1
  cmp -s -- "$active_app_env" "$APP_ENV" || return 1
  cmp -s -- "$active_release_env" "$RELEASE_ENV" || return 1
  desktop_fingerprint="$(
    python3 "$SCRIPT_DIR/release-state.py" desktop-digest \
      --release-dir "$RELEASE_DIR" \
      --app-env "$active_app_env" \
      --release-env "$active_release_env"
  )" || return 1
  [[ "$desktop_fingerprint" =~ ^[0-9a-f]{64}$ ]] || return 1
  expected_desktop_state="$STATE_DIR/desktop-states/${release_id}-${desktop_fingerprint:0:16}"
  [[ -L "$STATE_DIR/active-desktop-state" ]] || return 1
  active_desktop_state="$(
    readlink -f "$STATE_DIR/active-desktop-state"
  )" || return 1
  [[ "$active_desktop_state" == "$expected_desktop_state" ]] || return 1
  python3 "$SCRIPT_DIR/release-state.py" desktop-verify \
    --state-root "$STATE_DIR" --state-dir "$active_desktop_state" \
    >/dev/null \
    || return 1
  [[ "$("$SCRIPT_DIR/platform-desktop-transaction.sh" status)" == none ]]
}

reconcile_or_mark_critical() {
  local -r reason="$1"
  local deadline=""
  local remaining=""
  local status=0
  local -a reconcile_args=()
  if deadline="$(state --source journal --field cutover_deadline_epoch 2>/dev/null)" \
    && [[ "$deadline" =~ ^[0-9]+$ ]]; then
    CUTOVER_DEADLINE_EPOCH="$deadline"
    if ! remaining="$(cutover_remaining_seconds)"; then
      python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
        --state-root "$STATE_DIR" \
        --failure "${reason}:cutover_deadline_exhausted"
      logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
        "CRITICAL release reconciliation refused a second cutover budget (${reason})"
      return 70
    fi
    reconcile_args=(--deadline-epoch "$CUTOVER_DEADLINE_EPOCH")
  else
    # Before arm-cutover no route/worker commit point moved, so a bounded
    # cleanup window is not a replacement for an already-started cutover.
    remaining="$ROLLBACK_DEADLINE_SECONDS"
    reconcile_args=(--deadline-seconds "$ROLLBACK_DEADLINE_SECONDS")
  fi
  if timeout --signal=KILL "${remaining}s" \
    "$SCRIPT_DIR/reconcile-platform-release.sh" "${reconcile_args[@]}"; then
    return 0
  else
    status=$?
  fi
  python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
    --state-root "$STATE_DIR" --failure "${reason}:exit_${status}"
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
    "CRITICAL release reconciliation failed within ${ROLLBACK_DEADLINE_SECONDS}s (${reason})"
  return 70
}

preserve_ambiguous_desktop_release() {
  local -r failure="$1"
  python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
    --state-root "$STATE_DIR" --failure "$failure" >/dev/null 2>&1 || true
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
    "CRITICAL coordinated desktop release is not converged: $failure" \
    >/dev/null 2>&1 || true
  PRESERVE_MAINTENANCE_LEASE=true
  exit 70
}

converge_pending_desktop_rollback() {
  local expected_outcome=absent_final
  local outcome=""
  if [[ -n "$PREVIOUS_DESKTOP_STATE" ]]; then
    expected_outcome=previous_final
  fi
  run_supervised_child env FB_AGENT_ROOT="$ROOT_DIR" \
    "$SCRIPT_DIR/platform-desktop-release.sh" \
      --release-env "$candidate_release_env" \
      --app-env "$candidate_app_env" \
      --profile-seed-dir "$DESKTOP_PROFILE_SEED_DIR" \
      --deadline-epoch "$CUTOVER_DEADLINE_EPOCH" \
      --rollback-only \
    || return 1
  outcome="$(desktop_release_outcome)"
  [[ "$outcome" == "$expected_outcome" ]]
}

rollback_parent_release() {
  local -r exit_code="$1"
  local -r rollback_was_armed="$ROLLBACK_ARMED"
  local alloy_cleanup_failed=false
  local desktop_outcome=""
  local remaining=""
  if [[ "$ROLLBACK_IN_PROGRESS" == true ]]; then
    PRESERVE_MAINTENANCE_LEASE=true
    exit 70
  fi
  ROLLBACK_IN_PROGRESS=true
  ROLLBACK_ARMED=false
  trap - ERR
  # Rollback is already bounded by the immutable terminal epoch. Catch and
  # ignore repeated operator signals so they cannot interrupt pointer/runtime
  # convergence; external commands retain their normal signal disposition.
  trap ':' HUP TERM INT
  if [[ "$ALLOY_CANDIDATE_STARTED" == true ]]; then
    if FB_AGENT_ROOT="$ROOT_DIR" FB_AGENT_RELEASE_DIR="$RELEASE_DIR" \
      "$SCRIPT_DIR/platform-alloy-agent.sh" candidate-cleanup; then
      ALLOY_CANDIDATE_STARTED=false
    else
      alloy_cleanup_failed=true
      logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
        "CRITICAL isolated Alloy candidate cleanup failed during release rollback"
    fi
  fi
  # Pointer direction and runtime convergence are distinct. Only a completed
  # desktop transaction may authorize the coordinated app rollback or release
  # the maintenance fence.
  if [[ "$DESKTOP_CUTOVER_STARTED" == true \
    && "$DESKTOP_CUTOVER_COMPLETED" == false ]]; then
    desktop_outcome="$(desktop_release_outcome)"
    case "$desktop_outcome" in
      candidate_final)
        # Exit 75 is emitted only after the desktop journal was durably
        # completed and a signal/error arrived in the disarm instruction gap.
        # Any other failed invocation against a pre-existing final state must
        # remain fenced because its direct readiness check did not succeed.
        if ((exit_code == 75)); then
          DESKTOP_CUTOVER_COMPLETED=true
        else
          preserve_ambiguous_desktop_release \
            "desktop_candidate_final_without_successful_child_acceptance"
        fi
        ;;
      candidate_pending)
        preserve_ambiguous_desktop_release \
          "desktop_candidate_pending_forward_reconciliation"
        ;;
      previous_pending|absent_pending)
        load_cutover_deadline \
          && cutover_remaining_seconds >/dev/null \
          && converge_pending_desktop_rollback \
          || preserve_ambiguous_desktop_release \
            "desktop_previous_pending_rollback_nonconvergent"
        desktop_outcome="$(desktop_release_outcome)"
        ;;
      previous_final|absent_final) ;;
      invalid|*)
        preserve_ambiguous_desktop_release \
          "desktop_release_outcome_invalid"
        ;;
    esac

    case "$desktop_outcome" in
      previous_final)
        [[ "$FIRST_RELEASE" == false && -n "$PREVIOUS_DESKTOP_STATE" ]] \
          || preserve_ambiguous_desktop_release \
            "desktop_previous_final_without_previous_application"
        ;;
      absent_final)
        # A first release is forward-only once its application pointer has
        # committed. There is no previous app to pair with an absent desktop.
        preserve_ambiguous_desktop_release \
          "initial_desktop_absent_after_application_commit"
        ;;
      candidate_final) ;;
      *)
        preserve_ambiguous_desktop_release \
          "desktop_rollback_did_not_reach_a_final_outcome"
        ;;
    esac

    if [[ "$desktop_outcome" == previous_final ]] \
      && { ! load_cutover_deadline \
        || ! remaining="$(cutover_remaining_seconds)" \
        || ! timeout --foreground --signal=KILL "${remaining}s" \
          python3 "$SCRIPT_DIR/release-state.py" rollback-commit \
            --state-root "$STATE_DIR"; }; then
      python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
        --state-root "$STATE_DIR" \
        --failure "coordinated_app_browser_rollback:pointer_restore_failed"
      logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
        "CRITICAL previous browser was restored but application pointer rollback failed"
      PRESERVE_MAINTENANCE_LEASE=true
      exit 70
    fi
  fi
  if ((exit_code == 70)); then
    PRESERVE_MAINTENANCE_LEASE=true
    exit 70
  fi
  # A successful child reconciliation archives the journal.  Only parent
  # failures outside that child still need a fresh reconciliation attempt.
  if [[ "$rollback_was_armed" == true \
    && -f "$STATE_DIR/release-transaction.json" ]]; then
    printf 'ERROR: platform release failed; reconciling to the atomic active-state pointer\n' >&2
    reconcile_or_mark_critical "parent_release_rollback" || {
      PRESERVE_MAINTENANCE_LEASE=true
      exit 70
    }
  fi
  if [[ "$alloy_cleanup_failed" == true ]]; then
    PRESERVE_MAINTENANCE_LEASE=true
    exit 70
  fi
  exit "$exit_code"
}

handle_termination() {
  local -r exit_code="$1"
  trap ':' HUP TERM INT
  terminate_supervised_child
  if [[ "$ROLLBACK_ARMED" == true && "$ROLLBACK_IN_PROGRESS" == false ]]; then
    rollback_parent_release "$exit_code"
  fi
  exit "$exit_code"
}
trap 'handle_termination 129' HUP
trap 'handle_termination 143' TERM
trap 'handle_termination 130' INT

while (($#)); do
  case "$1" in
    *) die "unknown argument: $1" ;;
  esac
done

for command in cmp cut date docker find flock grep install logger mktemp ps python3 readlink rm sed sha256sum sleep sort stat systemctl tail timeout tr; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
for file in \
  "$DESIRED_APP_ENV" \
  "$BACKUP_ENV" \
  "$BOOTSTRAP_SECRETS" \
  "$BROWSER_CONTROL_ENV" \
  "$BROWSER_MAINTENANCE_ENV" \
  "$BROWSER_AUTOPAUSE_ENV" \
  "$BROWSER_META_API_ENV" \
  "$BROWSER_CAMPAIGN_CREATOR_ENV" \
  "$BROWSER_AUTHORITY_ENV"; do
  [[ -f "$file" && ! -L "$file" ]] \
    || die "required regular release file is missing: $file"
done
[[ -f "$ALLOY_ENV" && ! -L "$ALLOY_ENV" ]] \
  || die "required Alloy environment is missing: $ALLOY_ENV"
[[ "$(stat -Lc '%a' "$ALLOY_ENV")" == "600" ]] || die "$ALLOY_ENV must have mode 600"
for file in \
  "$DESIRED_APP_ENV" \
  "$BACKUP_ENV" \
  "$BOOTSTRAP_SECRETS" \
  "$BROWSER_CONTROL_ENV" \
  "$BROWSER_MAINTENANCE_ENV" \
  "$BROWSER_AUTOPAUSE_ENV" \
  "$BROWSER_META_API_ENV" \
  "$BROWSER_CAMPAIGN_CREATOR_ENV" \
  "$BROWSER_AUTHORITY_ENV"; do
  [[ "$(stat -Lc '%a' "$file")" == "600" ]] || die "$file must have mode 600"
done
browser_control_env_require "$BROWSER_CONTROL_ENV" \
  || die "browser control environment failed the private-file contract"
browser_maintenance_env_require "$BROWSER_MAINTENANCE_ENV" \
  || die "browser maintenance environment failed the private-file contract"
for operation_env in \
  "$BROWSER_AUTOPAUSE_ENV" \
  "$BROWSER_META_API_ENV" \
  "$BROWSER_CAMPAIGN_CREATOR_ENV"; do
  browser_operation_env_require "$operation_env" \
    || die "browser operation environment failed the private-file contract"
done
browser_authority_env_require "$BROWSER_AUTHORITY_ENV" \
  || die "browser authority environment failed the private-file contract"
export BROWSER_CONTROL_ENV_FILE="$BROWSER_CONTROL_ENV"
export BROWSER_MAINTENANCE_ENV_FILE="$BROWSER_MAINTENANCE_ENV"
export BROWSER_AUTOPAUSE_ENV_FILE="$BROWSER_AUTOPAUSE_ENV"
export BROWSER_META_API_ENV_FILE="$BROWSER_META_API_ENV"
export BROWSER_CAMPAIGN_CREATOR_ENV_FILE="$BROWSER_CAMPAIGN_CREATOR_ENV"
export BROWSER_AUTHORITY_ENV_FILE="$BROWSER_AUTHORITY_ENV"
for file in \
  "$APP_ENV" \
  "$RELEASE_ENV" \
  "$EFFECTIVE_CONFIG_FINGERPRINT" \
  "$RELEASE_CHECKSUMS"; do
  [[ -f "$file" && ! -L "$file" ]] \
    || die "required immutable release file is missing: $file"
  [[ "$(stat -Lc '%a' "$file")" == "400" ]] \
    || die "$file must have sealed mode 400"
done
[[ -f "$RELEASE_DIR/.fb-agent-source-manifest.json" \
  && ! -L "$RELEASE_DIR/.fb-agent-source-manifest.json" ]] \
  || die "immutable source manifest is missing"
[[ "$(stat -Lc '%a' "$RELEASE_DIR/.fb-agent-source-manifest.json")" == "400" ]] \
  || die "immutable source manifest must have sealed mode 400"
(
  cd "$RELEASE_DIR"
  sha256sum --check --strict .fb-agent-release >/dev/null
)
python3 "$SCRIPT_DIR/release-state.py" manifest-verify \
  --release-dir "$RELEASE_DIR" \
  --manifest "$RELEASE_DIR/.fb-agent-source-manifest.json" \
  --require-read-only >/dev/null
declared_config_fingerprint="$(<"$EFFECTIVE_CONFIG_FINGERPRINT")"
[[ "$declared_config_fingerprint" =~ ^[0-9a-f]{64}$ ]] \
  || die "effective production config fingerprint is invalid"
[[ "$(sha256sum "$APP_ENV" | cut -d' ' -f1)" == "$declared_config_fingerprint" ]] \
  || die "effective production config fingerprint does not match production.env"

bootstrap_cluster_id="$(dotenv_value "$BOOTSTRAP_SECRETS" FB_AGENT_BOOTSTRAP_CLUSTER_ID)"
bootstrap_postgres_password="$(dotenv_value "$BOOTSTRAP_SECRETS" POSTGRES_PASSWORD)"
[[ "$bootstrap_cluster_id" =~ ^[0-9a-f]{32}$ ]] \
  || die "durable bootstrap cluster id is invalid"
[[ ${#bootstrap_postgres_password} -ge 16 ]] \
  || die "durable bootstrap PostgreSQL password is invalid"
[[ "$(dotenv_value "$APP_ENV" FB_AGENT_BOOTSTRAP_CLUSTER_ID)" == "$bootstrap_cluster_id" ]] \
  || die "candidate environment belongs to a different bootstrap cluster"
[[ "$(dotenv_value "$APP_ENV" POSTGRES_PASSWORD)" == "$bootstrap_postgres_password" ]] \
  || die "candidate PostgreSQL password differs from durable bootstrap state"

readonly POSTGRES_VOLUME="fb_agent_safety_first_pgdata"
readonly REDIS_VOLUME="fb_agent_safety_first_redisdata"
readonly PGBACKREST_SPOOL_VOLUME="fb_agent_safety_first_pgbackrest_spool"
readonly CAMPAIGN_UPLOAD_VOLUME="fb_agent_safety_first_campaign_uploads"
readonly PLATFORM_NETWORK="fb_agent_safety_first_platform"
for resource_contract in \
  "POSTGRES_VOLUME:$POSTGRES_VOLUME" \
  "REDIS_VOLUME:$REDIS_VOLUME" \
  "PGBACKREST_SPOOL_VOLUME:$PGBACKREST_SPOOL_VOLUME" \
  "CAMPAIGN_UPLOAD_VOLUME:$CAMPAIGN_UPLOAD_VOLUME" \
  "PLATFORM_NETWORK:$PLATFORM_NETWORK"; do
  resource_key="${resource_contract%%:*}"
  expected_resource="${resource_contract#*:}"
  manifest_resource="$(dotenv_value "$RELEASE_ENV" "$resource_key")"
  [[ -z "$manifest_resource" || "$manifest_resource" == "$expected_resource" ]] \
    || die "$resource_key must use the canonical safety-first resource"
done
export FB_AGENT_BOOTSTRAP_CLUSTER_ID="$bootstrap_cluster_id"
export POSTGRES_VOLUME REDIS_VOLUME PGBACKREST_SPOOL_VOLUME
export CAMPAIGN_UPLOAD_VOLUME PLATFORM_NETWORK

release_id="$(dotenv_value "$RELEASE_ENV" RELEASE_ID)"
[[ "$release_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid release id"
exec 9>"$LOCK_FILE"
flock -n 9 || die "another deployment is already running"
export FB_AGENT_DEPLOY_LOCK_FD=9
TEMP_DIR="$(mktemp -d)"

# The desired environment is operator-owned input. Validate its complete
# effective rendering before reconciliation, installer adoption or any other
# durable mutation. A same-RELEASE_ID retry after an operator rotation must
# fail without restoring bytes from the currently active release.
expected_app_env="$TEMP_DIR/production.env"
python3 "$SCRIPT_DIR/prepare_production_env.py" \
  --input "$DESIRED_APP_ENV" \
  --bootstrap-secrets "$BOOTSTRAP_SECRETS" \
  --output "$expected_app_env" \
  --public-url "https://app.adpulse.su" \
  --desktop-webtop-image "$(dotenv_value "$RELEASE_ENV" DESKTOP_WEBTOP_IMAGE)" \
  --desktop-kasmvnc-image "$(dotenv_value "$RELEASE_ENV" DESKTOP_KASMVNC_IMAGE)" \
  >/dev/null
cmp -s -- "$expected_app_env" "$APP_ENV" \
  || die "desired effective production config changed after immutable release render"
[[ "$(sha256sum "$expected_app_env" | cut -d' ' -f1)" == "$declared_config_fingerprint" ]] \
  || die "desired effective production config fingerprint mismatch"

FB_AGENT_ROOT="$ROOT_DIR" "$SCRIPT_DIR/install-alloy-agent-unit.sh" --validate-only

# PostgreSQL keeps this path across release pruning and container restarts.
# Configuration policy changes require an explicit operator update instead of
# silently changing backup semantics as a side effect of an app release.
if [[ -e "$PGBACKREST_CONFIG" || -L "$PGBACKREST_CONFIG" ]]; then
  [[ -f "$PGBACKREST_CONFIG" && ! -L "$PGBACKREST_CONFIG" ]] \
    || die "stable pgBackRest config must be a regular file"
  cmp -s -- "$RELEASE_DIR/deploy/backup/pgbackrest.conf" "$PGBACKREST_CONFIG" \
    || die "pgBackRest config changed; update it explicitly in shared state before deploy"
else
  install -m 0644 "$RELEASE_DIR/deploy/backup/pgbackrest.conf" "$PGBACKREST_CONFIG"
fi
export PGBACKREST_CONFIG_FILE="$PGBACKREST_CONFIG"
install -d -m 0700 "$STATE_DIR/desktop-readiness" "$STATE_DIR/desktop-readiness/states"
export DESKTOP_READINESS_DIR="$STATE_DIR/desktop-readiness"

# Complete or roll back a transaction left by SIGKILL/power loss before the
# next candidate is rendered into durable release state. An unselected first
# candidate never changed public state and is safely aborted; after initial
# forward selection, normal reconciliation converges to that candidate.
if [[ -f "$STATE_DIR/release-transaction.json" ]]; then
  if initial_forward_resume_matches_current_release; then
    log "resuming exact committed first release with an absent desktop"
    resume_exact_initial_forward_release \
      || exit 70
    initial_forward_resume_is_complete \
      || {
        python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
          --state-root "$STATE_DIR" \
          --failure "initial_forward_resume:postcondition_failed"
        exit 70
      }
    printf 'Platform release %s completed initial desktop recovery\n' "$release_id"
    exit 0
  elif [[ -e "$STATE_DIR/active-state" || -L "$STATE_DIR/active-state" ]]; then
    reconcile_or_mark_critical "pre_release_reconciliation" || exit 70
  else
    recovery_policy="$(state --source journal --field recovery_policy 2>/dev/null || true)"
    if [[ "$recovery_policy" == initial_forward_only ]]; then
      reconcile_or_mark_critical "initial_release_reconciliation" || exit 70
    elif [[ -z "$recovery_policy" ]]; then
      reconcile_or_mark_critical "initial_preselection_reconciliation" || exit 70
    else
      die "unsupported recovery policy without an active release: $recovery_policy"
    fi
  fi
fi

if [[ -e "$STATE_DIR/active-state" || -L "$STATE_DIR/active-state" ]]; then
  active_color="$(state --source active --field color)"
  active_app_env="$(state --source active --field app_env)"
  active_release_env="$(state --source active --field release_env)"
  active_release_dir="$(state --source active --field release_dir)"
  case "$active_color" in
    blue) target_color=green ;;
    green) target_color=blue ;;
    *) die "invalid active color: $active_color" ;;
  esac
else
  FIRST_RELEASE=true
  active_color=""
  active_app_env=""
  active_release_dir=""
  target_color=blue
  for path in \
    "$ROOT_DIR/current" \
    "$STATE_DIR/active-app.env" \
    "$STATE_DIR/active-release-images.env" \
    "$STATE_DIR/active-color"; do
    [[ ! -e "$path" && ! -L "$path" ]] \
      || die "fresh installation refused pre-existing runtime state: $path"
  done
  if systemctl is-active --quiet fb-agent.service; then
    die "fresh installation refused a running unsupported application service"
  fi
  for project in \
    fb_agent \
    fb_agent_blue \
    fb_agent_green \
    fb_agent_desktop \
    fb_agent_vision; do
    if docker ps -a --filter "label=com.docker.compose.project=$project" \
      --format '{{.ID}}' | grep -q .; then
      die "fresh installation refused incumbent Compose project: $project"
    fi
  done
  if [[ -e "$BOOTSTRAP_STATE" || -L "$BOOTSTRAP_STATE" ]]; then
    python3 "$SCRIPT_DIR/bootstrap-state.py" validate-owned \
      --state "$BOOTSTRAP_STATE" \
      --cluster-id "$bootstrap_cluster_id" \
      --postgres-volume "$POSTGRES_VOLUME" \
      --platform-network "$PLATFORM_NETWORK" >/dev/null \
      || die "fresh installation found bootstrap state owned by another cluster"
    log "resuming the exact owned first-cluster bootstrap transaction"
  fi
  if docker ps -a --filter name='^/vision-webtop$' --format '{{.ID}}' | grep -q .; then
    die "fresh installation refused an incumbent Vision desktop container"
  fi
  [[ "$VISION_WEBTOP_ROOT_DIR" = /* && "$VISION_WEBTOP_ROOT_DIR" != *".."* ]] \
    || die "VISION_WEBTOP_ROOT must be a safe absolute path"
  for path in \
    "$VISION_WEBTOP_ROOT_DIR/config" \
    "$VISION_WEBTOP_ROOT_DIR/.production-manifest.sha256"; do
    [[ ! -e "$path" && ! -L "$path" ]] \
      || die "fresh installation refused incumbent desktop state: $path"
  done
  "$SCRIPT_DIR/install-vision-webtop.sh" \
    --profile-seed-dir "$DESKTOP_PROFILE_SEED_DIR" \
    --validate-profile-seed-only
fi

# The stable, boot-safe reconciler is installed before the journal, candidate
# containers, singleton leases or public route can be mutated. A reboot at any
# subsequent failpoint therefore converges the durable transaction.
FB_AGENT_ROOT="$ROOT_DIR" "$SCRIPT_DIR/install-release-reconciler.sh"

CANDIDATE_STATE="$(python3 "$SCRIPT_DIR/release-state.py" prepare \
  --state-root "$STATE_DIR" \
  --release-root "$RELEASE_ROOT" \
  --release-dir "$RELEASE_DIR" \
  --app-env "$APP_ENV" \
  --release-env "$RELEASE_ENV" \
  --release-id "$release_id" \
  --color "$target_color")"
python3 "$SCRIPT_DIR/release-state.py" begin \
  --state-root "$STATE_DIR" --candidate-state "$CANDIDATE_STATE" >/dev/null
candidate_app_env="$(state --source candidate --field app_env)"
candidate_release_env="$(state --source candidate --field release_env)"
candidate_desktop_fingerprint="$(
  python3 "$SCRIPT_DIR/release-state.py" desktop-digest \
    --release-dir "$RELEASE_DIR" \
    --app-env "$candidate_app_env" \
    --release-env "$candidate_release_env"
)"
[[ "$candidate_desktop_fingerprint" =~ ^[0-9a-f]{64}$ ]] \
  || die "candidate desktop fingerprint is invalid"
EXPECTED_DESKTOP_CANDIDATE_STATE="$STATE_DIR/desktop-states/${release_id}-${candidate_desktop_fingerprint:0:16}"
if [[ -e "$STATE_DIR/active-desktop-state" \
  || -L "$STATE_DIR/active-desktop-state" ]]; then
  [[ "$FIRST_RELEASE" == false && -L "$STATE_DIR/active-desktop-state" ]] \
    || die "fresh installation refused an active desktop state"
  PREVIOUS_DESKTOP_STATE="$(readlink -f "$STATE_DIR/active-desktop-state")" \
    || die "active desktop state does not resolve"
  [[ -d "$PREVIOUS_DESKTOP_STATE" \
    && "$(dirname -- "$PREVIOUS_DESKTOP_STATE")" == \
      "$STATE_DIR/desktop-states" ]] \
    || die "active desktop state is outside the immutable state root"
  python3 "$SCRIPT_DIR/release-state.py" desktop-verify \
    --state-root "$STATE_DIR" \
    --state-dir "$PREVIOUS_DESKTOP_STATE" >/dev/null
elif [[ "$FIRST_RELEASE" == false ]]; then
  die "existing application release has no committed desktop state"
fi
[[ "$("$SCRIPT_DIR/platform-desktop-transaction.sh" status)" == none ]] \
  || die "a previous desktop transaction remains before application cutover"
ROLLBACK_ARMED=true
trap 'rollback_parent_release $?' ERR

# Install and validate route templates before any release mutation. Normal
# releases preserve committed credentials; a clean host has no public runtime
# and safely seeds the blue-only template with candidate credentials.
route_app_env="$active_app_env"
[[ "$FIRST_RELEASE" == false ]] || route_app_env="$candidate_app_env"
APP_ENV_OVERRIDE="$route_app_env" "$SCRIPT_DIR/install-server-units.sh" \
  --caddy-only --sync-scope none

# Vision/Kasm/browser-agent is independently versioned, but a semantic contract
# change is committed only after the matching application release. Runtime
# inventory is still checked before any candidate is allowed near cutover.
if [[ "$FIRST_RELEASE" == false ]]; then
  # A killed preflight may leave only the explicitly isolated telemetry
  # candidate. Remove that exact project, then reject every other unknown
  # endpoint or protected-alias owner before the independent desktop mutates.
  FB_AGENT_ROOT="$ROOT_DIR" FB_AGENT_RELEASE_DIR="$RELEASE_DIR" \
    "$SCRIPT_DIR/platform-alloy-agent.sh" candidate-cleanup
  python3 "$SCRIPT_DIR/platform-network-inventory.py" \
    --cluster-id "$bootstrap_cluster_id" \
    --network "$PLATFORM_NETWORK" \
    --phase runtime
fi

if [[ "$FIRST_RELEASE" == true ]]; then
  "$SCRIPT_DIR/platform-bootstrap.sh" \
    --release-env "$candidate_release_env" \
    --app-env "$candidate_app_env" \
    --backup-env "$BACKUP_ENV" \
    --require-empty
else
  export APP_ENV_FILE="$candidate_app_env" BACKUP_ENV_FILE="$BACKUP_ENV"
  running_services="$(docker compose -p fb_agent_infra --env-file "$candidate_release_env" \
    -f "$RELEASE_DIR/deploy/compose/docker-compose.infra.yml" \
    ps --status running --services)"
  grep -qx postgres <<<"$running_services" || die "durable PostgreSQL is not running"
  if ! grep -qx redis <<<"$running_services"; then
    log "WARNING: optional Redis is not running; release continues in degraded mode"
  fi
  if ! FB_AGENT_ROOT="$ROOT_DIR" \
    "$SCRIPT_DIR/install-platform-units.sh" \
      --release-env "$active_release_env" --verify-only; then
    # A process failure immediately after the first app commit can leave timer
    # installation incomplete. Prove a new full backup, WAL replay and isolated
    # restore of the current active release, then resume the idempotent
    # installer. This remains safe even when the original adoption evidence is
    # stale or the retry uses a newer candidate RELEASE_ID.
    active_release_id="$(state --source active --field release_id)"
    recovery_token="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
    recovery_root="$STATE_DIR/backup-evidence/timer-recovery"
    recovery_evidence="$recovery_root/${active_release_id}-${recovery_token}"
    "$SCRIPT_DIR/release-backup-gate.sh" \
      --release-env "$active_release_env" \
      --app-env "$active_app_env" \
      --backup-env "$BACKUP_ENV" \
      --config-file "$PGBACKREST_CONFIG" \
      --evidence-root "$recovery_root" \
      --accepted-dir "$recovery_evidence"
    FB_AGENT_ROOT="$ROOT_DIR" "$SCRIPT_DIR/install-platform-units.sh" \
      --release-env "$active_release_env" \
      --full-evidence "$recovery_evidence/full.json" \
      --restore-evidence "$recovery_evidence/restore.json" \
      --expected-release-id "$active_release_id"
  fi
fi

if [[ "$FIRST_RELEASE" == true ]]; then
  # Bootstrap created the network after the pre-desktop branch above. Apply
  # the same stable inventory gate before starting any telemetry candidate.
  FB_AGENT_ROOT="$ROOT_DIR" FB_AGENT_RELEASE_DIR="$RELEASE_DIR" \
    "$SCRIPT_DIR/platform-alloy-agent.sh" candidate-cleanup
  python3 "$SCRIPT_DIR/platform-network-inventory.py" \
    --cluster-id "$bootstrap_cluster_id" \
    --network "$PLATFORM_NETWORK" \
    --phase runtime
fi

# Observability is an acceptance dependency, not a post-commit best effort.
# The preflight uses a distinct Compose project, port, network alias and data
# volume. It cannot replace or mutate the canonical incumbent before commit.
FB_AGENT_ROOT="$ROOT_DIR" FB_AGENT_RELEASE_DIR="$RELEASE_DIR" \
  "$SCRIPT_DIR/platform-alloy-agent.sh" candidate-up
ALLOY_CANDIDATE_STARTED=true

# Hold one renewable PostgreSQL fence across app handoff and browser readiness.
# New v5 workers may start, but they cannot claim browser-backed tasks until
# the matching v5 desktop is committed. The desktop child adopts this owner;
# only this parent releases it after both sides are exact and ready.
[[ -z "${FB_AGENT_BROWSER_MAINTENANCE_OWNER:-}" ]] \
  || die "platform release must acquire its own browser maintenance owner"
browser_maintenance_enter \
  || die "coordinated browser maintenance fence could not be acquired"
browser_maintenance_checkpoint \
  || die "browser work did not quiesce before coordinated app/browser cutover"
desktop_cutover_snapshot_is_current \
  || die "desktop pointer or transaction changed before coordinated cutover"

# Pull every immutable desktop image and prove that N-1 can still be restored
# while the incumbent application/browser pair is active. Registry latency and
# a missing old probe/parser therefore fail before any public route changes.
run_supervised_child env FB_AGENT_ROOT="$ROOT_DIR" \
  "$SCRIPT_DIR/platform-desktop-release.sh" \
    --release-env "$candidate_release_env" \
    --app-env "$candidate_app_env" \
    --profile-seed-dir "$DESKTOP_PROFILE_SEED_DIR" \
    --preflight-only
browser_maintenance_checkpoint \
  || die "coordinated browser maintenance fence was lost after desktop preflight"

# The child arms the durable absolute deadline immediately before the route
# switch and owns cutover reconciliation.  The parent never starts a second
# budget after the child has returned a terminal rollback failure.
run_supervised_child "$SCRIPT_DIR/bluegreen-deploy.sh" \
  --color "$target_color" \
  --release-env "$candidate_release_env" \
  --app-env "$candidate_app_env" \
  --backup-env "$BACKUP_ENV" \
  --state-dir "$STATE_DIR" \
  --candidate-state "$CANDIDATE_STATE" \
  --activate
load_cutover_deadline \
  || die "blue/green cutover did not persist its absolute deadline"
export FB_AGENT_BROWSER_MAINTENANCE_DEADLINE_EPOCH="$CUTOVER_DEADLINE_EPOCH"
cutover_remaining_seconds >/dev/null \
  || die "blue/green cutover exhausted its absolute deadline before desktop adoption"

# bluegreen-deploy has committed the active app pointer and handed off workers.
# Keep claims fenced until the candidate browser proves the same semantic
# contract through the active app and the host-owned parser.
require_cutover_reserve 70 "post-app browser fence checkpoint"
browser_maintenance_checkpoint \
  || die "coordinated browser maintenance fence was lost after app handoff"
DESKTOP_CUTOVER_STARTED=true
run_supervised_child env FB_AGENT_ROOT="$ROOT_DIR" \
  "$SCRIPT_DIR/platform-desktop-release.sh" \
    --release-env "$candidate_release_env" \
    --app-env "$candidate_app_env" \
    --profile-seed-dir "$DESKTOP_PROFILE_SEED_DIR" \
    --deadline-epoch "$CUTOVER_DEADLINE_EPOCH"
[[ "$(desktop_release_outcome)" == candidate_final ]] \
  || die "desktop child returned without final durable candidate acceptance"
DESKTOP_CUTOVER_COMPLETED=true
require_cutover_reserve 35 "desktop fence release"
browser_maintenance_checkpoint \
  || die "coordinated browser maintenance fence was lost after desktop readiness"
browser_maintenance_leave \
  || die "coordinated browser maintenance fence could not be released"
unset FB_AGENT_BROWSER_MAINTENANCE_OWNER

# Only a committed application release may replace the canonical telemetry
# project. A failed replacement restores the previous release's agent and
# removes the isolated candidate before returning failure.
alloy_promote_args=(promote)
if [[ -n "$active_release_dir" ]]; then
  alloy_promote_args+=(--previous-release-dir "$active_release_dir")
fi
run_cutover_bounded "alloy_promote" env \
  FB_AGENT_ROOT="$ROOT_DIR" FB_AGENT_RELEASE_DIR="$RELEASE_DIR" \
  "$SCRIPT_DIR/platform-alloy-agent.sh" "${alloy_promote_args[@]}"
ALLOY_CANDIDATE_STARTED=false
run_cutover_bounded "record_alloy_adopted" \
  python3 "$SCRIPT_DIR/release-state.py" stage \
    --state-root "$STATE_DIR" --stage alloy_adopted

if [[ "$FIRST_RELEASE" == true ]]; then
  evidence_dir="$STATE_DIR/backup-evidence/adoption-$release_id"
  run_cutover_bounded "adopt_backup_timers" env \
    FB_AGENT_ROOT="$ROOT_DIR" "$VERIFIED_RELEASE_EXEC" \
    --state app --entrypoint scripts/install-platform-units.sh -- \
    --full-evidence "$evidence_dir/full.json" \
    --restore-evidence "$evidence_dir/restore.json" \
    --expected-release-id "$release_id"
else
  run_cutover_bounded "verify_backup_timers" env \
    FB_AGENT_ROOT="$ROOT_DIR" "$VERIFIED_RELEASE_EXEC" \
      --state app --entrypoint scripts/install-platform-units.sh -- --verify-only
fi
run_cutover_bounded "record_timers_adopted" \
  python3 "$SCRIPT_DIR/release-state.py" stage \
    --state-root "$STATE_DIR" --stage timers_adopted

run_cutover_bounded "adopt_server_units" env \
  APP_ENV_OVERRIDE="$STATE_DIR/active-app.env" "$VERIFIED_RELEASE_EXEC" \
    --state app --entrypoint scripts/install-server-units.sh --
run_cutover_bounded "adopt_alloy_unit" env \
  FB_AGENT_ROOT="$ROOT_DIR" "$VERIFIED_RELEASE_EXEC" \
    --state app --entrypoint scripts/install-alloy-agent-unit.sh --
run_cutover_bounded "record_systemd_adopted" \
  python3 "$SCRIPT_DIR/release-state.py" stage \
    --state-root "$STATE_DIR" --stage systemd_adopted

active_desktop_release_dir="$(readlink -f \
  "$STATE_DIR/active-desktop-state/release")"
[[ -d "$active_desktop_release_dir" \
  && "$(dirname -- "$active_desktop_release_dir")" == "$RELEASE_ROOT" ]] \
  || die "active desktop release directory is invalid"
run_cutover_bounded "record_desktop_adopted" \
  python3 "$SCRIPT_DIR/release-state.py" stage \
    --state-root "$STATE_DIR" --stage desktop_adopted
run_cutover_bounded "complete_platform_release" \
  python3 "$SCRIPT_DIR/release-state.py" complete --state-root "$STATE_DIR"

ROLLBACK_ARMED=false
trap - ERR

active_release_dir="$(state --source active --field release_dir)"
find "$RELEASE_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr | tail -n +6 | cut -d' ' -f2- \
  | while IFS= read -r old_release; do
      [[ -n "$old_release" ]] || continue
      canonical="$(readlink -f "$old_release")"
      [[ "$canonical" == "$old_release" ]] || continue
      [[ "$(dirname -- "$canonical")" == "$RELEASE_ROOT" ]] || continue
      [[ -f "$canonical/.fb-agent-release" && ! -L "$canonical/.fb-agent-release" ]] || continue
      if [[ "$canonical" != "$active_release_dir" \
        && "$canonical" != "$active_desktop_release_dir" ]]; then
        rm -rf -- "$canonical"
      fi
    done
printf 'Platform release %s is active on %s\n' "$release_id" "$target_color"
