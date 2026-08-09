#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=scripts/browser-control-env.sh
source "$SCRIPT_DIR/browser-control-env.sh"
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly STATE_DIR="$ROOT_DIR/shared"
readonly BACKUP_ENV="$STATE_DIR/pgbackrest.env"
readonly PGBACKREST_CONFIG="$STATE_DIR/pgbackrest.conf"
readonly BROWSER_CONTROL_ENV="$STATE_DIR/browser-control.env"
readonly BROWSER_MAINTENANCE_ENV="$STATE_DIR/browser-maintenance.env"
readonly BROWSER_AUTOPAUSE_ENV="$STATE_DIR/browser-autopause.env"
readonly BROWSER_META_API_ENV="$STATE_DIR/browser-meta-api.env"
readonly BROWSER_CAMPAIGN_CREATOR_ENV="$STATE_DIR/browser-campaign-creator.env"
readonly BROWSER_AUTHORITY_ENV="$STATE_DIR/browser-authority.env"
readonly DESKTOP_PROFILE_SEED_DIR="${FB_AGENT_DESKTOP_PROFILE_SEED_DIR:-$STATE_DIR/desktop-profile-seed}"
DEADLINE_SECONDS=180
DEADLINE_EPOCH=""
DEADLINE_EXPLICIT=false
BOOT_MODE=false
INITIAL_FORWARD_RESUME=false
DRY_RUN=false
STARTED_AT="$(date +%s)"
ORIGINAL_CUTOVER_DEADLINE=""
BOOT_RECOVERY_DEADLINE=false
INITIAL_FORWARD_RECOVERY_DEADLINE=false
EXPECTED_RELEASE_DIR=""
EXPECTED_APP_ENV=""
EXPECTED_RELEASE_ENV=""
declare -a FAILURES=()
HOST_METRIC_OPERATION=release_reconcile
HOST_METRIC_STARTED=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[release-reconcile] %s\n' "$*" >&2; }
record_host_metric() {
  local -r operation="$1"
  local -r outcome="$2"
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || return 0
  if ! python3 "$SCRIPT_DIR/host_metrics.py" record \
    --operation "$operation" --outcome "$outcome"; then
    log "CRITICAL failed to persist $operation host metric ($outcome)"
  fi
}
finish() {
  local -r exit_code=$?
  trap - EXIT
  if [[ "$HOST_METRIC_STARTED" == true ]]; then
    if ((exit_code == 0)); then
      record_host_metric "$HOST_METRIC_OPERATION" success
      if [[ -f "$STATE_DIR/rollback-failed.json" ]]; then
        record_host_metric release_rollback success
      fi
    else
      record_host_metric "$HOST_METRIC_OPERATION" failure
    fi
  fi
  exit "$exit_code"
}
trap finish EXIT

while (($#)); do
  case "$1" in
    --deadline-seconds) DEADLINE_SECONDS="${2:?missing deadline}"; shift 2 ;;
    --deadline-epoch)
      DEADLINE_EPOCH="${2:?missing deadline epoch}"
      DEADLINE_EXPLICIT=true
      shift 2
      ;;
    --boot) BOOT_MODE=true; shift ;;
    --resume-initial-forward) INITIAL_FORWARD_RESUME=true; shift ;;
    --expected-release-dir)
      EXPECTED_RELEASE_DIR="${2:?missing expected release directory}"
      shift 2
      ;;
    --expected-app-env)
      EXPECTED_APP_ENV="${2:?missing expected app environment}"
      shift 2
      ;;
    --expected-release-env)
      EXPECTED_RELEASE_ENV="${2:?missing expected release environment}"
      shift 2
      ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ "$BOOT_MODE" != true || "$INITIAL_FORWARD_RESUME" != true ]] \
  || die "boot and explicit initial-forward recovery are mutually exclusive"
[[ "$DRY_RUN" != true || "$INITIAL_FORWARD_RESUME" != true ]] \
  || die "explicit initial-forward recovery cannot run in dry-run mode"
