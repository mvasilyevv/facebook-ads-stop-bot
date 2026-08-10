#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
# shellcheck source=scripts/browser-maintenance-lease.sh
source "$SCRIPT_DIR/browser-maintenance-lease.sh"
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly STATE_DIR="$ROOT_DIR/shared"
readonly STATES_DIR="$STATE_DIR/desktop-states"
readonly ACTIVE_STATE="$STATE_DIR/active-desktop-state"
readonly READINESS_DIR="$STATE_DIR/desktop-readiness"
readonly READINESS_STATES_DIR="$READINESS_DIR/states"
readonly ACTIVE_READINESS="$READINESS_DIR/active.env"
readonly JOURNAL="$STATE_DIR/desktop-transaction.env"

COMMAND=""
CANDIDATE_STATE=""
PREVIOUS_STATE=""
EXPECT=""
JOURNAL_CANDIDATE=""
JOURNAL_PREVIOUS=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

dotenv_value() {
  local -r file="$1"
  local -r key="$2"
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

atomic_relative_symlink() {
  local -r target="$1"
  local -r destination="$2"
  local temporary=""
  temporary="${destination}.new.$$"
  [[ ! -e "$temporary" && ! -L "$temporary" ]] \
    || die "temporary desktop pointer already exists: $temporary"
  ln -s "$target" "$temporary"
  mv -Tf -- "$temporary" "$destination"
  sync -f "$(dirname -- "$destination")"
}

validate_state_name() {
  [[ "$1" =~ ^[A-Za-z0-9._-]{1,160}$ ]] \
    || die "desktop transaction state name is invalid"
}

validate_state() {
  local -r state_name="$1"
  local -r state_dir="$STATES_DIR/$state_name"
  local release_dir=""
  validate_state_name "$state_name"
  [[ -d "$state_dir" && ! -L "$state_dir" \
    && "$(stat -Lc '%a:%u:%g' "$state_dir")" == "700:0:0" ]] \
    || die "desktop transaction state is not a root-owned mode-700 directory"
  for file in app.env release-images.env fingerprint; do
    [[ -f "$state_dir/$file" && ! -L "$state_dir/$file" \
      && "$(stat -Lc '%a:%u:%g' "$state_dir/$file")" == "600:0:0" ]] \
      || die "desktop transaction state file is missing or unsafe: $state_dir/$file"
  done
  [[ "$(<"$state_dir/fingerprint")" =~ ^[0-9a-f]{64}$ ]] \
    || die "desktop transaction fingerprint is invalid"
  [[ -L "$state_dir/release" ]] || die "desktop transaction release pointer is missing"
  release_dir="$(readlink -f "$state_dir/release")"
  [[ -d "$release_dir" && ! -L "$release_dir" \
    && "$(dirname -- "$release_dir")" == "$ROOT_DIR/releases" ]] \
    || die "desktop transaction release is outside the immutable release root"
  [[ -f "$release_dir/.fb-agent-source-manifest.json" \
    && ! -L "$release_dir/.fb-agent-source-manifest.json" ]] \
    || die "desktop transaction release has no immutable source manifest"
  [[ -f "$READINESS_STATES_DIR/$state_name.env" \
    && ! -L "$READINESS_STATES_DIR/$state_name.env" \
    && "$(stat -Lc '%a:%u:%g' "$READINESS_STATES_DIR/$state_name.env")" \
      == "600:0:0" ]] \
    || die "desktop transaction readiness credentials are missing or unsafe"
  python3 "$SCRIPT_DIR/release-state.py" desktop-verify \
    --state-root "$STATE_DIR" \
    --state-dir "$state_dir" >/dev/null \
    || die "desktop transaction state cryptographic contract is invalid"
}

state_name_from_path() {
  local -r state_path="$1"
  local canonical=""
  [[ -n "$state_path" ]] || return 0
  canonical="$(readlink -f "$state_path")"
  [[ "$canonical" == "$STATES_DIR/"* \
    && "$(dirname -- "$canonical")" == "$STATES_DIR" ]] \
    || die "desktop transaction state path is outside $STATES_DIR"
  printf '%s\n' "${canonical##*/}"
}

read_journal() {
  [[ -f "$JOURNAL" && ! -L "$JOURNAL" \
    && "$(stat -Lc '%a:%u:%g' "$JOURNAL")" == "600:0:0" ]] \
    || die "desktop transaction journal is missing or unsafe"
  [[ "$(dotenv_value "$JOURNAL" schema)" == "fb-agent-desktop-transaction-v1" ]] \
    || die "desktop transaction journal schema is unsupported"
  JOURNAL_CANDIDATE="$(dotenv_value "$JOURNAL" candidate_state)"
  JOURNAL_PREVIOUS="$(dotenv_value "$JOURNAL" previous_state)"
  validate_state "$JOURNAL_CANDIDATE"
  if [[ -n "$JOURNAL_PREVIOUS" ]]; then
    validate_state "$JOURNAL_PREVIOUS"
    [[ "$JOURNAL_PREVIOUS" != "$JOURNAL_CANDIDATE" ]] \
      || die "desktop transaction previous and candidate states are identical"
  fi
}

write_journal() {
  local -r candidate="$1"
  local -r previous="$2"
  local temporary=""
  temporary="$(mktemp "${JOURNAL}.new.XXXXXXXX")"
  {
    printf 'schema=fb-agent-desktop-transaction-v1\n'
    printf 'candidate_state=%s\n' "$candidate"
    printf 'previous_state=%s\n' "$previous"
  } >"$temporary"
  chmod 0600 "$temporary"
  chown 0:0 "$temporary"
  sync -f "$temporary"
  mv -Tf -- "$temporary" "$JOURNAL"
  sync -f "$STATE_DIR"
}

active_state_name() {
  local canonical=""
  if [[ ! -e "$ACTIVE_STATE" && ! -L "$ACTIVE_STATE" ]]; then
    return 0
  fi
  [[ -L "$ACTIVE_STATE" ]] || die "active desktop state must be a symlink"
  canonical="$(readlink -f "$ACTIVE_STATE")"
  [[ "$(dirname -- "$canonical")" == "$STATES_DIR" ]] \
    || die "active desktop state points outside $STATES_DIR"
  printf '%s\n' "${canonical##*/}"
}

transaction_outcome() {
  local active=""
  [[ -n "$JOURNAL_CANDIDATE" ]] \
    || die "desktop transaction journal was not loaded"
  active="$(active_state_name)"
  if [[ "$active" == "$JOURNAL_CANDIDATE" ]]; then
    printf 'candidate\n'
  elif [[ -n "$JOURNAL_PREVIOUS" && "$active" == "$JOURNAL_PREVIOUS" ]]; then
    printf 'previous\n'
  elif [[ -z "$JOURNAL_PREVIOUS" && -z "$active" ]]; then
    printf 'absent\n'
  else
    die "active desktop state matches neither side of the durable transaction"
  fi
}

install_units_for_state() {
  local -r state_name="$1"
  local -r state_dir="$STATES_DIR/$state_name"
  local release_dir=""
  local unit=""
  release_dir="$(readlink -f "$state_dir/release")"
  for unit in \
    fb-agent-desktop-agent.service \
    fb-agent-desktop-heal.service \
    fb-agent-desktop-heal.timer; do
    install -m 0644 \
      "$release_dir/deploy/systemd/$unit" "/etc/systemd/system/$unit"
  done
  systemctl daemon-reload
}

reconcile_transaction() {
  local outcome=""
  local target_state=""
  local target_dir=""
  local release_dir=""
  if [[ ! -e "$JOURNAL" && ! -L "$JOURNAL" ]]; then
    printf 'none\n'
    return
  fi
  browser_maintenance_checkpoint \
    || die "browser maintenance lease or quiescence was lost before desktop reconciliation"
  read_journal
  outcome="$(transaction_outcome)"
  case "$outcome" in
    candidate) target_state="$JOURNAL_CANDIDATE" ;;
    previous) target_state="$JOURNAL_PREVIOUS" ;;
    absent)
      rm -f -- "$ACTIVE_READINESS"
      sync -f "$READINESS_DIR"
      printf 'absent\n'
      return
      ;;
    *) die "unsupported desktop transaction outcome: $outcome" ;;
  esac
  target_dir="$STATES_DIR/$target_state"
  release_dir="$(readlink -f "$target_dir/release")"
  atomic_relative_symlink "states/${target_state}.env" "$ACTIVE_READINESS"
  APP_ENV_OVERRIDE="$target_dir/app.env" \
    "$release_dir/scripts/install-server-units.sh" \
    --caddy-only --sync-scope desktop >&2
  browser_maintenance_checkpoint \
    || die "browser maintenance lease or quiescence was lost during Caddy reconciliation"
  install_units_for_state "$target_state"
  browser_maintenance_checkpoint \
    || die "browser maintenance lease or quiescence was lost during unit reconciliation"
  printf '%s\n' "$outcome"
}

