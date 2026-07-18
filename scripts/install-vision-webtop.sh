#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly SOURCE_DIR="$PROJECT_DIR/deploy/vision-webtop"
readonly KASM_SOURCE_DIR="$PROJECT_DIR/deploy/kasmvnc-sidecar"
readonly TARGET_DIR="${VISION_WEBTOP_ROOT:-/opt/vision-webtop}"
readonly COMPOSE_ENV_FILE="$PROJECT_DIR/.env"
readonly ACTIVE_MANIFEST_FILE="$TARGET_DIR/.production-manifest.sha256"
readonly BROWSER_AGENT_CONTAINER="fb_agent-browser-agent-1"

ACTIVE_COMPOSE_BACKUP=""
ACTIVE_MANIFEST_BACKUP=""
CONFIG_SNAPSHOT=""
BASELINE_FILE=""
POSTCHECK_FILE=""
MANIFEST_CHANGED=true
STACK_MUTATED=false
RESTART_APP=false

die() {
  printf 'ERROR: %s\n' "$*" >&2
  if [[ "$STACK_MUTATED" == true ]]; then
    rollback
  fi
  exit 1
}

compose() {
  docker compose \
    --env-file "$COMPOSE_ENV_FILE" \
    --project-directory "$TARGET_DIR" \
    -f "$TARGET_DIR/compose.yaml" \
    "$@"
}

dotenv_value() {
  local key=$1
  sed -n "s/^${key}=//p" "$COMPOSE_ENV_FILE" | tail -n 1
}

service_is_healthy() {
  local container_id=""
  container_id="$(compose ps -q "$1")"
  [[ -n "$container_id" ]] \
    && [[ "$(docker inspect "$container_id" --format '{{.State.Health.Status}}')" == "healthy" ]]
}

assert_database_quiescent() {
  local postgres_container=""
  local state=""
  postgres_container="$(docker ps \
    --filter label=com.docker.compose.project=fb_agent \
    --filter label=com.docker.compose.service=postgres \
    --format '{{.Names}}' | head -n 1)"
  [[ -n "$postgres_container" ]] || die "fb_agent postgres container is not running"
  state="$(docker exec "$postgres_container" sh -eu -c \
    'psql --no-psqlrc --tuples-only --no-align --set ON_ERROR_STOP=1 \
      --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'"'"'SQL'"'"'
SELECT
  COALESCE((
    SELECT is_scanning_enabled::text
    FROM observer_config
    WHERE singleton_key = '"'"'default'"'"'
  ), '"'"'false'"'"') || '"'"':'"'"' ||
  (SELECT count(*)::text FROM task_queue WHERE lower(status) = '"'"'running'"'"');
SQL')"
  [[ "$state" == "false:0" ]] || {
    die "desktop migration requires scanning paused and zero running tasks (current: $state)"
  }
}

capture_runtime_contract() {
  local destination=$1
  local api_key=""
  api_key="$(dotenv_value API_KEY)"
  [[ -n "$api_key" ]] || die "API_KEY is missing; cannot capture Vision contract"
  {
    printf 'display=:1\n'
    docker exec vision-webtop sh -eu -c \
      'DISPLAY=:1 xdpyinfo | awk '\''/dimensions:/{print "dimensions=" $2; exit}'\'''
    curl --silent --show-error --fail --max-time 15 \
      --header "X-API-Key: $api_key" \
      http://127.0.0.1:8100/api/settings/vision
    printf '\n'
  } >"$destination"
  chmod 0600 "$destination"
}

runtime_contract_matches() {
  python3 - "$BASELINE_FILE" "$POSTCHECK_FILE" <<'PY'
import json
import sys
from pathlib import Path

def load(path: str) -> tuple[str, dict[str, object]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    dimensions = next((line for line in lines if line.startswith("dimensions=")), "")
    payload = json.loads(lines[-1])
    return dimensions, payload

before_dimensions, before = load(sys.argv[1])
after_dimensions, after = load(sys.argv[2])
stable_keys = ("profile_id", "cdp_port")
ok = (
    before_dimensions == "dimensions=1366x768"
    and after_dimensions == before_dimensions
    and all(before.get(key) == after.get(key) for key in stable_keys)
)
raise SystemExit(0 if ok else 1)
PY
}

desktop_is_ready() {
  local anonymous_status=""
  local user=""
  local password=""
  user="$(dotenv_value DESKTOP_KASM_SERVICE_USER)"
  password="$(dotenv_value DESKTOP_KASM_SERVICE_PASSWORD)"
  anonymous_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --max-time 3 http://127.0.0.1:8444/ || true)"
  service_is_healthy webtop \
    && service_is_healthy kasmvnc \
    && [[ "$anonymous_status" == "401" ]] \
    && curl --silent --fail --max-time 3 \
      --user "$user:$password" http://127.0.0.1:8444/ >/dev/null \
    && docker exec vision-webtop sh -eu -c \
      'DISPLAY=:1 xdpyinfo | grep -Eq "dimensions:[[:space:]]+1366x768"' \
    && compose exec -T kasmvnc sh -eu -c \
      'pgrep -f "X(kasmvnc|vnc).*:10" >/dev/null && pgrep -x kasmxproxy >/dev/null'
}

ensure_cdp_ready() {
  local api_key=""
  local response=""
  api_key="$(dotenv_value API_KEY)"
  response="$(curl --silent --show-error --fail --max-time 30 \
    --request POST --header "X-API-Key: $api_key" \
    http://127.0.0.1:8100/api/vision/ensure-cdp)"
  python3 -c \
    'import json,sys; raise SystemExit(0 if json.loads(sys.argv[1]).get("ok") else 1)' \
    "$response"
}

cleanup() {
  [[ -z "$ACTIVE_COMPOSE_BACKUP" ]] || rm -f -- "$ACTIVE_COMPOSE_BACKUP"
  [[ -z "$ACTIVE_MANIFEST_BACKUP" ]] || rm -f -- "$ACTIVE_MANIFEST_BACKUP"
}
trap cleanup EXIT

rollback() {
  local exit_code=$?
  trap - ERR
  printf 'ERROR: Kasm desktop update failed; restoring compose, /config and browser-agent\n' >&2
  if [[ "$STACK_MUTATED" == true ]]; then
    compose down --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -n "$CONFIG_SNAPSHOT" && -f "$CONFIG_SNAPSHOT" ]]; then
    tar --extract --gzip --file "$CONFIG_SNAPSHOT" --directory "$TARGET_DIR" || true
  fi
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

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
for command in curl docker install python3 sha256sum tar; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable"
[[ -s "$COMPOSE_ENV_FILE" ]] || die "release environment is missing: $COMPOSE_ENV_FILE"
[[ "$(stat -Lc '%a' "$COMPOSE_ENV_FILE")" == "600" ]] || die "$COMPOSE_ENV_FILE must have mode 600"
[[ -d "$TARGET_DIR/config" ]] || die "persistent webtop config is missing: $TARGET_DIR/config"
docker network inspect fb_agent_default >/dev/null 2>&1 \
  || docker network create fb_agent_default >/dev/null

