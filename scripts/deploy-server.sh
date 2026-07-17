#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
TARGET="${DEPLOY_TARGET:-root@62.60.150.133}"
ROOT_DIR="${DEPLOY_ROOT:-/opt/fb-agent}"
ENV_FILE="$PROJECT_DIR/.env"
PUBLIC_URL="${PUBLIC_URL:-https://app.adpulse.su}"
DESKTOP_WEBTOP_IMAGE_OVERRIDE="${DESKTOP_WEBTOP_IMAGE:-}"
DESKTOP_DOCKER_CONFIG_OVERRIDE="${DESKTOP_DOCKER_CONFIG:-}"
RELEASE_ID=""
ALLOW_VISION_OFFLINE=false
DRY_RUN=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { [[ -n "${TEMP_DIR:-}" ]] && rm -rf -- "$TEMP_DIR"; }
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --host) TARGET="${2:?missing value for --host}"; shift 2 ;;
    --root) ROOT_DIR="${2:?missing value for --root}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?missing value for --env-file}"; shift 2 ;;
    --public-url) PUBLIC_URL="${2:?missing value for --public-url}"; shift 2 ;;
    --release) RELEASE_ID="${2:?missing value for --release}"; shift 2 ;;
    --allow-vision-offline) ALLOW_VISION_OFFLINE=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

for command in ssh rsync python3 git; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -f "$ENV_FILE" ]] || die "environment file not found: $ENV_FILE"
[[ "$ROOT_DIR" == /* ]] || die "--root must be an absolute path"
if [[ -n "$DESKTOP_DOCKER_CONFIG_OVERRIDE" ]]; then
  [[ "$DESKTOP_DOCKER_CONFIG_OVERRIDE" =~ ^/[-A-Za-z0-9._/]+$ ]] \
    || die "DESKTOP_DOCKER_CONFIG must be a safe absolute path"
fi
if [[ -z "$RELEASE_ID" ]]; then
  RELEASE_ID="$(date -u +%Y%m%dT%H%M%SZ)-$(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
fi
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid release id"

TEMP_DIR="$(mktemp -d)"
PROD_ENV="$TEMP_DIR/production.env"
ENV_SOURCE="$ENV_FILE"
if ssh "$TARGET" "test -f '$ROOT_DIR/shared/.env'"; then
  ENV_SOURCE="$TEMP_DIR/server.env"
  rsync -a "$TARGET:$ROOT_DIR/shared/.env" "$ENV_SOURCE"
  chmod 600 "$ENV_SOURCE"
  printf 'Using the existing server environment as the secret source.\n'
fi
prepare_env_args=(
  --input "$ENV_SOURCE"
  --output "$PROD_ENV"
  --public-url "$PUBLIC_URL"
  --generate-postgres-password-if-insecure
)
if [[ -n "$DESKTOP_WEBTOP_IMAGE_OVERRIDE" ]]; then
  prepare_env_args+=(--desktop-webtop-image "$DESKTOP_WEBTOP_IMAGE_OVERRIDE")
fi
python3 "$SCRIPT_DIR/prepare_production_env.py" "${prepare_env_args[@]}"

remote_release="$ROOT_DIR/releases/$RELEASE_ID"
printf 'Deployment target: %s:%s\n' "$TARGET" "$remote_release"
if [[ "$DRY_RUN" == true ]]; then
  rsync -an --delete \
    --exclude '/.git/' --exclude '/.env' --exclude '/.venv/' --exclude 'node_modules/' \
    --exclude '/data/' --exclude '/.logs/' --exclude 'coverage/' --exclude 'dist/' \
    --exclude '__pycache__/' --exclude '*.pyc' --exclude '/.pytest_cache/' \
    "$PROJECT_DIR/" "$TARGET:$remote_release/"
  printf 'Dry run completed; no remote files were changed.\n'
  exit 0
fi

ssh "$TARGET" "install -d -m 700 '$ROOT_DIR/shared' '$ROOT_DIR/releases' '$ROOT_DIR/backups/postgres' '$remote_release'"
rsync -a --delete \
  --exclude '/.git/' --exclude '/.env' --exclude '/.venv/' --exclude 'node_modules/' \
  --exclude '/data/' --exclude '/.logs/' --exclude 'coverage/' --exclude 'dist/' \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude '/.pytest_cache/' \
  "$PROJECT_DIR/" "$TARGET:$remote_release/"
rsync -a "$PROD_ENV" "$TARGET:$ROOT_DIR/shared/.env.new"
ssh "$TARGET" "set -eu; chmod 600 '$ROOT_DIR/shared/.env.new'; mv '$ROOT_DIR/shared/.env.new' '$ROOT_DIR/shared/.env'; printf '%s\\n' '$RELEASE_ID' > '$remote_release/.release-id'; ln -sfn '$ROOT_DIR/shared/.env' '$remote_release/.env'; chmod +x '$remote_release'/scripts/*.sh"

# The desktop host and API routes are one release contract. Bring the private
# Vision/Guacamole stack to a healthy state before switching Caddy/app release;
# the installer rolls back its compose/image if any desktop health gate fails.
if [[ -n "$DESKTOP_DOCKER_CONFIG_OVERRIDE" ]]; then
  ssh "$TARGET" \
    "DOCKER_CONFIG='$DESKTOP_DOCKER_CONFIG_OVERRIDE' '$remote_release/scripts/install-vision-webtop.sh'"
else
  ssh "$TARGET" "'$remote_release/scripts/install-vision-webtop.sh'"
fi

if [[ "$ALLOW_VISION_OFFLINE" == true ]]; then
  ssh "$TARGET" \
    "FB_AGENT_ROOT='$ROOT_DIR' '$remote_release/scripts/server-release.sh' --allow-vision-offline"
else
  ssh "$TARGET" "FB_AGENT_ROOT='$ROOT_DIR' '$remote_release/scripts/server-release.sh'"
fi
# Site/systemd-файлы входят в каждый release и должны обновляться вместе с ним.
# Installer валидирует новый Caddyfile и атомарно возвращает предыдущий при ошибке.
ssh "$TARGET" "'$ROOT_DIR/current/scripts/install-server-units.sh'"
printf 'Deployment completed: %s\n' "$RELEASE_ID"