complete_transaction() {
  local outcome=""
  local expected_readiness=""
  [[ -e "$JOURNAL" || -L "$JOURNAL" ]] \
    || die "no durable desktop transaction exists"
  browser_maintenance_checkpoint \
    || die "browser maintenance lease or quiescence was lost before transaction completion"
  read_journal
  outcome="$(transaction_outcome)"
  [[ "$outcome" == "$EXPECT" ]] \
    || die "desktop transaction outcome is $outcome, expected $EXPECT"
  if [[ "$outcome" == "candidate" ]]; then
    expected_readiness="states/${JOURNAL_CANDIDATE}.env"
  elif [[ "$outcome" == "previous" ]]; then
    expected_readiness="states/${JOURNAL_PREVIOUS}.env"
  else
    expected_readiness=""
  fi
  if [[ -n "$expected_readiness" ]]; then
    [[ -L "$ACTIVE_READINESS" \
      && "$(readlink "$ACTIVE_READINESS")" == "$expected_readiness" ]] \
      || die "desktop readiness pointer has not converged with active state"
  else
    [[ ! -e "$ACTIVE_READINESS" && ! -L "$ACTIVE_READINESS" ]] \
      || die "fresh desktop rollback left a readiness pointer"
  fi
  rm -f -- "$JOURNAL"
  sync -f "$STATE_DIR"
}

