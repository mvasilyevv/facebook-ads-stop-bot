#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly SOURCE_DIR="$PROJECT_DIR/deploy/vision-webtop"
readonly TARGET_DIR="${VISION_WEBTOP_ROOT:-/opt/vision-webtop}"
readonly COMPOSE_ENV_FILE="$PROJECT_DIR/.env"
readonly ACTIVE_MANIFEST_FILE="$TARGET_DIR/.production-manifest.sha256"
readonly GUACAMOLE_IMAGE="guacamole/guacamole@sha256:f344085e618bb05e22b964b0208dbd06d3468275bac70206f93805245e067b40"
readonly BROWSER_AGENT_CONTAINER="fb_agent-browser-agent-1"
ACTIVE_COMPOSE_BACKUP=""
ACTIVE_MANIFEST_BACKUP=""
MANIFEST_CHANGED=true
RESTART_APP=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

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

bootstrap_completed() {
  local container_id=""
  container_id="$(compose ps -a -q database-bootstrap)"
  [[ -n "$container_id" ]] \
    && [[ "$(docker inspect "$container_id" --format '{{.State.Status}}:{{.State.ExitCode}}')" == "exited:0" ]]
}

database_contract_is_ready() {
  local result=""
  result="$(compose exec -T postgres sh -eu -c \
    'psql --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<"SQL"
SELECT
  (SELECT count(*) FROM guacamole_connection)::text || '"'"':'"'"' ||
  (SELECT count(*) FROM guacamole_connection_permission WHERE permission = '"'"'READ'"'"')::text || '"'"':'"'"' ||
  (SELECT count(*) FROM guacamole_system_permission)::text || '"'"':'"'"' ||
  (SELECT count(*) FROM guacamole_connection_parameter
   WHERE parameter_name = '"'"'hostname'"'"' AND parameter_value = '"'"'vision-webtop'"'"')::text;
SQL')"
  [[ "$result" == "1:1:0:1" ]]
}

desktop_is_ready() {
  service_is_healthy webtop \
    && service_is_healthy guacd \
    && service_is_healthy postgres \
    && bootstrap_completed \
    && service_is_healthy guacamole \
    && curl --silent --fail --max-time 3 http://127.0.0.1:8090/desktop/ >/dev/null \
    && docker exec vision-webtop bash -c \
      'exec 8<>/dev/tcp/127.0.0.1/4822 && exec 9<>/dev/tcp/127.0.0.1/5900' \
    && database_contract_is_ready
}

ensure_cdp_ready() {
  local api_key=""
  local response=""

  api_key="$(sed -n 's/^API_KEY=//p' "$COMPOSE_ENV_FILE" | tail -n 1)"
  [[ -n "$api_key" ]] || die "API_KEY is missing; cannot restore Vision CDP"
  response="$(curl --silent --show-error --fail --max-time 30 \
    --request POST --header "X-API-Key: $api_key" \
    http://127.0.0.1:8100/api/vision/ensure-cdp)"
  python3 -c \
    'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("ok") else 1)' \
    "$response"
  printf 'Vision CDP readiness restored\n'
}

cleanup() {
  [[ -z "$ACTIVE_COMPOSE_BACKUP" ]] || rm -f -- "$ACTIVE_COMPOSE_BACKUP"
  [[ -z "$ACTIVE_MANIFEST_BACKUP" ]] || rm -f -- "$ACTIVE_MANIFEST_BACKUP"
}
trap cleanup EXIT

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
for command in curl docker install python3 sha256sum; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable"
[[ -s "$COMPOSE_ENV_FILE" ]] || die "release environment is missing: $COMPOSE_ENV_FILE"
[[ "$(stat -Lc '%a' "$COMPOSE_ENV_FILE")" == "600" ]] || die "$COMPOSE_ENV_FILE must have mode 600"
[[ -d "$TARGET_DIR/config" ]] || die "persistent webtop config is missing: $TARGET_DIR/config"
docker network inspect fb_agent_default >/dev/null 2>&1 || docker network create fb_agent_default >/dev/null

desktop_webtop_image="$(sed -n 's/^DESKTOP_WEBTOP_IMAGE=//p' "$COMPOSE_ENV_FILE" | tail -n 1)"
[[ "$desktop_webtop_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
  || die "DESKTOP_WEBTOP_IMAGE must be an immutable image@sha256 reference"

manifest_hash="$({
  sha256sum \
    "$SOURCE_DIR/compose.yaml" \
    "$SOURCE_DIR/Dockerfile" \
    "$SOURCE_DIR/bootstrap-guacamole-db.sh" \
    "$SOURCE_DIR/build-guacamole-extension.py" \
    "$SOURCE_DIR/guacamole-extension/guac-manifest.json" \
    "$SOURCE_DIR/guacamole-extension/html/user-menu.html" \
    "$SOURCE_DIR/guacamole-extension/css/adpulse-desktop.css" \
    "$SOURCE_DIR/vision-service-run" \
    "$SOURCE_DIR/vision-window-fit-run" \
    "$SOURCE_DIR/vision-vnc-run"
  sed -n \
    -e '/^DESKTOP_WEBTOP_IMAGE=/p' \
    -e '/^DESKTOP_VNC_PASSWORD=/p' \
    -e '/^DESKTOP_GUACAMOLE_POSTGRES_/p' \
    "$COMPOSE_ENV_FILE" | sort | sha256sum
} | sha256sum | awk '{print $1}')"

