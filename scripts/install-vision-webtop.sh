#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly SOURCE_DIR="$PROJECT_DIR/deploy/vision-webtop"
readonly TARGET_DIR="${VISION_WEBTOP_ROOT:-/opt/vision-webtop}"
readonly BACKUP_COMPOSE="$TARGET_DIR/compose.yaml.pre-fb-agent"
RESTART_APP=false

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { printf 'ERROR: run as root\n' >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { printf 'ERROR: docker is not installed\n' >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { printf 'ERROR: Docker Compose v2 is unavailable\n' >&2; exit 1; }
[[ -d "$TARGET_DIR/config" ]] || { printf 'ERROR: persistent webtop config is missing: %s/config\n' "$TARGET_DIR" >&2; exit 1; }
docker network inspect fb_agent_default >/dev/null 2>&1 || docker network create fb_agent_default >/dev/null

if [[ -f "$TARGET_DIR/compose.yaml" && ! -f "$BACKUP_COMPOSE" ]]; then
  cp -a "$TARGET_DIR/compose.yaml" "$BACKUP_COMPOSE"
fi
install -m 0644 "$SOURCE_DIR/Dockerfile" "$TARGET_DIR/Dockerfile"
install -m 0644 "$SOURCE_DIR/compose.yaml" "$TARGET_DIR/compose.yaml"
install -m 0755 "$SOURCE_DIR/vision-service-run" "$TARGET_DIR/vision-service-run"
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
  printf 'ERROR: Vision webtop update failed; restoring previous compose\n' >&2
  if [[ -f "$BACKUP_COMPOSE" ]]; then
    cp -a "$BACKUP_COMPOSE" "$TARGET_DIR/compose.yaml"
    docker compose --project-directory "$TARGET_DIR" -f "$TARGET_DIR/compose.yaml" up -d || true
  fi
  exit "$exit_code"
}
trap rollback ERR

docker compose --project-directory "$TARGET_DIR" -f "$TARGET_DIR/compose.yaml" build --pull
if docker ps --format '{{.Names}}' | grep -qx 'fb_agent-browser-agent-1'; then
  docker stop --time 90 fb_agent-browser-agent-1 >/dev/null
  RESTART_APP=true
fi
docker compose --project-directory "$TARGET_DIR" -f "$TARGET_DIR/compose.yaml" up -d --force-recreate

for _ in $(seq 1 60); do
  if curl --silent --fail --max-time 3 http://127.0.0.1:3000/ >/dev/null; then
    if [[ "$RESTART_APP" == true ]] && systemctl list-unit-files fb-agent.service >/dev/null 2>&1; then
      systemctl restart fb-agent.service
    fi
    trap - ERR
    printf 'Vision webtop installed; desktop is available on 127.0.0.1:3000\n'
    exit 0
  fi
  sleep 2
done
printf 'ERROR: webtop did not become ready\n' >&2
exit 1
