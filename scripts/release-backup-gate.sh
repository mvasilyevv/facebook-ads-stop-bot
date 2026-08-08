#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
RELEASE_ENV=""
APP_ENV=""
BACKUP_ENV=""
CONFIG_FILE=""
EVIDENCE_ROOT=""
ACCEPTED_DIR=""
ATTEMPT_DIR=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[release-backup-gate] %s\n' "$*" >&2; }
cleanup() {
  local -r exit_code=$?
  trap - EXIT
  if ((exit_code != 0)) && [[ -n "$ATTEMPT_DIR" && -d "$ATTEMPT_DIR" ]]; then
    find "$ATTEMPT_DIR" -mindepth 1 -delete
    rmdir "$ATTEMPT_DIR"
  fi
  exit "$exit_code"
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --release-env) RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --app-env) APP_ENV="${2:?missing value}"; shift 2 ;;
    --backup-env) BACKUP_ENV="${2:?missing value}"; shift 2 ;;
    --config-file) CONFIG_FILE="${2:?missing value}"; shift 2 ;;
    --evidence-root) EVIDENCE_ROOT="${2:?missing value}"; shift 2 ;;
    --accepted-dir) ACCEPTED_DIR="${2:?missing value}"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done

for command in dirname find install mktemp mv python3 rmdir sed stat; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
for file in "$RELEASE_ENV" "$APP_ENV" "$BACKUP_ENV" "$CONFIG_FILE"; do
  [[ -f "$file" && ! -L "$file" ]] || die "required regular file is missing: $file"
done
[[ "$EVIDENCE_ROOT" = /* && "$EVIDENCE_ROOT" != *".."* ]] \
  || die "evidence root must be a safe absolute path"
release_id="$(sed -n 's/^RELEASE_ID=//p' "$RELEASE_ENV" | tail -n 1)"
[[ "$release_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "release manifest has invalid RELEASE_ID"

install -d -m 0700 "$EVIDENCE_ROOT"
[[ ! -L "$EVIDENCE_ROOT" && "$(stat -Lc '%a' "$EVIDENCE_ROOT")" == 700 ]] \
  || die "release backup evidence root must be a mode-700 real directory"
if [[ -n "$ACCEPTED_DIR" ]]; then
  [[ "$ACCEPTED_DIR" = /* && "$ACCEPTED_DIR" != *".."* ]] \
    || die "accepted evidence directory must be a safe absolute path"
  [[ "$(dirname -- "$ACCEPTED_DIR")" == "$EVIDENCE_ROOT" ]] \
    || die "accepted evidence directory must be one direct child of evidence root"
  if [[ -d "$ACCEPTED_DIR" && ! -L "$ACCEPTED_DIR" ]]; then
    python3 "$SCRIPT_DIR/backup-adoption-evidence.py" validate-pair \
      --full "$ACCEPTED_DIR/full.json" \
      --restore "$ACCEPTED_DIR/restore.json" \
      --expected-release-id "$release_id" \
      --max-age-seconds 14400 \
      --require-pitr-marker
    log "reusing still-fresh immutable accepted evidence: $ACCEPTED_DIR"
    exit 0
  fi
  [[ ! -e "$ACCEPTED_DIR" && ! -L "$ACCEPTED_DIR" ]] \
    || die "accepted evidence path must be absent or a real directory"
fi
ATTEMPT_DIR="$(mktemp -d "$EVIDENCE_ROOT/.attempt-${release_id}-XXXXXXXX")"
full_evidence="$ATTEMPT_DIR/full.json"
restore_evidence="$ATTEMPT_DIR/restore.json"

"$SCRIPT_DIR/pgbackrest-admin.sh" \
  --release-env "$RELEASE_ENV" \
  --app-env "$APP_ENV" \
  --backup-env "$BACKUP_ENV" \
  --config-file "$CONFIG_FILE" \
  --evidence "$full_evidence" \
  full
backup_set="$(python3 "$SCRIPT_DIR/backup-adoption-evidence.py" \
  evidence-full-label --full "$full_evidence")"

"$SCRIPT_DIR/pgbackrest-restore-drill.sh" \
  --release-env "$RELEASE_ENV" \
  --app-env "$APP_ENV" \
  --backup-env "$BACKUP_ENV" \
  --config-file "$CONFIG_FILE" \
  --backup-set "$backup_set" \
  --prove-post-backup-wal \
  --evidence "$restore_evidence"

python3 "$SCRIPT_DIR/backup-adoption-evidence.py" validate-pair \
  --full "$full_evidence" \
  --restore "$restore_evidence" \
  --expected-release-id "$release_id" \
  --max-age-seconds 14400 \
  --require-pitr-marker
if [[ -n "$ACCEPTED_DIR" ]]; then
  mv "$ATTEMPT_DIR" "$ACCEPTED_DIR"
  ATTEMPT_DIR=""
  log "full backup, post-backup WAL replay and isolated restore accepted: $ACCEPTED_DIR"
else
  attempt_name="${ATTEMPT_DIR##*/}"
  evidence_dir="$EVIDENCE_ROOT/accepted-${attempt_name#.attempt-}"
  mv "$ATTEMPT_DIR" "$evidence_dir"
  ATTEMPT_DIR=""
  log "full backup, post-backup WAL replay and isolated restore accepted: $evidence_dir"
fi
