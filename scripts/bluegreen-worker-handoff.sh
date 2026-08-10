#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "${FB_AGENT_PROJECT_DIR:-$SCRIPT_DIR/..}" && pwd -P)"
readonly PROJECT_DIR
# shellcheck source=scripts/browser-control-env.sh
source "$SCRIPT_DIR/browser-control-env.sh"
readonly APP_COMPOSE="$PROJECT_DIR/deploy/compose/docker-compose.app.yml"
FROM_COLOR=""
TO_COLOR=""
FROM_RELEASE_ENV=""
TO_RELEASE_ENV=""
APP_ENV=""
BACKUP_ENV=""
DRY_RUN=false
DEADLINE_EPOCH=""

readonly -a NON_MONEY_WORKERS=(
  observer meta_api telegram_delivery_worker telegram_update_worker cleanup reconciler
  health_watchdog digest_scheduler tracker_reconciliation_worker
  campaign_creator
)
readonly -a MONEY_WORKERS=(autopause_worker)
readonly -a ALL_WORKERS=("${NON_MONEY_WORKERS[@]}" "${MONEY_WORKERS[@]}")
readonly -a SINGLETON_WORKERS=(
  cleanup reconciler health_watchdog digest_scheduler
)
readonly SINGLETON_READY_PREFIX="/tmp/fb-agent-postgres-singleton-"

die() { printf 'ERROR: %s\n' "$*" >&2; return 1; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

ports_for_color() {
  case "$1" in
    blue) APP_API_PORT=18100; APP_WEB_PORT=18080; APP_TMA_PORT=18081 ;;
    green) APP_API_PORT=28100; APP_WEB_PORT=28080; APP_TMA_PORT=28081 ;;
    *) die "color must be blue or green" ;;
  esac
  export APP_API_PORT APP_WEB_PORT APP_TMA_PORT
}

release_id_from() {
  sed -n 's/^RELEASE_ID=//p' "$1" | tail -n 1
}

run_app_compose() {
  local -r color="$1"
  local -r release_env="$2"
  shift 2
  ports_for_color "$color"
  export APP_COLOR="$color"
  export RELEASE_ID
  RELEASE_ID="$(release_id_from "$release_env")"
  run_before_deadline docker compose -p "fb_agent_${color}" \
    --env-file "$release_env" -f "$APP_COMPOSE" "$@"
}

while (($#)); do
  case "$1" in
    --from-color) FROM_COLOR="${2:?missing value}"; shift 2 ;;
    --to-color) TO_COLOR="${2:?missing value}"; shift 2 ;;
    --from-release-env) FROM_RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --to-release-env) TO_RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --app-env) APP_ENV="${2:?missing value}"; shift 2 ;;
    --backup-env) BACKUP_ENV="${2:?missing value}"; shift 2 ;;
    --deadline-epoch) DEADLINE_EPOCH="${2:?missing value}"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "$DEADLINE_EPOCH" =~ ^[0-9]+$ ]] || die "--deadline-epoch is required"
now_epoch="$(date +%s)"
((DEADLINE_EPOCH > now_epoch && DEADLINE_EPOCH <= now_epoch + 180)) \
  || die "absolute deadline must be within the next 180 seconds"
readonly DEADLINE_EPOCH

remaining_seconds() {
  local now=""
  now="$(date +%s)"
  local -r remaining=$((DEADLINE_EPOCH - now))
  ((remaining > 0)) || return 1
  printf '%s\n' "$remaining"
}

run_before_deadline() {
  local remaining=""
  remaining="$(remaining_seconds)" || return 124
  timeout --signal=KILL "${remaining}s" "$@"
}

[[ "$TO_COLOR" == blue || "$TO_COLOR" == green ]] || die "--to-color is required"
[[ -f "$TO_RELEASE_ENV" ]] || die "target release manifest is missing"
[[ -f "$APP_ENV" ]] || die "application env is missing"
[[ -f "$BACKUP_ENV" ]] || die "backup env is missing"
if [[ -n "$FROM_COLOR" ]]; then
  [[ "$FROM_COLOR" == blue || "$FROM_COLOR" == green ]] || die "invalid --from-color"
  [[ -f "$FROM_RELEASE_ENV" ]] || die "previous release manifest is missing"
fi
export APP_ENV_FILE="$APP_ENV"
export BACKUP_ENV_FILE="$BACKUP_ENV"
[[ -n "${BROWSER_CONTROL_ENV_FILE:-}" ]] \
  || die "BROWSER_CONTROL_ENV_FILE is required"
browser_control_env_require "$BROWSER_CONTROL_ENV_FILE" \
  || die "browser control environment failed the private-file contract"
