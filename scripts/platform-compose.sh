#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly CURRENT_DIR="${FB_AGENT_RELEASE_DIR:-$ROOT_DIR/current}"
# shellcheck source=scripts/browser-control-env.sh
source "$CURRENT_DIR/scripts/browser-control-env.sh"
readonly STATE_DIR="$ROOT_DIR/shared"
readonly DEPLOY_LOCK_FILE="$STATE_DIR/deploy.lock"
readonly BACKUP_ENV="$STATE_DIR/pgbackrest.env"
readonly PGBACKREST_CONFIG="$STATE_DIR/pgbackrest.conf"
readonly BROWSER_CONTROL_ENV="$STATE_DIR/browser-control.env"
readonly BROWSER_MAINTENANCE_ENV="$STATE_DIR/browser-maintenance.env"
readonly BROWSER_AUTOPAUSE_ENV="$STATE_DIR/browser-autopause.env"
readonly BROWSER_META_API_ENV="$STATE_DIR/browser-meta-api.env"
readonly BROWSER_CAMPAIGN_CREATOR_ENV="$STATE_DIR/browser-campaign-creator.env"
readonly BROWSER_AUTHORITY_ENV="$STATE_DIR/browser-authority.env"
if [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
  [[ "${FB_AGENT_VERIFIED_RELEASE_SCHEMA}" == "fb-agent-verified-release-exec/v1" \
    && -n "${FB_AGENT_ACTIVE_STATE_DIR:-}" ]] \
    || { printf 'ERROR: verified application state is invalid\n' >&2; exit 1; }
  readonly APP_STATE_DIR="$FB_AGENT_ACTIVE_STATE_DIR"
else
  readonly APP_STATE_DIR="$STATE_DIR/active-state"
fi
readonly APP_ENV="$APP_STATE_DIR/app.env"
readonly RELEASE_ENV="$APP_STATE_DIR/release-images.env"
readonly COLOR_FILE="$APP_STATE_DIR/color"
readonly APP_COMPOSE="$CURRENT_DIR/deploy/compose/docker-compose.app.yml"
readonly INFRA_COMPOSE="$CURRENT_DIR/deploy/compose/docker-compose.infra.yml"
readonly PLATFORM_NETWORK="fb_agent_safety_first_platform"
readonly NETWORK_INVENTORY="$CURRENT_DIR/scripts/platform-network-inventory.py"

readonly -a WORKERS=(
  observer autopause_worker meta_api telegram_delivery_worker telegram_update_worker
  cleanup reconciler health_watchdog enable_recommendation digest_scheduler
  cabinet_scheduler tracker_reconciliation_worker campaign_creator
)
readonly -a APP_SERVICES=(api frontend mini-app "${WORKERS[@]}")

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

for command in curl docker flock python3 readlink sed seq sleep; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable"
for file in \
  "$APP_ENV" \
  "$BACKUP_ENV" \
  "$BROWSER_CONTROL_ENV" \
  "$BROWSER_MAINTENANCE_ENV" \
  "$BROWSER_AUTOPAUSE_ENV" \
  "$BROWSER_META_API_ENV" \
  "$BROWSER_CAMPAIGN_CREATOR_ENV" \
  "$BROWSER_AUTHORITY_ENV" \
  "$PGBACKREST_CONFIG" \
  "$RELEASE_ENV" \
  "$APP_COMPOSE" \
  "$INFRA_COMPOSE" \
  "$NETWORK_INVENTORY"; do
  [[ -f "$file" ]] || die "required platform file is missing: $file"
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
[[ -f "$COLOR_FILE" ]] || die "active color is missing; complete platform adoption first"
COLOR="$(<"$COLOR_FILE")"
case "$COLOR" in
  blue) APP_API_PORT=18100; APP_WEB_PORT=18080; APP_TMA_PORT=18081 ;;
  green) APP_API_PORT=28100; APP_WEB_PORT=28080; APP_TMA_PORT=28081 ;;
  *) die "invalid active color: $COLOR" ;;
