#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly RELEASE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly PROJECT_NAME="${COMPOSE_PROJECT_NAME:-fb_agent}"
readonly LOCK_FILE="$ROOT_DIR/shared/deploy.lock"
ALLOW_VISION_OFFLINE=false

ensure_cdp_ready() {
  local api_key=""
  local response=""
  local attempt=0

  api_key="$(sed -n 's/^API_KEY=//p' "$RELEASE_DIR/.env" | tail -n 1)"
  [[ -n "$api_key" ]] || {
    printf 'ERROR: API_KEY is missing; cannot bootstrap Vision CDP\n' >&2
    return 1
  }

  if ! response="$(curl --silent --show-error --fail --max-time 30 \
    --request POST --header "X-API-Key: $api_key" \
    http://127.0.0.1:8100/api/vision/ensure-cdp)"; then
    printf 'ERROR: Vision ensure-cdp request failed\n' >&2
    return 1
  fi
  if ! python3 -c \
    'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("ok") else 1)' \
    "$response"; then
    printf 'ERROR: Vision ensure-cdp returned ok=false\n' >&2
    return 1
  fi

  for ((attempt = 1; attempt <= 20; attempt++)); do
    if response="$(curl --silent --show-error --fail --max-time 10 \
      --header "X-API-Key: $api_key" http://127.0.0.1:8100/api/settings/vision)" \
      && python3 -c \
        'import json,sys; d=json.loads(sys.argv[1]); raise SystemExit(0 if d.get("cdp_ready") and d.get("cdp_port") else 1)' \
        "$response"; then
      printf 'Vision CDP readiness: OK\n'
      return 0
    fi
    sleep 2
  done

  printf 'ERROR: Vision CDP did not become ready after ensure-cdp\n' >&2
  return 1
}

ensure_worker_heartbeats_ready() {
  local response=""
  local attempt=0

  for ((attempt = 1; attempt <= 24; attempt++)); do
    if response="$(curl --silent --show-error --max-time 10 \
      http://127.0.0.1:8100/system-readyz)" \
      && python3 -c '
import json, sys
d = json.loads(sys.argv[1])
expected = int(d.get("workers_expected") or 0)
online = int(d.get("workers_online") or 0)
raise SystemExit(0 if d.get("infrastructure_ready") and expected > 0 and online == expected else 1)
' "$response"; then
      printf 'Worker heartbeat readiness: OK\n'
      return 0
    fi
    sleep 5
  done

  printf 'ERROR: not all expected worker heartbeats became ONLINE\n' >&2
  return 1
}

