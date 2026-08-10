#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
DESKTOP_RELEASE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly DESKTOP_RELEASE_DIR
# shellcheck source=scripts/browser-maintenance-lease.sh
source "$SCRIPT_DIR/browser-maintenance-lease.sh"
# shellcheck source=scripts/browser-control-env.sh
source "$SCRIPT_DIR/browser-control-env.sh"
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly STATE_DIR="$ROOT_DIR/shared"
readonly DESKTOP_STATES_DIR="$STATE_DIR/desktop-states"
VERIFIED_RUNTIME=false
if [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
  [[ "${FB_AGENT_VERIFIED_RELEASE_SCHEMA}" == "fb-agent-verified-release-exec/v1" \
    && -n "${FB_AGENT_ACTIVE_STATE_DIR:-}" ]] \
    || { printf 'ERROR: verified desktop state is invalid\n' >&2; exit 1; }
  ACTIVE_STATE="$FB_AGENT_ACTIVE_STATE_DIR"
  VERIFIED_RUNTIME=true
else
  ACTIVE_STATE="$STATE_DIR/active-desktop-state"
fi
readonly ACTIVE_STATE VERIFIED_RUNTIME
readonly COMPOSE_FILE="$DESKTOP_RELEASE_DIR/deploy/compose/docker-compose.desktop-agent.yml"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
  local exit_code=$?
  trap - EXIT
  if ! browser_maintenance_leave; then
    printf 'ERROR: browser maintenance lease could not be released\n' >&2
    if ((exit_code == 0)); then
      exit_code=1
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT
dotenv_value() {
  local -r key="$1"
  sed -n "s/^${key}=//p" "$RELEASE_ENV" | tail -n 1
}

for command in awk docker flock install mktemp python3 readlink rm sed sleep timeout; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
if [[ "$VERIFIED_RUNTIME" == true ]]; then
  [[ -d "$ACTIVE_STATE" && ! -L "$ACTIVE_STATE" \
    && "$(readlink -f "$ACTIVE_STATE")" == "$ACTIVE_STATE" \
    && "$(dirname -- "$ACTIVE_STATE")" == "$DESKTOP_STATES_DIR" ]] \
    || die "verified desktop state directory is unsafe"
  active_state_dir="$ACTIVE_STATE"
else
  [[ -L "$ACTIVE_STATE" ]] || die "active desktop state pointer is missing"
  active_state_dir="$(readlink -f "$ACTIVE_STATE")"
  [[ -d "$active_state_dir" && ! -L "$active_state_dir" \
    && "$(dirname -- "$active_state_dir")" == "$DESKTOP_STATES_DIR" ]] \
    || die "active desktop state pointer is unsafe"
fi
readonly active_state_dir
readonly APP_ENV="$active_state_dir/app.env"
readonly RELEASE_ENV="$active_state_dir/release-images.env"
readonly BROWSER_CONTROL_ENV="$STATE_DIR/browser-control.env"
[[ "$(readlink -f "$active_state_dir/release")" == "$DESKTOP_RELEASE_DIR" ]] \
  || die "desktop runtime wrapper does not match the committed release directory"
for file in "$APP_ENV" "$RELEASE_ENV" "$BROWSER_CONTROL_ENV" "$COMPOSE_FILE"; do
  [[ -f "$file" ]] || die "required desktop runtime file is missing: $file"
done
browser_control_env_require "$BROWSER_CONTROL_ENV" \
  || die "browser control environment failed the private-file contract"
command="${1:-status}"
shift || true
mutation=false
case "$command" in
  up|stop|remove|restart|compose) mutation=true ;;
  status|logs|verify) ;;
  *) die "unsupported command: $command" ;;
esac
if [[ "$mutation" == true && "${FB_AGENT_DESKTOP_LOCK_HELD:-0}" != "1" ]]; then
  exec 8>"$STATE_DIR/desktop-release.lock"
  flock -n 8 || die "desktop release or healer already owns the runtime"
fi
if [[ "$mutation" == true ]]; then
  browser_maintenance_enter \
    || die "desktop runtime mutation could not acquire the durable browser fence"
