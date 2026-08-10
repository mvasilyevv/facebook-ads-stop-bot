#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
readonly COMPOSE_FILE="$PROJECT_DIR/deploy/compose/docker-compose.infra.yml"
CONFIG_FILE="${PGBACKREST_CONFIG_FILE:-${FB_AGENT_ROOT:-/opt/fb-agent}/shared/pgbackrest.conf}"
RELEASE_ENV=""
APP_ENV=""
BACKUP_ENV=""
COMMAND="info"
EVIDENCE=""
TEMP_INFO=""
HOST_METRIC_OPERATION=""
HOST_METRIC_STARTED=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
record_host_metric() {
  local -r outcome="$1"
  [[ -n "$HOST_METRIC_OPERATION" ]] || return 0
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || return 0
  if ! python3 "$SCRIPT_DIR/host_metrics.py" record \
    --operation "$HOST_METRIC_OPERATION" --outcome "$outcome"; then
    printf 'CRITICAL: failed to persist %s host metric (%s)\n' \
      "$HOST_METRIC_OPERATION" "$outcome" >&2
  fi
}
cleanup() {
  local -r exit_code=$?
  trap - EXIT
  [[ -z "$TEMP_INFO" ]] || rm -f -- "$TEMP_INFO"
  if [[ "$HOST_METRIC_STARTED" == true ]]; then
    if ((exit_code == 0)); then
      record_host_metric success
    else
      record_host_metric failure
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --release-env) RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --app-env) APP_ENV="${2:?missing value}"; shift 2 ;;
    --backup-env) BACKUP_ENV="${2:?missing value}"; shift 2 ;;
    --config-file) CONFIG_FILE="${2:?missing value}"; shift 2 ;;
    --evidence) EVIDENCE="${2:?missing value}"; shift 2 ;;
    stanza-create|check|info|full|diff|expire) COMMAND="$1"; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
if [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
  [[ "${FB_AGENT_VERIFIED_RELEASE_SCHEMA}" == "fb-agent-verified-release-exec/v1" \
    && -n "${FB_AGENT_ACTIVE_STATE_DIR:-}" ]] \
    || die "verified application state is invalid"
  RELEASE_ENV="$FB_AGENT_ACTIVE_STATE_DIR/release-images.env"
  APP_ENV="$FB_AGENT_ACTIVE_STATE_DIR/app.env"
fi
[[ -z "$EVIDENCE" || "$COMMAND" == full ]] \
  || die "--evidence is supported only for a full backup"
case "$COMMAND" in
  full) HOST_METRIC_OPERATION=pgbackrest_full ;;
  diff) HOST_METRIC_OPERATION=pgbackrest_diff ;;
esac
if [[ -n "$HOST_METRIC_OPERATION" ]]; then
  HOST_METRIC_STARTED=true
  record_host_metric started
fi
for file in "$RELEASE_ENV" "$APP_ENV" "$BACKUP_ENV" "$CONFIG_FILE"; do
  [[ -f "$file" ]] || die "required file is missing: $file"
done
export APP_ENV_FILE="$APP_ENV"
export BACKUP_ENV_FILE="$BACKUP_ENV"
export PGBACKREST_CONFIG_FILE="$CONFIG_FILE"
compose=(docker compose -p "${INFRA_PROJECT_NAME:-fb_agent_infra}" \
  --env-file "$RELEASE_ENV" -f "$COMPOSE_FILE")

case "$COMMAND" in
  stanza-create) args=(stanza-create) ;;
  check) args=(check) ;;
  info) args=(info) ;;
  full) args=(backup --type=full) ;;
  diff) args=(backup --type=diff) ;;
  expire) args=(expire) ;;
esac

if [[ "$COMMAND" != full || -z "$EVIDENCE" ]]; then
  if [[ -n "$HOST_METRIC_OPERATION" ]]; then
    "${compose[@]}" exec -T --user postgres postgres \
      pgbackrest --stanza=fb-agent "${args[@]}"
    exit 0
  fi
  exec "${compose[@]}" exec -T --user postgres postgres \
    pgbackrest --stanza=fb-agent "${args[@]}"
fi

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
"${compose[@]}" exec -T --user postgres postgres \
  pgbackrest --stanza=fb-agent "${args[@]}"
TEMP_INFO="$(mktemp)"
"${compose[@]}" exec -T --user postgres postgres \
  pgbackrest --stanza=fb-agent --output=json info >"$TEMP_INFO"
completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
release_id="$(sed -n 's/^RELEASE_ID=//p' "$RELEASE_ENV" | tail -n 1)"
[[ "$release_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "release manifest has invalid RELEASE_ID"
python3 "$SCRIPT_DIR/backup-adoption-evidence.py" write-full \
  --output "$EVIDENCE" \
  --info "$TEMP_INFO" \
  --config "$CONFIG_FILE" \
  --backup-env "$BACKUP_ENV" \
  --release-id "$release_id" \
  --started-at "$started_at" \
  --completed-at "$completed_at"
printf 'Immutable full-backup evidence written: %s\n' "$EVIDENCE"
