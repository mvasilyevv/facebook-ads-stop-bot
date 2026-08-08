#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly UNIT_DIR="/etc/systemd/system"
FULL_EVIDENCE=""
RESTORE_EVIDENCE=""
EXPECTED_RELEASE_ID=""
VERIFY_ONLY=false
RELEASE_ENV=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --full-evidence) FULL_EVIDENCE="${2:?missing value}"; shift 2 ;;
    --restore-evidence) RESTORE_EVIDENCE="${2:?missing value}"; shift 2 ;;
    --expected-release-id) EXPECTED_RELEASE_ID="${2:?missing value}"; shift 2 ;;
    --release-env) RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --verify-only) VERIFY_ONLY=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
if [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
  [[ "${FB_AGENT_VERIFIED_RELEASE_SCHEMA}" == \
    "fb-agent-verified-release-exec/v1" \
    && -n "${FB_AGENT_ACTIVE_STATE_DIR:-}" ]] \
    || die "verified application state is invalid"
  RELEASE_ENV="$FB_AGENT_ACTIVE_STATE_DIR/release-images.env"
fi

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
for command in grep install python3 rm stat systemctl; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -n "$RELEASE_ENV" && -f "$RELEASE_ENV" ]] \
  || die "active immutable release manifest is missing"
[[ -f "$ROOT_DIR/shared/pgbackrest.env" && ! -L "$ROOT_DIR/shared/pgbackrest.env" ]] \
  || die "pgBackRest env is missing or symlinked"
[[ -f "$ROOT_DIR/shared/pgbackrest.conf" && ! -L "$ROOT_DIR/shared/pgbackrest.conf" ]] \
  || die "stable pgBackRest config is missing"
[[ "$(stat -Lc '%a' "$ROOT_DIR/shared/pgbackrest.env")" == "600" ]] \
  || die "pgBackRest env must have mode 600"

readonly -a TIMERS=(
  fb-agent-pgbackrest-full.timer
  fb-agent-pgbackrest-diff.timer
  fb-agent-restore-drill.timer
)
verify_timers() {
  local timer=""
  for timer in "${TIMERS[@]}"; do
    systemctl is-enabled --quiet "$timer" \
      || die "required recurring safety timer is not enabled: $timer"
    systemctl is-active --quiet "$timer" \
      || die "required recurring safety timer is not active: $timer"
  done
}
if [[ "$VERIFY_ONLY" == true ]]; then
  "$SCRIPT_DIR/install-host-metrics.sh" --verify-only
  verify_timers
  printf 'Required pgBackRest and restore-drill timers are enabled and active\n'
  exit 0
fi
[[ "$EXPECTED_RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] \
  || die "--expected-release-id is required for initial timer adoption"

# This is the sole adoption gate: both evidence files and their immutable
# checksums must prove a new encrypted off-host full backup and an isolated
# restore of that exact backup set before any recurring timer is enabled.
python3 "$SCRIPT_DIR/backup-adoption-evidence.py" validate-pair \
  --full "$FULL_EVIDENCE" \
  --restore "$RESTORE_EVIDENCE" \
  --expected-release-id "$EXPECTED_RELEASE_ID" \
  --max-age-seconds 14400 \
  --require-pitr-marker
"$SCRIPT_DIR/install-host-metrics.sh"

readonly -a UNITS=(
  fb-agent-host-operation-failed@.service
  fb-agent-pgbackrest-full.service
  fb-agent-pgbackrest-full.timer
  fb-agent-pgbackrest-diff.service
  fb-agent-pgbackrest-diff.timer
  fb-agent-restore-drill.service
  fb-agent-restore-drill.timer
)
for unit in "${UNITS[@]}"; do
  install -m 0644 "$PROJECT_DIR/deploy/systemd/$unit" "$UNIT_DIR/$unit"
done
systemctl daemon-reload

# The old local pg_dump is not a second source of truth after the evidence
# gate. Disable it only after the accepted full+restore pair validates.
if systemctl list-unit-files --no-legend fb-agent-backup.timer 2>/dev/null \
  | grep -q '^fb-agent-backup\.timer'; then
  systemctl disable --now fb-agent-backup.timer
fi
rm -f -- \
  "$UNIT_DIR/fb-agent-backup.timer" \
  "$UNIT_DIR/fb-agent-backup.service"
systemctl daemon-reload
systemctl enable --now "${TIMERS[@]}"
verify_timers
printf 'pgBackRest timers enabled from validated full+restore evidence\n'