fi
RELEASE_ID="$(dotenv_value RELEASE_ID)"
BROWSER_AGENT_IMAGE="$(dotenv_value BROWSER_AGENT_IMAGE)"
FB_AGENT_BOOTSTRAP_CLUSTER_ID="$(
  sed -n 's/^FB_AGENT_BOOTSTRAP_CLUSTER_ID=//p' "$APP_ENV" | tail -n 1
)"
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die "desktop release id is invalid"
[[ "$BROWSER_AGENT_IMAGE" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
  || die "browser-agent image is not digest-pinned"
[[ "$FB_AGENT_BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] \
  || die "desktop state has no durable bootstrap cluster identity"

export BROWSER_CONTROL_ENV_FILE="$BROWSER_CONTROL_ENV"
BROWSER_AGENT_AM_COLUMNS_QS="$(
  sed -n 's/^BROWSER_AGENT_AM_COLUMNS_QS=//p' "$APP_ENV" | tail -n 1
)"
BROWSER_AUTHORITY_CONSUME_URL="$(
  sed -n 's/^BROWSER_AUTHORITY_CONSUME_URL=//p' "$APP_ENV" | tail -n 1
)"
BROWSER_MAINTENANCE_CONSUME_URL="$(
  sed -n 's/^BROWSER_MAINTENANCE_CONSUME_URL=//p' "$APP_ENV" | tail -n 1
)"
[[ "$BROWSER_AUTHORITY_CONSUME_URL" == \
  "https://app.adpulse.su/api/v1/internal/browser-operations/consume" ]] \
  || die "browser authority consume URL is not the canonical HTTPS endpoint"
[[ "$BROWSER_MAINTENANCE_CONSUME_URL" == \
  "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume" ]] \
  || die "browser maintenance consume URL is not the canonical HTTPS endpoint"
export BROWSER_AGENT_AM_COLUMNS_QS BROWSER_AUTHORITY_CONSUME_URL
export BROWSER_MAINTENANCE_CONSUME_URL
export RELEASE_ID FB_AGENT_BOOTSTRAP_CLUSTER_ID
compose=(docker compose -p fb_agent_desktop --env-file "$RELEASE_ENV" -f "$COMPOSE_FILE")

browser_identity_is_exact() {
  local container_id=""
  local webtop_id=""
  local inspection=""
  webtop_id="$(docker inspect --format '{{.Id}}' vision-webtop 2>/dev/null)" \
    || return 1
  [[ -n "$webtop_id" && "$webtop_id" != *$'\n'* ]] || return 1
  container_id="$("${compose[@]}" ps -q browser-agent)"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || return 1
  inspection="$(docker inspect --format \
    '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.fb-agent.managed"}}|{{index .Config.Labels "com.fb-agent.cluster-id"}}|{{index .Config.Labels "com.fb-agent.purpose"}}|{{index .Config.Labels "com.fb-agent.release"}}|{{.Config.Image}}|{{.HostConfig.NetworkMode}}' \
    "$container_id")" || return 1
  [[ "$inspection" == \
    "true|healthy|fb_agent_desktop|browser-agent|true|${FB_AGENT_BOOTSTRAP_CLUSTER_ID}|vision|${RELEASE_ID}|${BROWSER_AGENT_IMAGE}|container:${webtop_id}" ]]
}

start_browser() {
  "$DESKTOP_RELEASE_DIR/scripts/wait-for-vision-container.sh"
  "${compose[@]}" config --quiet
  while IFS= read -r image; do
    [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] \
      || die "non-immutable desktop image: $image"
  done < <("${compose[@]}" config --images)
  if browser_identity_is_exact; then
    browser_maintenance_assert_held \
      || die "browser maintenance renewal failed during browser verification"
    return 0
  fi
  "${compose[@]}" pull browser-agent
  browser_maintenance_checkpoint \
    || die "browser maintenance fence was lost before browser recreation"
  "${compose[@]}" up -d --force-recreate --wait --wait-timeout 180 browser-agent
  browser_identity_is_exact \
    || die "browser-agent identity or Vision namespace binding does not match the committed desktop release"
  browser_maintenance_assert_held \
    || die "browser maintenance renewal failed during browser start"
}

remove_browser() {
  "${compose[@]}" stop --timeout 90 browser-agent
  browser_maintenance_checkpoint \
    || die "browser maintenance fence was lost during browser stop"
  "${compose[@]}" rm -f browser-agent
  browser_maintenance_assert_held \
    || die "browser maintenance renewal failed during browser removal"
}

case "$command" in
  up)
    start_browser
    ;;
  stop)
    "${compose[@]}" stop --timeout 90 browser-agent
    browser_maintenance_assert_held \
      || die "browser maintenance renewal failed during browser stop"
    ;;
  remove)
    remove_browser
    ;;
  restart)
    remove_browser
    start_browser
    ;;
  status) "${compose[@]}" ps "$@" ;;
  logs) "${compose[@]}" logs --tail="${LOG_TAIL:-200}" "$@" ;;
  verify)
    browser_identity_is_exact \
      || die "browser-agent identity or Vision namespace binding is not exact"
    ;;
  compose)
    "${compose[@]}" "$@"
    browser_maintenance_assert_held \
      || die "browser maintenance renewal failed during Compose mutation"
    ;;
esac