if [[ "$BOOT_MODE" == true ]]; then
  HOST_METRIC_OPERATION=release_boot_reconcile
elif [[ "$INITIAL_FORWARD_RESUME" == true ]]; then
  HOST_METRIC_OPERATION=release_initial_forward_reconcile
fi
if [[ "$DRY_RUN" != true ]]; then
  HOST_METRIC_STARTED=true
  record_host_metric "$HOST_METRIC_OPERATION" started
fi
[[ "$DEADLINE_SECONDS" =~ ^[0-9]+$ ]] || die "deadline must be an integer"
((DEADLINE_SECONDS >= 1 && DEADLINE_SECONDS <= 180)) \
  || die "rollback/reconciliation deadline must be between 1 and 180 seconds"
if [[ -n "$DEADLINE_EPOCH" ]]; then
  [[ "$DEADLINE_EPOCH" =~ ^[0-9]+$ ]] || die "deadline epoch must be an integer"
fi

for command in cmp date docker flock install logger python3 readlink sed systemctl tail timeout; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -d "$STATE_DIR" ]] || die "state directory is missing"
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

LOCK_FILE="$STATE_DIR/deploy.lock"
if [[ -n "${FB_AGENT_DEPLOY_LOCK_FD:-}" ]]; then
  [[ "$FB_AGENT_DEPLOY_LOCK_FD" =~ ^[0-9]+$ ]] \
    || die "inherited deployment lock fd is invalid"
  [[ -e "/proc/$$/fd/$FB_AGENT_DEPLOY_LOCK_FD" ]] \
    || die "inherited deployment lock fd is not open"
  lock_target="$(readlink -f "/proc/$$/fd/$FB_AGENT_DEPLOY_LOCK_FD")"
  [[ "$lock_target" == "$LOCK_FILE" ]] \
    || die "inherited deployment lock does not guard $LOCK_FILE"
  flock -n "$FB_AGENT_DEPLOY_LOCK_FD" \
    || die "inherited deployment lock is not held"
else
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "another deployment or reconciliation is already running"
  export FB_AGENT_DEPLOY_LOCK_FD=9
fi

state() {
  python3 "$SCRIPT_DIR/release-state.py" get --state-root "$STATE_DIR" "$@"
}

start_deadline_breach_recovery() {
  local -r now_epoch="$1"
  local -r original_deadline="$2"

  if [[ "$BOOT_MODE" == true ]]; then
    BOOT_RECOVERY_DEADLINE=true
  elif [[ "$INITIAL_FORWARD_RESUME" == true ]]; then
    INITIAL_FORWARD_RECOVERY_DEADLINE=true
  else
    return 1
  fi
  ORIGINAL_CUTOVER_DEADLINE="$original_deadline"
  DEADLINE_EPOCH=$((now_epoch + DEADLINE_SECONDS))
  python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
    --state-root "$STATE_DIR" \
    --failure "cutover_deadline_breached:original_${ORIGINAL_CUTOVER_DEADLINE}"
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
    "CRITICAL persisted cutover deadline ${ORIGINAL_CUTOVER_DEADLINE} expired before bounded recovery"
  log "persisted cutover deadline breached; bounded recovery ends at $DEADLINE_EPOCH"
}

stage_rank() {
  case "$1" in
    prepared) printf '0\n' ;;
    candidate_started) printf '1\n' ;;
    route_switched) printf '2\n' ;;
    workers_handed_off) printf '3\n' ;;
    accepted) printf '4\n' ;;
    committed) printf '5\n' ;;
    alloy_adopted) printf '6\n' ;;
    timers_adopted) printf '7\n' ;;
    systemd_adopted) printf '8\n' ;;
    desktop_adopted) printf '9\n' ;;
    completed) printf '10\n' ;;
    *) return 1 ;;
  esac
}

stage_is_before() {
  local -r current="$1"
  local -r target="$2"
  (( $(stage_rank "$current") < $(stage_rank "$target") ))
}