BROWSER_MAINTENANCE_ENV_FILE="${BROWSER_MAINTENANCE_ENV_FILE:-$(dirname -- "$BROWSER_CONTROL_ENV_FILE")/browser-maintenance.env}"
BROWSER_AUTOPAUSE_ENV_FILE="${BROWSER_AUTOPAUSE_ENV_FILE:-$(dirname -- "$BROWSER_CONTROL_ENV_FILE")/browser-autopause.env}"
BROWSER_META_API_ENV_FILE="${BROWSER_META_API_ENV_FILE:-$(dirname -- "$BROWSER_CONTROL_ENV_FILE")/browser-meta-api.env}"
BROWSER_CAMPAIGN_CREATOR_ENV_FILE="${BROWSER_CAMPAIGN_CREATOR_ENV_FILE:-$(dirname -- "$BROWSER_CONTROL_ENV_FILE")/browser-campaign-creator.env}"
BROWSER_AUTHORITY_ENV_FILE="${BROWSER_AUTHORITY_ENV_FILE:-$(dirname -- "$BROWSER_CONTROL_ENV_FILE")/browser-authority.env}"
browser_maintenance_env_require "$BROWSER_MAINTENANCE_ENV_FILE" \
  || die "browser maintenance environment failed the private-file contract"
for operation_env in \
  "$BROWSER_AUTOPAUSE_ENV_FILE" \
  "$BROWSER_META_API_ENV_FILE" \
  "$BROWSER_CAMPAIGN_CREATOR_ENV_FILE"; do
  browser_operation_env_require "$operation_env" \
    || die "browser operation environment failed the private-file contract"
done
browser_authority_env_require "$BROWSER_AUTHORITY_ENV_FILE" \
  || die "browser authority environment failed the private-file contract"
