#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
readonly COMPOSE_FILE="$PROJECT_DIR/deploy/compose/docker-compose.infra.yml"
CONFIG_FILE="${PGBACKREST_CONFIG_FILE:-${FB_AGENT_ROOT:-/opt/fb-agent}/shared/pgbackrest.conf}"
RELEASE_ENV=""
APP_ENV=""
BACKUP_ENV=""
TARGET_TIME=""
BACKUP_SET=""
EVIDENCE=""
EVIDENCE_DIR=""
KEEP=false
DRY_RUN=false
LATEST_PITR=false
PROVE_POST_BACKUP_WAL=false
CREATED=false
TEMP_INFO=""
MARKER_KEY=""
MARKER_TOKEN=""
MARKER_CREATED=false
HOST_METRIC_STARTED=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[restore-drill] %s\n' "$*" >&2; }
record_host_metric() {
  local -r outcome="$1"
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || return 0
  if ! python3 "$SCRIPT_DIR/host_metrics.py" record \
    --operation restore_drill --outcome "$outcome"; then
    log "CRITICAL failed to persist restore_drill host metric ($outcome)"
  fi
}

while (($#)); do
  case "$1" in
    --release-env) RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --app-env) APP_ENV="${2:?missing value}"; shift 2 ;;
    --backup-env) BACKUP_ENV="${2:?missing value}"; shift 2 ;;
    --config-file) CONFIG_FILE="${2:?missing value}"; shift 2 ;;
    --target-time) TARGET_TIME="${2:?missing value}"; shift 2 ;;
    --backup-set) BACKUP_SET="${2:?missing value}"; shift 2 ;;
    --latest-pitr) LATEST_PITR=true; shift ;;
    --prove-post-backup-wal) PROVE_POST_BACKUP_WAL=true; shift ;;
    --evidence) EVIDENCE="${2:?missing value}"; shift 2 ;;
    --evidence-dir) EVIDENCE_DIR="${2:?missing value}"; shift 2 ;;
    --keep) KEEP=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
if [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
  [[ "${FB_AGENT_VERIFIED_RELEASE_SCHEMA}" == "fb-agent-verified-release-exec/v1" \
    && -n "${FB_AGENT_ACTIVE_STATE_DIR:-}" ]] \
    || die "verified application state is invalid"
  RELEASE_ENV="$FB_AGENT_ACTIVE_STATE_DIR/release-images.env"
  APP_ENV="$FB_AGENT_ACTIVE_STATE_DIR/app.env"
fi
[[ -z "$EVIDENCE" || -z "$EVIDENCE_DIR" ]] \
  || die "--evidence and --evidence-dir are mutually exclusive"
[[ "$LATEST_PITR" != true || -z "$BACKUP_SET" ]] \
  || die "--latest-pitr selects the latest recoverable backup; do not combine it with --backup-set"
for file in "$RELEASE_ENV" "$APP_ENV" "$BACKUP_ENV" "$CONFIG_FILE"; do
  [[ -f "$file" ]] || die "required file is missing: $file"
done
if [[ -n "$TARGET_TIME" ]]; then
  [[ "$TARGET_TIME" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}[T\ ][0-9]{2}:[0-9]{2}:[0-9]{2}(Z|[+-][0-9]{2}:[0-9]{2})$ ]] \
    || die "--target-time must be an RFC3339 timestamp with timezone"