cluster_id_from_env() {
  local -r app_env="$1"
  sed -n 's/^FB_AGENT_BOOTSTRAP_CLUSTER_ID=//p' "$app_env" | tail -n 1
}

remaining_seconds() {
  local -r now="$(date +%s)"
  local -r remaining=$((DEADLINE_EPOCH - now))
  ((remaining > 0)) || return 1
  printf '%s\n' "$remaining"
}

run_bounded() {
  local -r label="$1"
  shift
  local remaining=""
  if ! remaining="$(remaining_seconds)"; then
    FAILURES+=("${label}:deadline_exhausted")
    return 1
  fi
  if [[ "$DRY_RUN" == true ]]; then
    log "would run within ${remaining}s: $label"
    return 0
  fi
  if timeout --signal=KILL "${remaining}s" "$@"; then
    return 0
  else
    local -r status=$?
    FAILURES+=("${label}:exit_${status}")
    log "step failed: $label (exit $status)"
    return 1
  fi
}

ports_for_color() {
  case "$1" in
    blue) APP_API_PORT=18100; APP_WEB_PORT=18080; APP_TMA_PORT=18081 ;;
    green) APP_API_PORT=28100; APP_WEB_PORT=28080; APP_TMA_PORT=28081 ;;
    *) die "invalid blue/green color: $1" ;;
  esac
  export APP_API_PORT APP_WEB_PORT APP_TMA_PORT
}