if [[ -f "$ACTIVE_MANIFEST_FILE" ]] \
  && [[ "$(<"$ACTIVE_MANIFEST_FILE")" == "$manifest_hash" ]]; then
  MANIFEST_CHANGED=false
fi

if [[ -f "$TARGET_DIR/compose.yaml" ]]; then
  ACTIVE_COMPOSE_BACKUP="$(mktemp)"
  cp -a "$TARGET_DIR/compose.yaml" "$ACTIVE_COMPOSE_BACKUP"
fi
if [[ -f "$ACTIVE_MANIFEST_FILE" ]]; then
  ACTIVE_MANIFEST_BACKUP="$(mktemp)"
  cp -a "$ACTIVE_MANIFEST_FILE" "$ACTIVE_MANIFEST_BACKUP"
fi

install -m 0600 "$SOURCE_DIR/compose.yaml" "$TARGET_DIR/compose.yaml"
install -m 0700 "$SOURCE_DIR/bootstrap-guacamole-db.sh" "$TARGET_DIR/bootstrap-guacamole-db.sh"
python3 "$SOURCE_DIR/build-guacamole-extension.py" \
  --source "$SOURCE_DIR/guacamole-extension" \
  --output "$TARGET_DIR/adpulse-desktop-navigation.jar"
chmod 0644 "$TARGET_DIR/adpulse-desktop-navigation.jar"
install -m 0755 "$SOURCE_DIR/vision-service-run" "$TARGET_DIR/vision-service-run"
install -m 0755 "$SOURCE_DIR/vision-window-fit-run" "$TARGET_DIR/vision-window-fit-run"
install -m 0755 "$SOURCE_DIR/vision-vnc-run" "$TARGET_DIR/vision-vnc-run"
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

if [[ "$MANIFEST_CHANGED" == true || ! -s "$TARGET_DIR/guacamole-schema.sql" ]]; then
  schema_tmp="$(mktemp)"
  docker run --rm "$GUACAMOLE_IMAGE" /opt/guacamole/bin/initdb.sh --postgresql >"$schema_tmp"
  grep -Fq 'CREATE TABLE guacamole_connection (' "$schema_tmp" \
    || die "Guacamole 1.6.0 schema generation failed"
  install -m 0600 "$schema_tmp" "$TARGET_DIR/guacamole-schema.sql"
  rm -f -- "$schema_tmp"
fi

rollback() {
  local exit_code=$?
  trap - ERR
  printf 'ERROR: desktop stack update failed; restoring the previously active manifest\n' >&2
  if [[ -n "$ACTIVE_COMPOSE_BACKUP" && -f "$ACTIVE_COMPOSE_BACKUP" ]]; then
    cp -a "$ACTIVE_COMPOSE_BACKUP" "$TARGET_DIR/compose.yaml"
    compose up -d --remove-orphans || true
  fi
  if [[ -n "$ACTIVE_MANIFEST_BACKUP" && -f "$ACTIVE_MANIFEST_BACKUP" ]]; then
    cp -a "$ACTIVE_MANIFEST_BACKUP" "$ACTIVE_MANIFEST_FILE"
  else
    rm -f -- "$ACTIVE_MANIFEST_FILE"
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
while IFS= read -r image; do
  [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] \
    || die "desktop image is not digest-pinned: $image"
done < <(compose config --images)

if [[ "$MANIFEST_CHANGED" == true ]]; then
  compose pull
  if systemctl is-active --quiet fb-agent.service \
    || docker ps --format '{{.Names}}' | grep -qx "$BROWSER_AGENT_CONTAINER"; then
    RESTART_APP=true
  fi
  if docker ps -a --format '{{.Names}}' | grep -qx "$BROWSER_AGENT_CONTAINER"; then
    docker stop --time 90 "$BROWSER_AGENT_CONTAINER" >/dev/null 2>&1 || true
    # network_mode: container:vision-webtop stores the old container ID.
    docker rm -f "$BROWSER_AGENT_CONTAINER" >/dev/null
  fi
  # Bind-mounted extension/bootstrap changes do not alter Docker's container
  # config hash. Remove only the dependent desktop services so the new
  # production manifest is loaded exactly once; persistent JDBC/X11 data stays.
  compose rm -sf guacamole guacd database-bootstrap
  compose up -d --remove-orphans
else
  # A release that does not change the desktop manifest must not rebuild or
  # force-recreate this independent stack. This only restores missing services.
  compose up -d --remove-orphans
fi

for _ in $(seq 1 60); do
  if desktop_is_ready; then
    if [[ "$RESTART_APP" == true ]] \
      && systemctl list-unit-files fb-agent.service >/dev/null 2>&1; then
      systemctl restart fb-agent.service
      ensure_cdp_ready
    fi
    printf '%s\n' "$manifest_hash" >"$ACTIVE_MANIFEST_FILE"
    trap - ERR
    printf 'Vision desktop production stack is ready on 127.0.0.1:8090/desktop/\n'
    exit 0
  fi
  sleep 2
done
die "desktop stack did not become ready"