fi
POSTGRES_IMAGE="$(sed -n 's/^POSTGRES_IMAGE=//p' "$RELEASE_ENV" | tail -n 1)"
[[ "$POSTGRES_IMAGE" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
  || die "POSTGRES_IMAGE is not digest-pinned"
release_id="$(sed -n 's/^RELEASE_ID=//p' "$RELEASE_ENV" | tail -n 1)"
[[ "$release_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "release manifest has invalid RELEASE_ID"
PRODUCTION_VOLUME="${POSTGRES_VOLUME:-$(sed -n 's/^POSTGRES_VOLUME=//p' "$RELEASE_ENV" | tail -n 1)}"
PRODUCTION_VOLUME="${PRODUCTION_VOLUME:-fb_agent_safety_first_pgdata}"
[[ "$PRODUCTION_VOLUME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]+$ ]] \
  || die "production Postgres volume name is unsafe"
export APP_ENV_FILE="$APP_ENV" BACKUP_ENV_FILE="$BACKUP_ENV"
export PGBACKREST_CONFIG_FILE="$CONFIG_FILE"
infra=(docker compose -p "${INFRA_PROJECT_NAME:-fb_agent_infra}" \
  --env-file "$APP_ENV" --env-file "$RELEASE_ENV" -f "$COMPOSE_FILE")

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
suffix="${timestamp//[^0-9A-Za-z]/}"
VOLUME="fb_agent_restore_drill_${suffix}"
NETWORK="fb_agent_restore_drill_${suffix}"
CONTAINER="fb-agent-restore-drill-${suffix}"
[[ "$VOLUME" =~ ^fb_agent_restore_drill_[0-9A-Za-z]+$ ]] || die "unsafe volume name"
[[ "$NETWORK" =~ ^fb_agent_restore_drill_[0-9A-Za-z]+$ ]] || die "unsafe network name"
[[ "$CONTAINER" =~ ^fb-agent-restore-drill-[0-9A-Za-z]+$ ]] || die "unsafe container name"
if [[ -n "$EVIDENCE_DIR" ]]; then
  [[ "$EVIDENCE_DIR" = /* && -d "$EVIDENCE_DIR" && ! -L "$EVIDENCE_DIR" ]] \
    || die "--evidence-dir must be an existing absolute real directory"
  EVIDENCE="$EVIDENCE_DIR/restore-${timestamp}.json"
fi

# shellcheck disable=SC2016,SC2317,SC2329 # Trap is indirect; quoted vars expand in the container.
cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ "$MARKER_CREATED" == true ]]; then
    if ! "${infra[@]}" exec -T --user postgres postgres sh -eu -c \
      'exec psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
        --set=marker_key="$1" --set=marker_token="$2" \
        --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
      sh "$MARKER_KEY" "$MARKER_TOKEN" <<'SQL' \
      >/dev/null 2>&1
DELETE FROM system_config
WHERE key = :'marker_key' AND value->>'token' = :'marker_token';
SQL
    then
      log "failed to remove production restore marker $MARKER_KEY"
      ((exit_code != 0)) || exit_code=1
    fi
  fi
  [[ -z "$TEMP_INFO" ]] || rm -f -- "$TEMP_INFO"
  if [[ "$CREATED" == true && "$KEEP" != true ]]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker network rm "$NETWORK" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
  fi
  if [[ "$HOST_METRIC_STARTED" == true ]]; then
    if ((exit_code == 0)); then
      record_host_metric success
    else
      record_host_metric failure
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT

if [[ "$DRY_RUN" == true ]]; then
  log "would restore $POSTGRES_IMAGE into isolated volume $VOLUME"
  [[ "$LATEST_PITR" != true ]] || log "would select latest full/diff/incr WAL chain and its PITR time"
  [[ -z "$TARGET_TIME" ]] || log "PITR target: $TARGET_TIME"
  exit 0
fi
HOST_METRIC_STARTED=true
record_host_metric started

TEMP_INFO="$(mktemp)"
docker run --rm --user postgres \
  --env-file "$APP_ENV" --env-file "$BACKUP_ENV" \
  -v "$CONFIG_FILE:/etc/pgbackrest/pgbackrest.conf:ro" \
  --entrypoint pgbackrest "$POSTGRES_IMAGE" \
  --stanza=fb-agent --output=json info >"$TEMP_INFO"
if [[ -z "$BACKUP_SET" ]]; then
  selection="$(python3 "$SCRIPT_DIR/backup-adoption-evidence.py" \
    latest-recoverable --info "$TEMP_INFO")"
  IFS=$'\t' read -r BACKUP_SET LATEST_RECOVERABLE_TIME <<<"$selection"
  [[ "$LATEST_PITR" != true || -n "$TARGET_TIME" ]] || TARGET_TIME="$LATEST_RECOVERABLE_TIME"
fi
[[ "$BACKUP_SET" =~ ^[0-9]{8}-[0-9]{6}F(_[0-9]{8}-[0-9]{6}[DI])?$ ]] \
  || die "invalid explicit recoverable backup set"
BACKUP_STOP_TIME="$(python3 "$SCRIPT_DIR/backup-adoption-evidence.py" \
  backup-details --info "$TEMP_INFO" --backup-set "$BACKUP_SET")"

if [[ "$PROVE_POST_BACKUP_WAL" == true ]]; then
  MARKER_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
  [[ "$MARKER_TOKEN" =~ ^[A-Za-z0-9_-]{20,40}$ ]] || die "failed to generate marker token"
  MARKER_KEY="restore_drill:${MARKER_TOKEN:0:16}"
  # Set before the INSERT so the EXIT trap also cleans up when psql succeeds
  # but output parsing or a later proof step fails.
  MARKER_CREATED=true
  # shellcheck disable=SC2016 # Quoted variables expand in the container shell.
  TARGET_TIME="$("${infra[@]}" exec -T --user postgres postgres sh -eu -c \
    'exec psql --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
      --set=marker_key="$1" --set=marker_token="$2" \
      --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
    sh "$MARKER_KEY" "$MARKER_TOKEN" <<'SQL'
INSERT INTO system_config (key, value, description)
VALUES (
  :'marker_key',
  jsonb_build_object('token', :'marker_token', 'created_at', clock_timestamp()),
  'Ephemeral post-backup WAL/PITR restore proof'
);
SELECT to_char(
  clock_timestamp() AT TIME ZONE 'UTC',
  'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
);
SQL
)"
  TARGET_TIME="${TARGET_TIME//$'\n'/}"
  [[ "$TARGET_TIME" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]+Z$ ]] \
    || die "database did not return a valid post-backup marker time"
  python3 - "$BACKUP_STOP_TIME" "$TARGET_TIME" <<'PY'
from datetime import datetime
import sys

backup = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
target = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
if target <= backup:
    raise SystemExit("PITR marker is not newer than the selected backup")
PY
  # shellcheck disable=SC2016 # Quoted variables expand in the container shell.
  ARCHIVE_WAL="$("${infra[@]}" exec -T --user postgres postgres sh -eu -c \
    'exec psql --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
      --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
      --command="SELECT pg_walfile_name(pg_current_wal_lsn());"')"
  [[ "$ARCHIVE_WAL" =~ ^[0-9A-F]{24}$ ]] || die "current WAL segment is invalid"
  # shellcheck disable=SC2016 # Quoted variables expand in the container shell.
  "${infra[@]}" exec -T --user postgres postgres sh -eu -c \
    'exec psql --no-psqlrc --quiet --set=ON_ERROR_STOP=1 \
      --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
      --command="SELECT pg_switch_wal();"' >/dev/null
  archived=false
  for _ in $(seq 1 60); do
    # shellcheck disable=SC2016 # Quoted variables expand in the container shell.
    archive_state="$("${infra[@]}" exec -T --user postgres postgres sh -eu -c \
      'exec psql --no-psqlrc --quiet --tuples-only --no-align --set=ON_ERROR_STOP=1 \
        --set=required_wal="$1" --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
        --command="SELECT COALESCE(last_archived_wal >= :'"'"'required_wal'"'"', false) FROM pg_stat_archiver;"' \
      sh "$ARCHIVE_WAL")"
    if [[ "$archive_state" == t ]]; then
      archived=true
      break
    fi
    sleep 2
  done
  [[ "$archived" == true ]] || die "post-backup marker WAL was not archived within 120 seconds"
  "${infra[@]}" exec -T --user postgres postgres \
    pgbackrest --stanza=fb-agent check
fi
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

docker volume create "$VOLUME" >/dev/null
docker network create "$NETWORK" >/dev/null
CREATED=true
docker run --rm --user 0:0 -v "$VOLUME:/var/lib/postgresql/data" \
  --entrypoint sh "$POSTGRES_IMAGE" -eu -c \
  'chown -R postgres:postgres /var/lib/postgresql/data && chmod 700 /var/lib/postgresql/data'

restore_args=(pgbackrest --stanza=fb-agent --repo=1 --set="$BACKUP_SET" restore)
if [[ -n "$TARGET_TIME" ]]; then
  restore_args+=(--type=time --target="$TARGET_TIME" --target-action=promote)
fi
docker run --rm --user postgres \
  --env-file "$APP_ENV" --env-file "$BACKUP_ENV" \
  -v "$VOLUME:/var/lib/postgresql/data" \
  -v "$CONFIG_FILE:/etc/pgbackrest/pgbackrest.conf:ro" \
  --entrypoint "${restore_args[0]}" "$POSTGRES_IMAGE" "${restore_args[@]:1}"

docker run -d --name "$CONTAINER" --network "$NETWORK" \
  --env-file "$APP_ENV" --env-file "$BACKUP_ENV" \
  -v "$VOLUME:/var/lib/postgresql/data" \
  -v "$CONFIG_FILE:/etc/pgbackrest/pgbackrest.conf:ro" \
  "$POSTGRES_IMAGE" postgres -c archive_mode=off >/dev/null

for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" sh -eu -c \
    'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
    recovery="$(docker exec "$CONTAINER" sh -eu -c \
      'psql --tuples-only --no-align --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT pg_is_in_recovery();"')"
    if [[ "$recovery" == "f" ]]; then
      revisions="$(docker exec "$CONTAINER" sh -eu -c \
        'psql --tuples-only --no-align --set=ON_ERROR_STOP=1 --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT version_num FROM alembic_version ORDER BY version_num;"')"
      mapfile -t revision_list <<<"$revisions"
      ((${#revision_list[@]} > 0)) || die "restored database has no Alembic revision"
      evidence_args=()
      for revision in "${revision_list[@]}"; do
        [[ "$revision" =~ ^[A-Za-z0-9._-]+$ ]] || die "restored Alembic revision is invalid"
        evidence_args+=(--revision "$revision")
      done
      recovery_setting="$(docker exec "$CONTAINER" sh -eu -c \
        'psql --tuples-only --no-align --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT current_setting('"'"'recovery_target_time'"'"', true);"')"
      marker_observed=false
      if [[ "$MARKER_CREATED" == true ]]; then
        restored_marker="$(docker exec "$CONTAINER" sh -eu -c \
          'psql --tuples-only --no-align --set=ON_ERROR_STOP=1 --set=marker_key="$1" --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command="SELECT value->>'"'"'token'"'"' FROM system_config WHERE key = :'"'"'marker_key'"'"';"' \
          sh "$MARKER_KEY")"
        [[ "$restored_marker" == "$MARKER_TOKEN" ]] \
          || die "restored database does not contain the post-backup WAL marker"
        marker_observed=true
      fi
      mapfile -t observed_mounts < <(docker inspect --format \
        '{{range .Mounts}}{{printf "%s|%s|%s\n" .Name .Type .Destination}}{{end}}' \
        "$CONTAINER")
      ((${#observed_mounts[@]} > 0)) || die "restore container mount inspection was empty"
      if [[ -n "$EVIDENCE" ]]; then
        completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        write_args=(
          write-restore
          --output "$EVIDENCE"
          --release-id "$release_id"
          --backup-set "$BACKUP_SET"
          --started-at "$started_at"
          --completed-at "$completed_at"
          --volume "$VOLUME"
          --network "$NETWORK"
          --container "$CONTAINER"
          --pg-is-in-recovery "$([[ "$recovery" == t ]] && printf true || printf false)"
          --production-volume "$PRODUCTION_VOLUME"
          "${evidence_args[@]}"
        )
        [[ -z "$recovery_setting" ]] || write_args+=(--recovery-target-setting "$recovery_setting")
        for mount in "${observed_mounts[@]}"; do
          write_args+=(--mount "$mount")
        done
        [[ -z "$TARGET_TIME" ]] || write_args+=(--target-time "$TARGET_TIME")
        if [[ "$MARKER_CREATED" == true ]]; then
          write_args+=(
            --marker-key "$MARKER_KEY"
            --marker-token "$MARKER_TOKEN"
            --marker-observed "$marker_observed"
          )
        fi
        python3 "$SCRIPT_DIR/backup-adoption-evidence.py" "${write_args[@]}"
      fi
      log "restore drill succeeded backup_set=$BACKUP_SET volume=$VOLUME"
      [[ "$KEEP" != true ]] || log "isolated resources kept by request: $CONTAINER $VOLUME $NETWORK"
      exit 0
    fi
  fi
  sleep 2
done
docker logs --tail 100 "$CONTAINER" >&2 || true
die "restored PostgreSQL did not become ready within 120 seconds"
