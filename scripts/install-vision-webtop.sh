#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly SOURCE_DIR="$PROJECT_DIR/deploy/vision-webtop"
readonly TARGET_DIR="${VISION_WEBTOP_ROOT:-/opt/vision-webtop}"
readonly COMPOSE_ENV_FILE="$PROJECT_DIR/.env"
readonly INITIAL_COMPOSE_BACKUP="$TARGET_DIR/compose.yaml.pre-fb-agent"
readonly BROWSER_AGENT_CONTAINER="fb_agent-browser-agent-1"
ACTIVE_COMPOSE_BACKUP=""
ACTIVE_IMAGE_REF=""
ROLLBACK_IMAGE=""
RESTART_APP=false

compose() {
  docker compose \
    --env-file "$COMPOSE_ENV_FILE" \
    --project-directory "$TARGET_DIR" \
    -f "$TARGET_DIR/compose.yaml" \
    "$@"
}

service_is_healthy() {
  local container_id=""
  container_id="$(compose ps -q "$1")"
  [[ -n "$container_id" ]] \
    && [[ "$(docker inspect "$container_id" --format '{{.State.Health.Status}}')" == "healthy" ]]
}

cleanup() {
  if [[ -n "$ACTIVE_COMPOSE_BACKUP" ]]; then
    rm -f -- "$ACTIVE_COMPOSE_BACKUP"
  fi
  if [[ -n "$ROLLBACK_IMAGE" ]]; then
    docker image rm "$ROLLBACK_IMAGE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

ensure_cdp_ready() {
  local api_key=""
  local response=""

  [[ -f "$PROJECT_DIR/.env" ]] || {
    printf 'ERROR: release environment is missing; cannot restore Vision CDP\n' >&2
    return 1
  }
  api_key="$(sed -n 's/^API_KEY=//p' "$PROJECT_DIR/.env" | tail -n 1)"
  [[ -n "$api_key" ]] || {
    printf 'ERROR: API_KEY is missing; cannot restore Vision CDP\n' >&2
    return 1
  }
  response="$(curl --silent --show-error --fail --max-time 30 \
    --request POST --header "X-API-Key: $api_key" \
    http://127.0.0.1:8100/api/vision/ensure-cdp)"
  python3 -c \
    'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("ok") else 1)' \
    "$response"
  printf 'Vision CDP readiness restored\n'
}

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { printf 'ERROR: run as root\n' >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { printf 'ERROR: docker is not installed\n' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { printf 'ERROR: Docker Compose v2 is unavailable\n' >&2; exit 1; }
[[ -f "$COMPOSE_ENV_FILE" ]] || {
  printf 'ERROR: release environment is missing: %s\n' "$COMPOSE_ENV_FILE" >&2
  exit 1
}
[[ -d "$TARGET_DIR/config" ]] || { printf 'ERROR: persistent webtop config is missing: %s/config\n' "$TARGET_DIR" >&2; exit 1; }
docker network inspect fb_agent_default >/dev/null 2>&1 || docker network create fb_agent_default >/dev/null

if [[ -f "$TARGET_DIR/compose.yaml" ]]; then
  ACTIVE_COMPOSE_BACKUP="$(mktemp)"
  cp -a "$TARGET_DIR/compose.yaml" "$ACTIVE_COMPOSE_BACKUP"
  if [[ ! -f "$INITIAL_COMPOSE_BACKUP" ]]; then
    cp -a "$TARGET_DIR/compose.yaml" "$INITIAL_COMPOSE_BACKUP"
  fi
fi
if docker container inspect vision-webtop >/dev/null 2>&1; then
  ACTIVE_IMAGE_REF="$(docker inspect vision-webtop --format '{{.Config.Image}}')"
  ROLLBACK_IMAGE="fb-agent/vision-webtop:rollback-$$"
  docker image tag "$(docker inspect vision-webtop --format '{{.Image}}')" "$ROLLBACK_IMAGE"
fi
install -m 0644 "$SOURCE_DIR/Dockerfile" "$TARGET_DIR/Dockerfile"
install -m 0644 "$SOURCE_DIR/compose.yaml" "$TARGET_DIR/compose.yaml"
install -m 0644 \
  "$SOURCE_DIR/selkies-clipboard-bridge.js" \
  "$TARGET_DIR/selkies-clipboard-bridge.js"
install -m 0755 "$SOURCE_DIR/vision-service-run" "$TARGET_DIR/vision-service-run"
install -m 0755 "$SOURCE_DIR/vision-window-fit-run" "$TARGET_DIR/vision-window-fit-run"
install -m 0755 "$SOURCE_DIR/vision-vnc-run" "$TARGET_DIR/vision-vnc-run"
rm -f -- "$TARGET_DIR/mobile-controls.js"
install -d -m 0700 \
  "$TARGET_DIR/config/.local/bin" "$TARGET_DIR/config/.config/autostart"
install -m 0755 "$SOURCE_DIR/disable-server-capslock" \
  "$TARGET_DIR/config/.local/bin/disable-server-capslock"
install -m 0644 "$SOURCE_DIR/disable-server-capslock.desktop" \
  "$TARGET_DIR/config/.config/autostart/disable-server-capslock.desktop"
chown 1000:1000 \
  "$TARGET_DIR/config/.local/bin" \
  "$TARGET_DIR/config/.config/autostart" \
  "$TARGET_DIR/config/.local/bin/disable-server-capslock" \
  "$TARGET_DIR/config/.config/autostart/disable-server-capslock.desktop"

rollback() {
  exit_code=$?
  trap - ERR
  printf 'ERROR: Vision webtop update failed; restoring the previously active compose/image\n' >&2
  if [[ -n "$ACTIVE_COMPOSE_BACKUP" && -f "$ACTIVE_COMPOSE_BACKUP" ]]; then
    cp -a "$ACTIVE_COMPOSE_BACKUP" "$TARGET_DIR/compose.yaml"
  fi
  if [[ -n "$ROLLBACK_IMAGE" && -n "$ACTIVE_IMAGE_REF" ]]; then
    docker image tag "$ROLLBACK_IMAGE" "$ACTIVE_IMAGE_REF" || true
  fi
  if [[ -f "$TARGET_DIR/compose.yaml" ]]; then
    compose up -d || true
  fi
  if [[ "$RESTART_APP" == true ]] \
    && systemctl list-unit-files fb-agent.service >/dev/null 2>&1; then
    docker rm -f "$BROWSER_AGENT_CONTAINER" >/dev/null 2>&1 || true
    systemctl restart fb-agent.service || true
  fi
  exit "$exit_code"
}
trap rollback ERR

compose config --quiet
compose build --pull
if systemctl is-active --quiet fb-agent.service \
  || docker ps --format '{{.Names}}' | grep -qx "$BROWSER_AGENT_CONTAINER"; then
  RESTART_APP=true
fi
if docker ps -a --format '{{.Names}}' | grep -qx "$BROWSER_AGENT_CONTAINER"; then
  docker stop --time 90 "$BROWSER_AGENT_CONTAINER" >/dev/null 2>&1 || true
  # network_mode: container:vision-webtop stores the target container ID.
  # Starting this container after Webtop recreation would reuse a dead namespace.
  docker rm -f "$BROWSER_AGENT_CONTAINER" >/dev/null
fi
compose up -d --force-recreate

for _ in $(seq 1 60); do
  if service_is_healthy webtop \
    && service_is_healthy guacd \
    && service_is_healthy guacamole \
    && curl --silent --fail --max-time 3 http://127.0.0.1:3000/ >/dev/null \
    && curl --silent --fail --max-time 3 http://127.0.0.1:8090/guacamole/ >/dev/null \
    && docker exec vision-webtop pgrep -x X0tigervnc >/dev/null; then
    if [[ "$RESTART_APP" == true ]] && systemctl list-unit-files fb-agent.service >/dev/null 2>&1; then
      systemctl restart fb-agent.service
      ensure_cdp_ready
    fi
    trap - ERR
    printf 'Vision webtop + Guacamole installed; canary is available on 127.0.0.1:8090/guacamole/\n'
    exit 0
  fi
  sleep 2
done
printf 'ERROR: webtop did not become ready\n' >&2
exit 1