esac
RELEASE_ID="$(sed -n 's/^RELEASE_ID=//p' "$RELEASE_ENV" | tail -n 1)"
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die "active release id is invalid"
BOOTSTRAP_CLUSTER_ID="$(
  sed -n 's/^FB_AGENT_BOOTSTRAP_CLUSTER_ID=//p' "$APP_ENV" | tail -n 1
)"
[[ "$BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] \
  || die "active bootstrap cluster id is invalid"

export APP_COLOR="$COLOR" APP_API_PORT APP_WEB_PORT APP_TMA_PORT RELEASE_ID
export FB_AGENT_BOOTSTRAP_CLUSTER_ID="$BOOTSTRAP_CLUSTER_ID"
export APP_ENV_FILE="$APP_ENV" BACKUP_ENV_FILE="$BACKUP_ENV"
export BROWSER_CONTROL_ENV_FILE="$BROWSER_CONTROL_ENV"
export BROWSER_MAINTENANCE_ENV_FILE="$BROWSER_MAINTENANCE_ENV"
export BROWSER_AUTOPAUSE_ENV_FILE="$BROWSER_AUTOPAUSE_ENV"
export BROWSER_META_API_ENV_FILE="$BROWSER_META_API_ENV"
export BROWSER_CAMPAIGN_CREATOR_ENV_FILE="$BROWSER_CAMPAIGN_CREATOR_ENV"
export BROWSER_AUTHORITY_ENV_FILE="$BROWSER_AUTHORITY_ENV"
export PGBACKREST_CONFIG_FILE="$PGBACKREST_CONFIG"
export DESKTOP_READINESS_DIR="${DESKTOP_READINESS_DIR:-$STATE_DIR/desktop-readiness}"
infra=(docker compose -p "${INFRA_PROJECT_NAME:-fb_agent_infra}" \
  --env-file "$RELEASE_ENV" -f "$INFRA_COMPOSE")
app=(docker compose -p "fb_agent_${COLOR}" --env-file "$RELEASE_ENV" -f "$APP_COMPOSE")

acquire_runtime_mutation_lock() {
  local inherited_fd=""
  local canonical_active_state=""

  inherited_fd="${FB_AGENT_DEPLOY_LOCK_FD:-}"
  if [[ -n "$inherited_fd" ]]; then
    [[ "$inherited_fd" =~ ^[0-9]+$ ]] \
      || die "inherited deployment lock fd is invalid"
    python3 - "$inherited_fd" "$DEPLOY_LOCK_FILE" <<'PY' \
      || die "inherited deployment lock does not guard the canonical lock file"
import os
import sys

try:
    descriptor = os.fstat(int(sys.argv[1]))
    target = os.stat(sys.argv[2], follow_symlinks=True)
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(
    0
    if (descriptor.st_dev, descriptor.st_ino) == (target.st_dev, target.st_ino)
    else 1
)
PY
    flock -n "$inherited_fd" \
      || die "inherited deployment lock is not held"
  elif [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
    exec 9>"$DEPLOY_LOCK_FILE"
    flock -n 9 || die "another deployment or reconciliation is already running"
    export FB_AGENT_DEPLOY_LOCK_FD=9
  else
    die "mutating runtime command requires the verified launcher or inherited deployment lock"
  fi

  [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]] || return 0
  canonical_active_state="$(
    python3 - "$STATE_DIR/active-state" <<'PY'
import sys
from pathlib import Path

pointer = Path(sys.argv[1])
if not pointer.is_symlink():
    raise SystemExit(1)
try:
    print(pointer.resolve(strict=True))
except OSError:
    raise SystemExit(1)
PY
  )" || die "canonical active application state is unavailable"
  [[ "$canonical_active_state" == "$APP_STATE_DIR" ]] \
    || die "active application state changed after verification; refusing mutation"
}

network_inventory_is_owned() {
  python3 "$NETWORK_INVENTORY" \
    --cluster-id "$BOOTSTRAP_CLUSTER_ID" \
    --network "$PLATFORM_NETWORK" \
    --phase runtime
}

app_services_running() {
  local service="" container_id="" inspection="" running="" release=""
  for service in "${APP_SERVICES[@]}"; do
    container_id="$("${app[@]}" --profile workers ps -q "$service")" || return 1
    [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || return 1
    inspection="$(docker inspect --format \
      '{{.State.Running}}|{{index .Config.Labels "com.fb-agent.release"}}' \
      "$container_id")" || return 1
    IFS='|' read -r running release <<<"$inspection"
    [[ "$running" == true && "$release" == "$RELEASE_ID" ]] || return 1
  done
}

release_tree_is_immutable() {
  local release_dir=""
  release_dir="$(readlink -f "$CURRENT_DIR")"
  [[ "$release_dir" == "$ROOT_DIR/releases/"* ]] || return 1
  python3 "$CURRENT_DIR/scripts/release-state.py" manifest-verify \
    --release-dir "$release_dir" \
    --manifest "$release_dir/.fb-agent-source-manifest.json" \
    --require-read-only >/dev/null
}

command="${1:-status}"
shift || true
case "$command" in
  up|stop|restart|compose|infra) acquire_runtime_mutation_lock ;;
esac
case "$command" in
  up)
    if ! "${infra[@]}" up -d redis; then
      warn "optional Redis could not be started; PostgreSQL control plane will continue degraded"
    fi
    "${infra[@]}" up -d --wait --wait-timeout 240 postgres
    network_inventory_is_owned
    "${app[@]}" --profile workers up -d --no-deps --remove-orphans "${APP_SERVICES[@]}"
    for _ in $(seq 1 48); do
      if "$0" boot-ready; then
        exit 0
      fi
      sleep 5
    done
    die "application workers did not become boot-ready within 240 seconds"
    ;;
  stop)
    exec "${app[@]}" --profile workers stop --timeout 90 "${APP_SERVICES[@]}"
    ;;
  restart)
    "${app[@]}" --profile workers stop --timeout 90 "${APP_SERVICES[@]}"
    exec "$0" up
    ;;
  status)
    "${infra[@]}" ps
    exec "${app[@]}" --profile workers ps "$@"
    ;;
  logs)
    exec "${app[@]}" --profile workers logs --tail="${LOG_TAIL:-200}" "$@"
    ;;
  ready)
    network_inventory_is_owned
    release_tree_is_immutable
    curl --silent --show-error --fail --max-time 10 \
      "http://127.0.0.1:${APP_API_PORT}/healthz" >/dev/null
    curl --silent --show-error --fail --max-time 10 \
      "http://127.0.0.1:${APP_API_PORT}/readyz" >/dev/null
    response="$(curl --silent --show-error --max-time 10 \
      "http://127.0.0.1:${APP_API_PORT}/system-readyz")"
    app_services_running
    python3 -c '
