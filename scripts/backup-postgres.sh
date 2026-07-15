#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly RELEASE_DIR="${FB_AGENT_RELEASE_DIR:-$ROOT_DIR/current}"
readonly BACKUP_DIR="$ROOT_DIR/backups/postgres"
readonly LOCK_FILE="$ROOT_DIR/shared/backup.lock"
readonly PROJECT_NAME="${COMPOSE_PROJECT_NAME:-fb_agent}"
readonly RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

[[ -d "$RELEASE_DIR" ]] || { printf 'ERROR: release directory is missing\n' >&2; exit 1; }
mkdir -p "$BACKUP_DIR" "$(dirname -- "$LOCK_FILE")"
exec 9>"$LOCK_FILE"
flock -n 9 || { printf 'ERROR: another backup is already running\n' >&2; exit 1; }

release_id="$(<"$RELEASE_DIR/.release-id")"
export IMAGE_TAG="$release_id"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
target="$BACKUP_DIR/fb-agent-$timestamp.dump"
temporary="$target.partial"
compose=(docker compose -p "$PROJECT_NAME" --env-file "$RELEASE_DIR/.env" -f "$RELEASE_DIR/docker-compose.yml")

cleanup() { rm -f -- "$temporary"; }
trap cleanup EXIT

"${compose[@]}" exec -T postgres sh -eu -c \
  'export PGPASSWORD="$POSTGRES_PASSWORD"; exec pg_dump --format=custom --compress=6 --no-owner --no-acl --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  >"$temporary"
[[ -s "$temporary" ]] || { printf 'ERROR: pg_dump produced an empty backup\n' >&2; exit 1; }
mv -- "$temporary" "$target"
sha256sum "$target" >"$target.sha256"
find "$BACKUP_DIR" -type f \( -name '*.dump' -o -name '*.dump.sha256' \) -mtime "+$RETENTION_DAYS" -delete
trap - EXIT
printf 'Postgres backup created: %s\n' "$target"