while (($#)); do
  case "$1" in
    prepare|reconcile|complete|status)
      [[ -z "$COMMAND" ]] || die "desktop transaction command was provided twice"
      COMMAND="$1"
      shift
      ;;
    --candidate-state) CANDIDATE_STATE="${2:?missing value}"; shift 2 ;;
    --previous-state) PREVIOUS_STATE="${2:-}"; shift 2 ;;
    --expect) EXPECT="${2:?missing value}"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
for command in \
  awk chmod chown dirname docker install ln mktemp mv python3 readlink rm sed sleep \
  stat sync systemctl timeout; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
if [[ "$COMMAND" != status ]]; then
  browser_maintenance_adopt "${FB_AGENT_BROWSER_MAINTENANCE_OWNER:-}" \
    || die "desktop transaction requires the caller's durable browser maintenance lease"
fi
case "$COMMAND" in
  prepare)
    candidate_name="$(state_name_from_path "$CANDIDATE_STATE")"
    previous_name="$(state_name_from_path "$PREVIOUS_STATE")"
    [[ -n "$candidate_name" ]] || die "--candidate-state is required"
    validate_state "$candidate_name"
    if [[ -n "$previous_name" ]]; then
      validate_state "$previous_name"
    fi
    if [[ -e "$JOURNAL" || -L "$JOURNAL" ]]; then
      read_journal
      [[ "$JOURNAL_CANDIDATE" == "$candidate_name" \
        && "$JOURNAL_PREVIOUS" == "$previous_name" ]] \
        || die "another durable desktop transaction requires reconciliation"
    else
      write_journal "$candidate_name" "$previous_name"
    fi
    ;;
  reconcile) reconcile_transaction ;;
  complete)
    case "$EXPECT" in candidate|previous|absent) ;;
      *) die "--expect must be candidate, previous or absent" ;;
    esac
    complete_transaction
    ;;
  status)
    if [[ -e "$JOURNAL" || -L "$JOURNAL" ]]; then
      read_journal
      transaction_outcome
    else
      printf 'none\n'
    fi
    ;;
  *) die "prepare, reconcile, complete or status command is required" ;;
esac
