#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
ENV_FILE="$PROJECT_DIR/.env"
ALLOW_VISION_OFFLINE=false
readonly VISION_CONTAINER="${VISION_CONTAINER:-vision-webtop}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

vision_api_ready() {
  # В production Vision API слушает loopback собственного network namespace.
  # Published host-port может принимать TCP и сразу reset'ить HTTP, хотя API жив
  # и доступен browser-agent, который разделяет namespace контейнера Vision.
  if curl --silent --max-time 5 --output /dev/null http://127.0.0.1:3030/ 2>/dev/null; then
    return 0
  fi

  local running=""
  running="$(docker inspect --format '{{.State.Running}}' "$VISION_CONTAINER" 2>/dev/null)" || true
  if [[ "$running" == "true" ]] && docker exec "$VISION_CONTAINER" \
    curl --silent --max-time 5 --output /dev/null http://127.0.0.1:3030/ 2>/dev/null; then
    return 0
  fi
  return 1
}

while (($#)); do
  case "$1" in
    --env-file) ENV_FILE="${2:?missing value for --env-file}"; shift 2 ;;
    --allow-vision-offline) ALLOW_VISION_OFFLINE=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

for command in docker curl flock sha256sum; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is not available"
docker info >/dev/null 2>&1 || die "Docker daemon is not available"
[[ -f "$ENV_FILE" ]] || die "environment file not found: $ENV_FILE"
mode="$(stat -Lc '%a' "$ENV_FILE")"
[[ "$mode" == "600" ]] || die "$ENV_FILE must have mode 600 (actual: $mode)"
python3 "$SCRIPT_DIR/prepare_production_env.py" --input "$ENV_FILE" --validate-only

available_kb="$(df -Pk "$PROJECT_DIR" | awk 'NR==2 {print $4}')"
((available_kb >= 10 * 1024 * 1024)) || die "less than 10 GiB disk space is available"
memory_kb="$(awk '/MemTotal:/ {print $2}' /proc/meminfo)"
((memory_kb >= 4 * 1024 * 1024)) || die "less than 4 GiB RAM is available"
swap_kb="$(awk '/SwapTotal:/ {print $2}' /proc/meminfo)"
((swap_kb > 0)) || warn "swap is disabled"

if ! vision_api_ready; then
  if [[ "$ALLOW_VISION_OFFLINE" == true ]]; then
    warn "Vision API at 127.0.0.1:3030 is offline; application will deploy in safe not-ready state"
  else
    die "Vision API at 127.0.0.1:3030 is offline"
  fi
fi

printf 'Server preflight: OK\n'
