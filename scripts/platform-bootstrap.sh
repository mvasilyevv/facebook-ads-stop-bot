#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
readonly COMPOSE_FILE="$PROJECT_DIR/deploy/compose/docker-compose.infra.yml"
readonly STATE_DIR="${FB_AGENT_STATE_DIR:-${FB_AGENT_ROOT:-/opt/fb-agent}/shared}"
readonly BOOTSTRAP_SECRETS="$STATE_DIR/bootstrap-secrets.env"
readonly BOOTSTRAP_STATE="$STATE_DIR/bootstrap-state.json"
RELEASE_ENV=""
APP_ENV=""
BACKUP_ENV=""
REQUIRE_EMPTY=false
DRY_RUN=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[platform-bootstrap] %s\n' "$*" >&2; }

usage() {
  printf '%s\n' \
    "Usage: $0 --release-env FILE --app-env FILE --backup-env FILE [--require-empty] [--dry-run]"
}

dotenv_value() {
  local -r file="$1"
  local -r key="$2"
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

validate_digest() {
  local -r key="$1"
  local value=""
  value="$(dotenv_value "$RELEASE_ENV" "$key")"
  [[ "$value" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || die "$key must be an immutable image@sha256 reference"
}

while (($#)); do
  case "$1" in
    --release-env) RELEASE_ENV="${2:?missing --release-env value}"; shift 2 ;;
    --app-env) APP_ENV="${2:?missing --app-env value}"; shift 2 ;;
    --backup-env) BACKUP_ENV="${2:?missing --backup-env value}"; shift 2 ;;
    --require-empty) REQUIRE_EMPTY=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

for command in docker grep python3 sed stat; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable"
for file in "$RELEASE_ENV" "$APP_ENV" "$BACKUP_ENV" "$BOOTSTRAP_SECRETS"; do
  [[ -f "$file" ]] || die "required file is missing: $file"
done
PGBACKREST_CONFIG_FILE="${PGBACKREST_CONFIG_FILE:-$STATE_DIR/pgbackrest.conf}"
[[ -f "$PGBACKREST_CONFIG_FILE" && ! -L "$PGBACKREST_CONFIG_FILE" ]] \
  || die "stable pgBackRest config is missing: $PGBACKREST_CONFIG_FILE"
export PGBACKREST_CONFIG_FILE
[[ "$(stat -Lc '%a' "$APP_ENV")" == "600" ]] || die "$APP_ENV must have mode 600"
[[ "$(stat -Lc '%a' "$BACKUP_ENV")" == "600" ]] || die "$BACKUP_ENV must have mode 600"
[[ ! -L "$BOOTSTRAP_SECRETS" && "$(stat -Lc '%a' "$BOOTSTRAP_SECRETS")" == "600" ]] \
  || die "$BOOTSTRAP_SECRETS must be a regular file with mode 600"
validate_digest POSTGRES_IMAGE
validate_digest REDIS_IMAGE

readonly DEFAULT_POSTGRES_VOLUME="fb_agent_safety_first_pgdata"
readonly DEFAULT_REDIS_VOLUME="fb_agent_safety_first_redisdata"
readonly DEFAULT_PGBACKREST_SPOOL_VOLUME="fb_agent_safety_first_pgbackrest_spool"
readonly DEFAULT_PGBACKREST_REPO_VOLUME="fb_agent_safety_first_pgbackrest_repo"
readonly DEFAULT_CAMPAIGN_UPLOAD_VOLUME="fb_agent_safety_first_campaign_uploads"
readonly DEFAULT_PLATFORM_NETWORK="fb_agent_safety_first_platform"
release_postgres_volume="$(dotenv_value "$RELEASE_ENV" POSTGRES_VOLUME)"
release_redis_volume="$(dotenv_value "$RELEASE_ENV" REDIS_VOLUME)"
release_spool_volume="$(dotenv_value "$RELEASE_ENV" PGBACKREST_SPOOL_VOLUME)"
release_repo_volume="$(dotenv_value "$RELEASE_ENV" PGBACKREST_REPO_VOLUME)"
release_upload_volume="$(dotenv_value "$RELEASE_ENV" CAMPAIGN_UPLOAD_VOLUME)"
release_platform_network="$(dotenv_value "$RELEASE_ENV" PLATFORM_NETWORK)"
POSTGRES_VOLUME="${POSTGRES_VOLUME:-${release_postgres_volume:-$DEFAULT_POSTGRES_VOLUME}}"
REDIS_VOLUME="${REDIS_VOLUME:-${release_redis_volume:-$DEFAULT_REDIS_VOLUME}}"
PGBACKREST_SPOOL_VOLUME="${PGBACKREST_SPOOL_VOLUME:-${release_spool_volume:-$DEFAULT_PGBACKREST_SPOOL_VOLUME}}"
PGBACKREST_REPO_VOLUME="${PGBACKREST_REPO_VOLUME:-${release_repo_volume:-$DEFAULT_PGBACKREST_REPO_VOLUME}}"
CAMPAIGN_UPLOAD_VOLUME="${CAMPAIGN_UPLOAD_VOLUME:-${release_upload_volume:-$DEFAULT_CAMPAIGN_UPLOAD_VOLUME}}"
PLATFORM_NETWORK="${PLATFORM_NETWORK:-${release_platform_network:-$DEFAULT_PLATFORM_NETWORK}}"
for resource in \
  "$POSTGRES_VOLUME" \
  "$REDIS_VOLUME" \
  "$PGBACKREST_SPOOL_VOLUME" \
  "$PGBACKREST_REPO_VOLUME" \
  "$CAMPAIGN_UPLOAD_VOLUME" \
  "$PLATFORM_NETWORK"; do
  [[ "$resource" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]+$ ]] \
    || die "platform resource has an unsafe name: $resource"
  [[ "$resource" == fb_agent_safety_first_* ]] \
    || die "fresh bootstrap refuses a resource outside the safety-first namespace: $resource"