export BROWSER_CONTROL_ENV_FILE BROWSER_MAINTENANCE_ENV_FILE
export BROWSER_AUTOPAUSE_ENV_FILE BROWSER_META_API_ENV_FILE
export BROWSER_CAMPAIGN_CREATOR_ENV_FILE BROWSER_AUTHORITY_ENV_FILE
FB_AGENT_BOOTSTRAP_CLUSTER_ID="$(
  sed -n 's/^FB_AGENT_BOOTSTRAP_CLUSTER_ID=//p' "$APP_ENV" | tail -n 1
)"
[[ "$FB_AGENT_BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] \
  || die "application env has an invalid bootstrap cluster id"
export FB_AGENT_BOOTSTRAP_CLUSTER_ID
PGBACKREST_CONFIG_FILE="${PGBACKREST_CONFIG_FILE:-$(dirname -- "$BACKUP_ENV")/pgbackrest.conf}"
[[ -f "$PGBACKREST_CONFIG_FILE" && ! -L "$PGBACKREST_CONFIG_FILE" ]] \
  || die "stable pgBackRest config is missing"
export PGBACKREST_CONFIG_FILE

if [[ "$DRY_RUN" == true ]]; then
  printf 'Would hand workers from %s to %s\n' "${FROM_COLOR:-none}" "$TO_COLOR"
  exit 0
fi

target_release_id="$(release_id_from "$TO_RELEASE_ENV")"
[[ "$target_release_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid target release id"

container_release_signature() {
  local -r service="$1"
  local container_id="" inspection="" running="" release="" restart_count=""
  container_id="$(run_app_compose "$TO_COLOR" "$TO_RELEASE_ENV" ps -q "$service")" \
    || return 1
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || return 1
  inspection="$(run_before_deadline docker inspect --format \
    '{{.State.Running}}|{{index .Config.Labels "com.fb-agent.release"}}|{{.RestartCount}}' \
    "$container_id")" || return 1
  IFS='|' read -r running release restart_count <<<"$inspection"
  [[ "$running" == true && "$release" == "$target_release_id" \
    && "$restart_count" =~ ^[0-9]+$ ]] || return 1
  printf '%s:%s\n' "$container_id" "$restart_count"
}

wait_target_money_ready() {
  local previous_autopause=""
  local current_autopause=""
  while remaining_seconds >/dev/null; do
    current_autopause="$(container_release_signature autopause_worker)" || current_autopause=""
    if [[ -n "$current_autopause" \
      && "$current_autopause" == "$previous_autopause" ]]; then
      printf 'Target money workers are release-specific and stable: %s\n' "$target_release_id"
      return 0
    fi
    previous_autopause="$current_autopause"
    remaining="$(remaining_seconds)" || break
    sleep_seconds=2
    ((remaining < sleep_seconds)) && sleep_seconds="$remaining"
    sleep "$sleep_seconds"
  done
  return 1
}

target_services_running() {
  local service=""
  for service in "${ALL_WORKERS[@]}"; do
    container_release_signature "$service" >/dev/null || return 1
  done
}

target_singletons_ready() {
  local service=""
  for service in "${SINGLETON_WORKERS[@]}"; do
    # shellcheck disable=SC2016 # Variables expand in the worker container shell.
    run_app_compose "$TO_COLOR" "$TO_RELEASE_ENV" exec -T "$service" sh -eu -c '
      marker="$1"
      value="$(cat "$marker")"
      pid="${value##*:}"
      case "$pid" in ""|*[!0-9]*) exit 1 ;; esac
      kill -0 "$pid"
    ' sh "${SINGLETON_READY_PREFIX}${service}.ready" >/dev/null || return 1
  done
}

retire_removed_worker_containers() {
  local -r color="$1"
  local -r project="fb_agent_${color}"
  local container_ids="" container_id="" service="" remaining="" stop_timeout=""
  container_ids="$(run_before_deadline docker ps -q \
    --filter "label=com.docker.compose.project=${project}" \
    --filter "label=com.fb-agent.metrics=true")" || return 1
  [[ -n "$container_ids" ]] || return 0

  while IFS= read -r container_id; do
    [[ "$container_id" =~ ^[a-f0-9]+$ ]] || return 1
    service="$(run_before_deadline docker inspect --format \
      '{{index .Config.Labels "com.docker.compose.service"}}' "$container_id")" \
      || return 1
    # The API is the only metrics producer intentionally left on the old color.
    [[ "$service" == "api" ]] && continue
    remaining="$(remaining_seconds)" || return 124
    stop_timeout=60
    ((remaining < stop_timeout)) && stop_timeout="$remaining"
    run_before_deadline docker stop --time "$stop_timeout" "$container_id" >/dev/null \
      || return 1
  done <<<"$container_ids"
}

# New consumers join the fenced PostgreSQL queue before the incumbent leaves.
# A short overlap is safe because claims/finalization are lease-token guarded;
# stopping the incumbent first would create an avoidable money-lane blackout.
run_app_compose "$TO_COLOR" "$TO_RELEASE_ENV" --profile workers \
  up -d --no-deps --remove-orphans "${NON_MONEY_WORKERS[@]}"
run_app_compose "$TO_COLOR" "$TO_RELEASE_ENV" --profile workers \
  up -d --no-deps "${MONEY_WORKERS[@]}"
wait_target_money_ready || die "target money workers never became release-specific and stable"

if [[ -n "$FROM_COLOR" ]]; then
  remaining="$(remaining_seconds)" || die "deadline expired before incumbent handoff"
  stop_timeout=60
  ((remaining < stop_timeout)) && stop_timeout="$remaining"
  run_app_compose "$FROM_COLOR" "$FROM_RELEASE_ENV" --profile workers \
    stop --timeout "$stop_timeout" "${NON_MONEY_WORKERS[@]}"

  # Money is deliberately the final incumbent group stopped, after the target
  # money containers have been observed running and stable in the new release.
  remaining="$(remaining_seconds)" || die "deadline expired before money handoff"
  stop_timeout=60
  ((remaining < stop_timeout)) && stop_timeout="$remaining"
  run_app_compose "$FROM_COLOR" "$FROM_RELEASE_ENV" --profile workers \
    stop --timeout "$stop_timeout" "${MONEY_WORKERS[@]}"

  # A service removed or renamed between immutable releases is invisible to
  # `compose stop SERVICE...`. Retire any remaining worker metrics producer
  # without encoding a legacy service name or touching the old-color API.
  retire_removed_worker_containers "$FROM_COLOR" \
    || die "removed incumbent worker container could not be retired"
fi

ports_for_color "$TO_COLOR"
while remaining="$(remaining_seconds)"; do
  request_timeout=10
  ((remaining < request_timeout)) && request_timeout="$remaining"
  if ! response="$(run_before_deadline curl --silent --show-error --max-time "$request_timeout" \
    "http://127.0.0.1:${APP_API_PORT}/system-readyz")"; then
    response=""
  fi
  if target_services_running && target_singletons_ready && python3 -c '
import json, sys
try:
    data = json.loads(sys.argv[1])
except (json.JSONDecodeError, IndexError):
    raise SystemExit(1)
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
  ' "$response"; then
    printf 'Worker handoff to %s is ready\n' "$TO_COLOR"
    exit 0
  fi
  remaining="$(remaining_seconds)" || break
  sleep_seconds=5
  ((remaining < sleep_seconds)) && sleep_seconds="$remaining"
  sleep "$sleep_seconds"
done
die "target workers did not become ready before the absolute cutover deadline"