restore_committed_webhook() {
  local -r executor_color="$active_color"
  local -r executor_release_env="$active_release_env"
  local -r executor_release_dir="$active_release_dir"
  local executor_release_id=""
  executor_release_id="$(state --source "$active_source" --field release_id)"
  [[ "$executor_color" == blue || "$executor_color" == green ]] || {
    FAILURES+=("restore_committed_telegram_webhook:invalid_executor_color")
    return 1
  }
  ports_for_color "$executor_color"
  export APP_COLOR="$executor_color"
  export APP_ENV_FILE="$active_app_env"
  export BACKUP_ENV_FILE="$BACKUP_ENV"
  export PGBACKREST_CONFIG_FILE="$PGBACKREST_CONFIG"
  export RELEASE_ID="$executor_release_id"
  export FB_AGENT_BOOTSTRAP_CLUSTER_ID
  FB_AGENT_BOOTSTRAP_CLUSTER_ID="$(cluster_id_from_env "$active_app_env")"
  [[ "$FB_AGENT_BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] || {
    FAILURES+=("restore_committed_telegram_webhook:invalid_cluster_id")
    return 1
  }
  run_bounded "restore_committed_telegram_webhook" \
    docker compose -p "fb_agent_${executor_color}" \
      --env-file "$executor_release_env" \
      -f "$executor_release_dir/deploy/compose/docker-compose.app.yml" \
      --profile release run --rm telegram_webhook_configurator
}

stop_state_services() {
  local -r source="$1"
  local color=""
  color="$(state --source "$source" --field color)"
  [[ "$color" == blue || "$color" == green ]] || return 0
  local release_env="" app_env="" release_dir="" release_id=""
  release_env="$(state --source "$source" --field release_env)"
  app_env="$(state --source "$source" --field app_env)"
  release_dir="$(state --source "$source" --field release_dir)"
  release_id="$(state --source "$source" --field release_id)"
  if ! run_bounded "verify_${source}_release_before_stop" python3 \
    "$SCRIPT_DIR/release-state.py" manifest-verify \
      --release-dir "$release_dir" \
      --manifest "$release_dir/.fb-agent-source-manifest.json" \
      --require-read-only; then
    return 1
  fi
  ports_for_color "$color"
  export APP_COLOR="$color" APP_ENV_FILE="$app_env" BACKUP_ENV_FILE="$BACKUP_ENV" RELEASE_ID="$release_id"
  export FB_AGENT_BOOTSTRAP_CLUSTER_ID
  FB_AGENT_BOOTSTRAP_CLUSTER_ID="$(cluster_id_from_env "$app_env")"
  [[ "$FB_AGENT_BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] || {
    FAILURES+=("stop_uncommitted_${color}:invalid_cluster_id")
    return 1
  }
  export PGBACKREST_CONFIG_FILE="$PGBACKREST_CONFIG"
  local -a services=(
    api frontend mini-app observer autopause_worker meta_api telegram_delivery_worker
    telegram_update_worker cleanup reconciler health_watchdog digest_scheduler
    tracker_reconciliation_worker campaign_creator
  )
  run_bounded "stop_uncommitted_${color}" \
    docker compose -p "fb_agent_${color}" --env-file "$release_env" \
      -f "$release_dir/deploy/compose/docker-compose.app.yml" --profile workers \
      stop --timeout 60 "${services[@]}"
}

advance_adoption_stage() {
  local -r target_stage="$1"
  if run_bounded "record_${target_stage}" python3 \
    "$SCRIPT_DIR/release-state.py" stage \
      --state-root "$STATE_DIR" --stage "$target_stage"; then
    journal_stage="$target_stage"
    return 0
  fi
  return 1
}

resume_post_commit_adoption() {
  local previous_release_dir=""
  local evidence_dir=""
  local -a alloy_args=(promote)
  local -a timer_args=()

  if state --source previous --field state_dir >/dev/null 2>&1; then
    previous_release_dir="$(state --source previous --field release_dir)"
    alloy_args+=(--previous-release-dir "$previous_release_dir")
  fi

  export FB_AGENT_BOOTSTRAP_CLUSTER_ID
  FB_AGENT_BOOTSTRAP_CLUSTER_ID="$(cluster_id_from_env "$active_app_env")"
  [[ "$FB_AGENT_BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] || {
    FAILURES+=("post_commit_adoption:invalid_cluster_id")
    return 1
  }

  if stage_is_before "$journal_stage" alloy_adopted; then
    if run_bounded "adopt_alloy" env \
      FB_AGENT_ROOT="$ROOT_DIR" \
      FB_AGENT_RELEASE_DIR="$active_release_dir" \
      FB_AGENT_BOOTSTRAP_CLUSTER_ID="$FB_AGENT_BOOTSTRAP_CLUSTER_ID" \
      "$active_release_dir/scripts/platform-alloy-agent.sh" "${alloy_args[@]}" \
      && advance_adoption_stage alloy_adopted; then
      :
    else
      return 1
    fi
  fi

  if stage_is_before "$journal_stage" timers_adopted; then
    if [[ -n "$previous_release_dir" ]]; then
      timer_args=(--verify-only)
    else
      evidence_dir="$STATE_DIR/backup-evidence/adoption-$active_release_id"
      timer_args=(
        --full-evidence "$evidence_dir/full.json"
        --restore-evidence "$evidence_dir/restore.json"
        --expected-release-id "$active_release_id"
      )
    fi
    if run_bounded "adopt_backup_timers" env FB_AGENT_ROOT="$ROOT_DIR" \
      "$active_release_dir/scripts/install-platform-units.sh" \
      --release-env "$active_release_env" "${timer_args[@]}" \
      && advance_adoption_stage timers_adopted; then
      :
    else
      return 1
    fi
  fi

  if stage_is_before "$journal_stage" systemd_adopted; then
    if run_bounded "adopt_server_units" env \
      APP_ENV_OVERRIDE="$active_app_env" \
      "$active_release_dir/scripts/install-server-units.sh" \
      && run_bounded "adopt_alloy_unit" env FB_AGENT_ROOT="$ROOT_DIR" \
        "$active_release_dir/scripts/install-alloy-agent-unit.sh" \
      && advance_adoption_stage systemd_adopted; then
      :
    else
      return 1
    fi
  fi

  if stage_is_before "$journal_stage" desktop_adopted; then
    if run_bounded "adopt_desktop_release" env FB_AGENT_ROOT="$ROOT_DIR" \
      "$active_release_dir/scripts/platform-desktop-release.sh" \
        --release-env "$active_release_env" \
        --app-env "$active_app_env" \
        --profile-seed-dir "$DESKTOP_PROFILE_SEED_DIR" \
        --deadline-epoch "$DEADLINE_EPOCH" \
      && advance_adoption_stage desktop_adopted; then
      :
    else
      return 1
    fi
  fi

  run_bounded "complete_selected_release" python3 \
    "$SCRIPT_DIR/release-state.py" complete --state-root "$STATE_DIR"
}

journal_present=false
journal_stage=""
if [[ -f "$STATE_DIR/release-transaction.json" ]]; then
  journal_present=true
  journal_stage="$(state --source journal --field stage)"
fi
if [[ "$INITIAL_FORWARD_RESUME" == true ]]; then
  [[ "$journal_present" == true \
    && -n "$EXPECTED_RELEASE_DIR" \
    && -n "$EXPECTED_APP_ENV" \
    && -n "$EXPECTED_RELEASE_ENV" ]] \
    || die "initial-forward recovery requires its journal and exact expected release inputs"
  [[ "$journal_stage" == committed \
    || "$journal_stage" == alloy_adopted \
    || "$journal_stage" == timers_adopted \
    || "$journal_stage" == systemd_adopted ]] \
    || die "initial-forward recovery requires a pre-desktop committed stage"
  [[ "$(state --source journal --field recovery_policy)" == initial_forward_only ]] \
    || die "initial-forward recovery requires the durable forward-only policy"
  [[ "$(state --source active --field state_dir)" == \
    "$(state --source candidate --field state_dir)" ]] \
    || die "initial-forward recovery requires the exact active candidate"
  if state --source previous --field state_dir >/dev/null 2>&1; then
    die "initial-forward recovery refuses a previous application state"
  fi
  if state --source journal --field rollback_requested_at >/dev/null 2>&1; then
    die "initial-forward recovery refuses rollback intent"
  fi
  active_resume_release_dir="$(state --source active --field release_dir)"
  active_resume_app_env="$(state --source active --field app_env)"
  active_resume_release_env="$(state --source active --field release_env)"
  [[ -f "$EXPECTED_APP_ENV" && ! -L "$EXPECTED_APP_ENV" \
    && -f "$EXPECTED_RELEASE_ENV" && ! -L "$EXPECTED_RELEASE_ENV" \
    && "$active_resume_release_dir" == "$EXPECTED_RELEASE_DIR" \
    && "$(readlink -f "$EXPECTED_RELEASE_DIR")" == "$EXPECTED_RELEASE_DIR" ]] \
    || die "initial-forward recovery release identity changed"
  cmp -s -- "$active_resume_app_env" "$EXPECTED_APP_ENV" \
    || die "initial-forward recovery app configuration changed"
  cmp -s -- "$active_resume_release_env" "$EXPECTED_RELEASE_ENV" \
    || die "initial-forward recovery image manifest changed"
  [[ ! -e "$STATE_DIR/active-desktop-state" \
    && ! -L "$STATE_DIR/active-desktop-state" ]] \
    || die "initial-forward recovery requires an absent desktop pointer"
  [[ "$("$SCRIPT_DIR/platform-desktop-transaction.sh" status)" == none ]] \
    || die "initial-forward recovery requires no desktop transaction"
fi
if [[ "$BOOT_MODE" == true && "$journal_present" == false \
  && ! -e "$STATE_DIR/active-state" && ! -L "$STATE_DIR/active-state" ]]; then
  log "no release transaction or committed release exists; boot reconciliation is a clean no-op"
  exit 0
fi
now_epoch="$(date +%s)"
JOURNAL_CUTOVER_DEADLINE=""
if [[ "$journal_present" == true ]]; then
  JOURNAL_CUTOVER_DEADLINE="$(
    state --source journal --field cutover_deadline_epoch 2>/dev/null || true
  )"
  if [[ -n "$JOURNAL_CUTOVER_DEADLINE" ]]; then
    [[ "$JOURNAL_CUTOVER_DEADLINE" =~ ^[0-9]+$ ]] \
      || die "invalid immutable cutover deadline in release journal"
    if [[ "$DEADLINE_EXPLICIT" == true \
      && "$DEADLINE_EPOCH" != "$JOURNAL_CUTOVER_DEADLINE" ]]; then
      die "explicit deadline does not match immutable journal cutover deadline"
    fi
    DEADLINE_EPOCH="$JOURNAL_CUTOVER_DEADLINE"
  fi
fi
if [[ -z "$DEADLINE_EPOCH" ]]; then
  DEADLINE_EPOCH=$((now_epoch + DEADLINE_SECONDS))
fi
[[ "$DEADLINE_EPOCH" =~ ^[0-9]+$ ]] || die "invalid absolute deadline in release journal"
if ((DEADLINE_EPOCH <= now_epoch)); then
  if [[ "$BOOT_MODE" != true \
    && "$INITIAL_FORWARD_RESUME" != true \
    || "$DEADLINE_EXPLICIT" == true \
    || -z "$JOURNAL_CUTOVER_DEADLINE" ]]; then
    die "absolute reconciliation deadline has expired"
  fi
  # The original cutover deadline remains immutable evidence for SLO
  # accounting. Boot recovery gets one new bounded convergence window so a
  # long power outage cannot leave the host in a permanent zero-action loop.
  start_deadline_breach_recovery "$now_epoch" "$JOURNAL_CUTOVER_DEADLINE" \
    || die "expired deadline recovery mode is not authorized"
fi
((DEADLINE_EPOCH > now_epoch && DEADLINE_EPOCH <= now_epoch + 180)) \
  || die "absolute reconciliation deadline must be live and within 180 seconds"

# The first clean installation persists its forward-only policy before moving
# the pointer. If power is lost between those writes, recovery selects the
# already-healthy blue candidate before touching Caddy, Telegram or workers.
active_source=active
if [[ "$journal_present" == true ]]; then
  recovery_policy="$(state --source journal --field recovery_policy 2>/dev/null || true)"
  if [[ "$recovery_policy" == initial_forward_only ]]; then
    if stage_is_before "$journal_stage" committed; then
      if [[ "$DRY_RUN" == true ]]; then
        active_source=candidate
      elif ! run_bounded "select_forward_candidate" python3 \
        "$SCRIPT_DIR/release-state.py" select-initial --state-root "$STATE_DIR"; then
        python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
          --state-root "$STATE_DIR" --failure "select_forward_candidate:failed"
        logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
          "CRITICAL initial forward-only release selection failed"
        exit 70
      fi
    elif [[ "$(state --source active --field state_dir)" != \
      "$(state --source candidate --field state_dir)" ]]; then
      python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
        --state-root "$STATE_DIR" \
        --failure "committed_initial_forward_identity:changed"
      logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
        "CRITICAL committed initial forward release lost candidate identity"
      exit 70
    fi
  elif [[ -z "$recovery_policy" \
    && ! -e "$STATE_DIR/active-state" && ! -L "$STATE_DIR/active-state" ]]; then
    # Before first selection there is no committed runtime to restore. A crash
    # may nevertheless leave the blue candidate attached to the platform
    # network. Retire that exact journal-owned candidate before aborting so a
    # different release ID can resume the owned baseline transaction.
    if stop_state_services candidate \
      && run_bounded "abort_unselected_initial_candidate" \
        python3 "$SCRIPT_DIR/release-state.py" abort --state-root "$STATE_DIR"; then
      log "unselected initial candidate retired; clean bootstrap may resume"
      exit 0
    fi
    python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
      --state-root "$STATE_DIR" \
      --failure "retire_unselected_initial_candidate:failed"
    logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
      "CRITICAL unselected initial candidate could not be retired"
    exit 70
  fi
fi

rollback_requested_at=""
if [[ "$journal_present" == true ]]; then
  rollback_requested_at="$(
    state --source journal --field rollback_requested_at 2>/dev/null || true
  )"
fi
if [[ -n "$rollback_requested_at" ]]; then
  if ! state --source previous --field state_dir >/dev/null 2>&1; then
    python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
      --state-root "$STATE_DIR" \
      --failure "resume_committed_rollback:previous_state_missing"
    die "committed rollback intent has no previous immutable state"
  fi
  if [[ "$DRY_RUN" == true ]]; then
    active_source=previous
  elif run_bounded "resume_committed_rollback" python3 \
    "$SCRIPT_DIR/release-state.py" rollback-commit --state-root "$STATE_DIR"; then
    journal_stage=accepted
  else
    python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
      --state-root "$STATE_DIR" \
      --failure "resume_committed_rollback:failed"
    logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
      "CRITICAL committed application rollback could not be resumed"
    exit 70
  fi
fi

if [[ "$active_source" == active \
  && ! -e "$STATE_DIR/active-state" \
  && ! -L "$STATE_DIR/active-state" ]]; then
  die "no committed blue/green release exists; an unselected initial candidate cannot be reconciled"
fi

active_path="$(state --source "$active_source" --field state_dir)"
active_color="$(state --source "$active_source" --field color)"
active_app_env="$(state --source "$active_source" --field app_env)"
active_release_env="$(state --source "$active_source" --field release_env)"
active_release_dir="$(state --source "$active_source" --field release_dir)"
active_release_id="$(state --source "$active_source" --field release_id)"

if ! run_bounded "verify_active_release" python3 \
  "$SCRIPT_DIR/release-state.py" manifest-verify \
    --release-dir "$active_release_dir" \
    --manifest "$active_release_dir/.fb-agent-source-manifest.json" \
    --require-read-only; then
  python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
    --state-root "$STATE_DIR" \
    --failure "verify_active_release:failed"
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
    "CRITICAL active release verification failed before release-owned execution"
  die "active release verification failed; no release-owned command was executed"
fi

if [[ "$DRY_RUN" != true ]]; then
  if run_bounded "restore_committed_links" python3 "$SCRIPT_DIR/release-state.py" \
    ensure-links --state-root "$STATE_DIR" --root-dir "$ROOT_DIR"; then :; fi
fi

caddy_args=(
  --color "$active_color"
  --state-dir "$STATE_DIR"
  --app-env "$active_app_env"
)
if [[ "$BOOT_MODE" == true ]]; then
  caddy_args+=(--no-reload)
fi
if run_bounded "restore_committed_caddy" \
  "$SCRIPT_DIR/bluegreen-switch-caddy.sh" "${caddy_args[@]}"; then
  :
fi

# setWebhook is external state and can succeed immediately before the local
# commit fails.  Reassert the active pointer's webhook identity before the
# journal is aborted/completed, under the very same absolute cutover deadline.
if [[ "$journal_present" == true ]]; then
  if restore_committed_webhook; then :; fi
fi

if [[ "$journal_present" == true ]]; then
  candidate_path="$(state --source candidate --field state_dir)"
  previous_color=""
  if state --source previous --field state_dir >/dev/null 2>&1; then
    previous_color="$(state --source previous --field color)"
  fi
  if [[ "$active_path" == "$candidate_path" ]]; then
    other_source=previous
    other_color="$previous_color"
  else
    other_source=candidate
    other_color="$(state --source candidate --field color)"
  fi

  if [[ "$BOOT_MODE" == true ]]; then
    if [[ "$other_color" == blue || "$other_color" == green ]]; then
      if stop_state_services "$other_source"; then :; fi
    fi
  elif [[ "$active_color" == blue || "$active_color" == green ]]; then
    [[ -f "$BACKUP_ENV" ]] || FAILURES+=("worker_handoff:backup_env_missing")
    if [[ "$other_color" == blue || "$other_color" == green ]]; then
      other_release_env="$(state --source "$other_source" --field release_env)"
      handoff=(
        --from-color "$other_color"
        --from-release-env "$other_release_env"
        --to-color "$active_color"
        --to-release-env "$active_release_env"
        --app-env "$active_app_env"
        --backup-env "$BACKUP_ENV"
      )
    else
      handoff=(
        --to-color "$active_color"
        --to-release-env "$active_release_env"
        --app-env "$active_app_env"
        --backup-env "$BACKUP_ENV"
      )
    fi
    if remaining="$(remaining_seconds)"; then
      handoff+=(--deadline-epoch "$DEADLINE_EPOCH")
      if run_bounded "restore_committed_workers" \
        env FB_AGENT_PROJECT_DIR="$active_release_dir" \
        "$SCRIPT_DIR/bluegreen-worker-handoff.sh" "${handoff[@]}"; then :; fi
    else
      FAILURES+=("restore_committed_workers:deadline_exhausted")
    fi
  else
    if [[ "$other_color" == blue || "$other_color" == green ]]; then
      if stop_state_services "$other_source"; then :; fi
    fi
  fi
fi

if [[ "$BOOT_MODE" == true && ( "$active_color" == blue || "$active_color" == green ) ]]; then
  if run_bounded "install_committed_platform_unit" \
    install -m 0644 "$active_release_dir/deploy/systemd/fb-agent.service" \
      /etc/systemd/system/fb-agent.service; then :; fi
  if run_bounded "reload_systemd_units" systemctl daemon-reload; then :; fi
fi

if [[ "$journal_present" == true && "$DRY_RUN" != true \
  && ${#FAILURES[@]} -eq 0 ]]; then
  if [[ "$active_path" == "$candidate_path" ]]; then
    journal_stage="$(state --source journal --field stage)"
    if stage_is_before "$journal_stage" accepted; then
      if run_bounded "accept_forward_selected_release" python3 \
        "$SCRIPT_DIR/release-state.py" stage --state-root "$STATE_DIR" \
        --stage accepted; then
        journal_stage=accepted
      fi
    fi
    if stage_is_before "$journal_stage" committed; then
      if run_bounded "commit_selected_release" python3 \
        "$SCRIPT_DIR/release-state.py" commit --state-root "$STATE_DIR"; then
        journal_stage=committed
      fi
    fi
    if [[ ${#FAILURES[@]} -eq 0 ]] \
      && run_bounded "link_selected_release" python3 "$SCRIPT_DIR/release-state.py" \
        ensure-links --state-root "$STATE_DIR" --root-dir "$ROOT_DIR"; then
      if resume_post_commit_adoption; then :; fi
    fi
  else
    if run_bounded "link_committed_release" python3 "$SCRIPT_DIR/release-state.py" \
      ensure-links --state-root "$STATE_DIR" --root-dir "$ROOT_DIR"; then
      if run_bounded "abort_failed_candidate" python3 "$SCRIPT_DIR/release-state.py" \
        abort --state-root "$STATE_DIR"; then :; fi
    fi
  fi
fi

if ((${#FAILURES[@]} > 0)); then
  marker_args=(mark-rollback-failed --state-root "$STATE_DIR")
  if [[ "$BOOT_RECOVERY_DEADLINE" == true \
    || "$INITIAL_FORWARD_RECOVERY_DEADLINE" == true ]]; then
    marker_args+=(--failure \
      "cutover_deadline_breached:original_${ORIGINAL_CUTOVER_DEADLINE}")
  fi
  for failure in "${FAILURES[@]}"; do
    marker_args+=(--failure "$failure")
  done
  python3 "$SCRIPT_DIR/release-state.py" "${marker_args[@]}"
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
    "CRITICAL release reconciliation failed: ${FAILURES[*]}"
  printf 'ERROR: release reconciliation failed before absolute deadline %s: %s\n' \
    "$DEADLINE_EPOCH" "${FAILURES[*]}" >&2
  exit 70
fi
if [[ "$BOOT_RECOVERY_DEADLINE" == true \
  || "$INITIAL_FORWARD_RECOVERY_DEADLINE" == true ]]; then
  log "committed state converged after original cutover deadline breach $ORIGINAL_CUTOVER_DEADLINE"
fi
log "committed state $active_color reconciled in $(( $(date +%s) - STARTED_AT ))s"