for key in DESKTOP_WEBTOP_IMAGE DESKTOP_KASMVNC_IMAGE; do
  image="$(dotenv_value "$key")"
  [[ "$image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || die "$key must be an immutable image@sha256 reference"
done

manifest_hash="$({
  sha256sum \
    "$SOURCE_DIR/compose.yaml" \
    "$SOURCE_DIR/Dockerfile" \
    "$SOURCE_DIR/vision-service-run" \
    "$SOURCE_DIR/vision-window-fit-run" \
    "$SOURCE_DIR/disable-server-capslock" \
    "$SOURCE_DIR/disable-server-capslock.desktop" \
    "$KASM_SOURCE_DIR/Dockerfile" \
    "$KASM_SOURCE_DIR/entrypoint.sh" \
    "$KASM_SOURCE_DIR/healthcheck.sh" \
    "$KASM_SOURCE_DIR/kasmvnc.yaml"
  sed -n \
    -e '/^DESKTOP_WEBTOP_IMAGE=/p' \
    -e '/^DESKTOP_KASMVNC_IMAGE=/p' \
    -e '/^DESKTOP_KASM_SERVICE_USER=/p' \
    -e '/^DESKTOP_KASM_SERVICE_PASSWORD=/p' \
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

if [[ "$MANIFEST_CHANGED" == true ]]; then
  assert_database_quiescent
  install -d -m 0700 "$TARGET_DIR/backups"
  snapshot_id="$(date -u +%Y%m%dT%H%M%SZ)"
  CONFIG_SNAPSHOT="$TARGET_DIR/backups/${snapshot_id}-pre-kasm-config.tar.gz"
  BASELINE_FILE="$TARGET_DIR/backups/${snapshot_id}-pre-kasm-baseline.txt"
  POSTCHECK_FILE="$TARGET_DIR/backups/${snapshot_id}-post-kasm-baseline.txt"
  tar --create --gzip --file "$CONFIG_SNAPSHOT" --directory "$TARGET_DIR" config
  capture_runtime_contract "$BASELINE_FILE"
fi

install -m 0600 "$SOURCE_DIR/compose.yaml" "$TARGET_DIR/compose.yaml"
install -m 0755 "$SOURCE_DIR/vision-service-run" "$TARGET_DIR/vision-service-run"
install -m 0755 "$SOURCE_DIR/vision-window-fit-run" "$TARGET_DIR/vision-window-fit-run"
install -d -m 0700 "$TARGET_DIR/config/.local/bin" "$TARGET_DIR/config/.config/autostart"
install -m 0755 "$SOURCE_DIR/disable-server-capslock" \
  "$TARGET_DIR/config/.local/bin/disable-server-capslock"
install -m 0644 "$SOURCE_DIR/disable-server-capslock.desktop" \
  "$TARGET_DIR/config/.config/autostart/disable-server-capslock.desktop"
chown 1000:1000 \
  "$TARGET_DIR/config/.local/bin" \
  "$TARGET_DIR/config/.config/autostart" \
  "$TARGET_DIR/config/.local/bin/disable-server-capslock" \
  "$TARGET_DIR/config/.config/autostart/disable-server-capslock.desktop"

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
    docker rm -f "$BROWSER_AGENT_CONTAINER" >/dev/null
  fi
  STACK_MUTATED=true
  compose up -d --remove-orphans --force-recreate
else
  compose up -d --remove-orphans
fi

for _ in $(seq 1 90); do
  if desktop_is_ready; then
    if [[ "$RESTART_APP" == true ]] \
      && systemctl list-unit-files fb-agent.service >/dev/null 2>&1; then
      systemctl restart fb-agent.service
      ensure_cdp_ready
    fi
    if [[ "$MANIFEST_CHANGED" == true ]]; then
      capture_runtime_contract "$POSTCHECK_FILE"
      runtime_contract_matches \
        || die "DISPLAY, geometry, Vision profile or CDP port changed during migration"
    fi
    printf '%s\n' "$manifest_hash" >"$ACTIVE_MANIFEST_FILE"
    trap - ERR

    printf 'Vision desktop is ready through KasmVNC on 127.0.0.1:8444\n'
    exit 0
  fi
  sleep 2
done
die "Kasm desktop stack did not become ready"