import json, sys
data = json.loads(sys.argv[1])
expected = int(data.get("actors_expected") or 0)
active = int(data.get("actors_active") or 0)
blockers = {item for item in (data.get("blockers") or []) if isinstance(item, str)}
allowed = set()
if data.get("scanning_enabled") is False:
    allowed = {"scanning_paused"}
actors_ready = True if data.get("scanning_enabled") is False else expected > 0 and active == expected
ok = (
    data.get("infrastructure_ready")
    and actors_ready
    and int(data.get("stale_money_tasks") or 0) == 0
    and int(data.get("expired_money_tasks") or 0) == 0
    and blockers.issubset(allowed)
)
raise SystemExit(0 if ok else 1)
' "$response"
    printf 'Active %s release %s is ready\n' "$COLOR" "$RELEASE_ID"
    ;;
  boot-ready)
    network_inventory_is_owned
    curl --silent --show-error --fail --max-time 10 \
      "http://127.0.0.1:${APP_API_PORT}/healthz" >/dev/null
    curl --silent --show-error --fail --max-time 10 \
      "http://127.0.0.1:${APP_API_PORT}/readyz" >/dev/null
    response="$(curl --silent --show-error --max-time 10 \
      "http://127.0.0.1:${APP_API_PORT}/system-readyz")"
    app_services_running
    python3 -c '
import json, sys
data = json.loads(sys.argv[1])
expected = int(data.get("actors_expected") or 0)
active = int(data.get("actors_active") or 0)
blockers = {item for item in (data.get("blockers") or []) if isinstance(item, str)}
allowed = set()
if data.get("scanning_enabled") is False:
    allowed = {"scanning_paused"}
actors_ready = True if data.get("scanning_enabled") is False else expected > 0 and active == expected
ok = (
    data.get("infrastructure_ready")
    and actors_ready
    and int(data.get("stale_money_tasks") or 0) == 0
    and int(data.get("expired_money_tasks") or 0) == 0
    and blockers.issubset(allowed)
)
raise SystemExit(0 if ok else 1)
' "$response"
    printf 'Active %s application release %s is boot-ready\n' "$COLOR" "$RELEASE_ID"
    ;;
  compose) exec "${app[@]}" --profile workers "$@" ;;
  infra) exec "${infra[@]}" "$@" ;;
  *) die "unsupported command: $command" ;;
esac