done
for resource_contract in \
  "$POSTGRES_VOLUME:$DEFAULT_POSTGRES_VOLUME" \
  "$REDIS_VOLUME:$DEFAULT_REDIS_VOLUME" \
  "$PGBACKREST_SPOOL_VOLUME:$DEFAULT_PGBACKREST_SPOOL_VOLUME" \
  "$PGBACKREST_REPO_VOLUME:$DEFAULT_PGBACKREST_REPO_VOLUME" \
  "$CAMPAIGN_UPLOAD_VOLUME:$DEFAULT_CAMPAIGN_UPLOAD_VOLUME" \
  "$PLATFORM_NETWORK:$DEFAULT_PLATFORM_NETWORK"; do
  actual_resource="${resource_contract%%:*}"
  expected_resource="${resource_contract#*:}"
  [[ "$actual_resource" == "$expected_resource" ]] \
    || die "fresh bootstrap requires canonical safety-first resource $expected_resource"
done
export POSTGRES_VOLUME REDIS_VOLUME PGBACKREST_SPOOL_VOLUME PGBACKREST_REPO_VOLUME
export CAMPAIGN_UPLOAD_VOLUME PLATFORM_NETWORK

BOOTSTRAP_CLUSTER_ID="$(dotenv_value "$BOOTSTRAP_SECRETS" FB_AGENT_BOOTSTRAP_CLUSTER_ID)"
BOOTSTRAP_POSTGRES_PASSWORD="$(dotenv_value "$BOOTSTRAP_SECRETS" POSTGRES_PASSWORD)"
[[ "$BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] \
  || die "durable bootstrap cluster id is invalid"
[[ ${#BOOTSTRAP_POSTGRES_PASSWORD} -ge 16 ]] \
  || die "durable bootstrap PostgreSQL password is invalid"
[[ "$(dotenv_value "$APP_ENV" FB_AGENT_BOOTSTRAP_CLUSTER_ID)" == "$BOOTSTRAP_CLUSTER_ID" ]] \
  || die "candidate environment belongs to a different bootstrap cluster"
[[ "$(dotenv_value "$APP_ENV" POSTGRES_PASSWORD)" == "$BOOTSTRAP_POSTGRES_PASSWORD" ]] \
  || die "candidate PostgreSQL password differs from durable bootstrap state"
export FB_AGENT_BOOTSTRAP_CLUSTER_ID="$BOOTSTRAP_CLUSTER_ID"

if docker ps --filter label=com.docker.compose.project=fb_agent \
  --format '{{.Label "com.docker.compose.service"}}' \
  | grep -Eq '^(postgres|redis)$'; then
  die "unsupported pre-platform data containers are running; provision a clean host or stop them explicitly"
fi

export APP_ENV_FILE="$APP_ENV"
export BACKUP_ENV_FILE="$BACKUP_ENV"
readonly PROJECT_NAME="${INFRA_PROJECT_NAME:-fb_agent_infra}"
compose=(docker compose -p "$PROJECT_NAME" --env-file "$RELEASE_ENV" -f "$COMPOSE_FILE")

if [[ "$DRY_RUN" == true ]]; then
  "${compose[@]}" config --quiet
  while IFS= read -r image; do
    [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || die "non-immutable image: $image"
  done < <("${compose[@]}" config --images)
  log "configuration is valid; no Docker state changed"
  exit 0
fi

ensure_platform_network() {
  if ! docker network inspect "$PLATFORM_NETWORK" >/dev/null 2>&1; then
    docker network create \
      --label "com.fb-agent.cluster-id=$BOOTSTRAP_CLUSTER_ID" \
      --label "com.fb-agent.network-contract=safety-first-v1" \
      "$PLATFORM_NETWORK" >/dev/null
  fi
  python3 "$SCRIPT_DIR/platform-network-inventory.py" \
    --cluster-id "$BOOTSTRAP_CLUSTER_ID" \
    --network "$PLATFORM_NETWORK" \
    --phase bootstrap
}

ensure_owned_volume() {
  local -r volume="$1"
  local -r purpose="$2"
  local owner="" actual_purpose=""
  if ! docker volume inspect "$volume" >/dev/null 2>&1; then
    docker volume create \
      --label "com.fb-agent.cluster-id=$BOOTSTRAP_CLUSTER_ID" \
      --label "com.fb-agent.volume-purpose=$purpose" \
      "$volume" >/dev/null
    return
  fi
  owner="$(docker volume inspect --format \
    '{{index .Labels "com.fb-agent.cluster-id"}}' "$volume")"
  actual_purpose="$(docker volume inspect --format \
    '{{index .Labels "com.fb-agent.volume-purpose"}}' "$volume")"
  [[ "$owner" == "$BOOTSTRAP_CLUSTER_ID" && "$actual_purpose" == "$purpose" ]] \
    || die "existing volume $volume is not owned by this safety-first cluster"
}

ensure_platform_network
ensure_owned_volume "$POSTGRES_VOLUME" postgres
ensure_owned_volume "$REDIS_VOLUME" redis
ensure_owned_volume "$PGBACKREST_SPOOL_VOLUME" pgbackrest-spool
ensure_owned_volume "$PGBACKREST_REPO_VOLUME" pgbackrest-repo
ensure_owned_volume "$CAMPAIGN_UPLOAD_VOLUME" campaign-uploads

"${compose[@]}" config --quiet
"${compose[@]}" pull
if ! "${compose[@]}" up -d redis; then
  log "WARNING: optional Redis failed to start; continuing with PostgreSQL"
fi
"${compose[@]}" up -d --wait --wait-timeout 240 postgres
require_empty_sql=false
[[ "$REQUIRE_EMPTY" == false ]] || require_empty_sql=true
# The host-side bootstrap consumes the same generated target, catalog,
# extension and partition guard as Alembic and the locked migrator.  No shell
# copy of schema identities is allowed to drift from the Python contract.
# shellcheck disable=SC2016
if ! python3 "$PROJECT_DIR/migrations/baseline_contract.py" \
    --render-platform-psql-guard \
  | "${compose[@]}" exec -T \
      --env "FB_REQUIRE_EMPTY=$require_empty_sql" \
      --env "FB_BOOTSTRAP_CLUSTER_ID=$BOOTSTRAP_CLUSTER_ID" \
      --user postgres postgres sh -eu -c \
    'exec psql --no-psqlrc --set=ON_ERROR_STOP=1 \
      --set=require_empty="$FB_REQUIRE_EMPTY" \
      --set=expected_cluster_id="$FB_BOOTSTRAP_CLUSTER_ID" \
      --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"'; then
  die "PostgreSQL volume $POSTGRES_VOLUME failed the shared safety-first database guard"
fi
python3 "$PROJECT_DIR/scripts/bootstrap-state.py" record-owned \
  --state "$BOOTSTRAP_STATE" \
  --cluster-id "$BOOTSTRAP_CLUSTER_ID" \
  --postgres-volume "$POSTGRES_VOLUME" \
  --platform-network "$PLATFORM_NETWORK" >/dev/null
log "PostgreSQL volume $POSTGRES_VOLUME passed the owned fresh-target guard"
"${compose[@]}" exec -T --user postgres postgres \
  pgbackrest --stanza=fb-agent stanza-create
"${compose[@]}" exec -T --user postgres postgres \
  pgbackrest --stanza=fb-agent check
log "durable infra is ready and pgBackRest archive-push is verified"