clear_worker_heartbeats() {
  local -a heartbeat_keys=()
  local heartbeat_output=""
  local key=""

  # compose up может оставить Redis-ключи предыдущего release живыми ещё до 60с.
  # Очищаем их после старта новых контейнеров и принимаем release только когда
  # каждый новый worker заново опубликует heartbeat.
  if ! heartbeat_output="$(
    "${compose[@]}" exec -T redis redis-cli --raw --scan --pattern 'worker:heartbeat:*'
  )"; then
    printf 'ERROR: failed to scan stale worker heartbeats\n' >&2
    return 1
  fi

  while IFS= read -r key; do
    [[ -n "$key" ]] && heartbeat_keys+=("$key")
  done <<<"$heartbeat_output"

  if ((${#heartbeat_keys[@]})); then
    "${compose[@]}" exec -T redis redis-cli DEL "${heartbeat_keys[@]}" >/dev/null
  fi
  printf 'Cleared stale worker heartbeats; waiting for current release\n'
}

if [[ "${1:-}" == "--allow-vision-offline" ]]; then
  ALLOW_VISION_OFFLINE=true
elif (($#)); then
  printf 'ERROR: unknown argument: %s\n' "$1" >&2
  exit 2
fi

[[ -f "$RELEASE_DIR/.release-id" ]] || { printf 'ERROR: release id is missing\n' >&2; exit 1; }
release_id="$(<"$RELEASE_DIR/.release-id")"
[[ "$release_id" =~ ^[A-Za-z0-9._-]+$ ]] || { printf 'ERROR: invalid release id\n' >&2; exit 1; }
[[ -L "$RELEASE_DIR/.env" || -f "$RELEASE_DIR/.env" ]] || { printf 'ERROR: .env is missing\n' >&2; exit 1; }

mkdir -p "$ROOT_DIR/shared" "$ROOT_DIR/backups/postgres" "$ROOT_DIR/releases"
docker network inspect fb_agent_default >/dev/null 2>&1 || docker network create fb_agent_default >/dev/null
exec 9>"$LOCK_FILE"
flock -n 9 || { printf 'ERROR: another deployment is already running\n' >&2; exit 1; }

preflight_args=(--env-file "$RELEASE_DIR/.env")
[[ "$ALLOW_VISION_OFFLINE" == true ]] && preflight_args+=(--allow-vision-offline)
"$SCRIPT_DIR/server-preflight.sh" "${preflight_args[@]}"

previous_dir=""
previous_tag=""
if [[ -L "$ROOT_DIR/current" ]]; then
  previous_dir="$(readlink -f "$ROOT_DIR/current")"
  if [[ -f "$previous_dir/.release-id" ]]; then
    previous_tag="$(<"$previous_dir/.release-id")"
  fi
fi

if docker ps --format '{{.Names}}' | grep -qx "${PROJECT_NAME}-postgres-1"; then
  FB_AGENT_RELEASE_DIR="$RELEASE_DIR" "$SCRIPT_DIR/backup-postgres.sh"
fi

export IMAGE_TAG="$release_id"
export IMAGE_REPOSITORY="${IMAGE_REPOSITORY:-fb-stop-bot}"
compose=(docker compose -p "$PROJECT_NAME" --env-file "$RELEASE_DIR/.env" -f "$RELEASE_DIR/docker-compose.yml" --profile web --profile agent)

printf 'Building release %s...\n' "$release_id"
docker build --file "$RELEASE_DIR/docker/Dockerfile.python-base" \
  --tag "$IMAGE_REPOSITORY/python-base:$release_id" "$RELEASE_DIR"
"${compose[@]}" build

rollback() {
  exit_code=$?
  trap - ERR
  printf 'ERROR: release %s failed; attempting application rollback\n' "$release_id" >&2
  if [[ -n "$previous_dir" && -n "$previous_tag" ]]; then
    export IMAGE_TAG="$previous_tag"
    previous_compose=(docker compose -p "$PROJECT_NAME" --env-file "$previous_dir/.env" \
      -f "$previous_dir/docker-compose.yml" --profile web --profile agent)
    rollback_services=()
    while IFS= read -r service; do
      case "$service" in
        postgres|redis|migrate) ;;
        *) rollback_services+=("$service") ;;
      esac
    done < <("${previous_compose[@]}" config --services)

    # Предыдущий migrate-образ может не знать ревизию, которую уже применил новый
    # release (реальный инцидент: old Alembic не знал 0034). Поэтому откатываем
    # только application-контейнеры и не запускаем старый migrate через depends_on.
    # Миграции обязаны оставаться обратно совместимыми минимум с N-1 release;
    # восстановление/понижение БД всегда отдельная осознанная операция из backup.
    if ((${#rollback_services[@]})); then
      "${previous_compose[@]}" up -d --no-deps --remove-orphans \
        --wait --wait-timeout 240 "${rollback_services[@]}" || true
    fi
  fi
  printf 'Database was not downgraded automatically; use the pre-deploy backup only after an explicit data-loss review.\n' >&2
  exit "$exit_code"
}
trap rollback ERR

"${compose[@]}" up -d --remove-orphans --wait --wait-timeout 240
curl --silent --show-error --fail --retry 12 --retry-delay 5 --retry-all-errors \
  --max-time 10 http://127.0.0.1:8100/healthz >/dev/null
if ! ensure_cdp_ready; then
  # Vision/CDP — внешний runtime-контур. После успешного запуска API/воркеров его
  # временная недоступность не должна откатывать рабочее приложение (и тем более
  # пытаться стартовать старый Alembic поверх новой схемы). Состояние остаётся честно
  # DEGRADED в /system-readyz, а ensure-cdp/observer продолжат самовосстановление.
  printf 'WARNING: Vision CDP is not ready; application remains deployed in safe degraded state\n' >&2
fi
curl --silent --show-error --fail --retry 12 --retry-delay 5 --retry-all-errors \
  --max-time 10 http://127.0.0.1:8100/readyz >/dev/null
# /readyz доказывает только Postgres/Redis. Отдельно ждём все heartbeat,
# иначе release мог стать green с мёртвым observer/meta money-контуром.
clear_worker_heartbeats
ensure_worker_heartbeats_ready

ln -sfn "$RELEASE_DIR" "$ROOT_DIR/current.new"
mv -Tf "$ROOT_DIR/current.new" "$ROOT_DIR/current"
trap - ERR

find "$ROOT_DIR/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -nr | tail -n +6 | cut -d' ' -f2- \
  | while IFS= read -r old_release; do
      [[ "$(readlink -f "$ROOT_DIR/current")" == "$old_release" ]] || rm -rf -- "$old_release"
    done

printf 'Release %s deployed successfully\n' "$release_id"
