#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

ACTION="${1:-}"
ROOT_DIR="${2:-}"
OWNER_TOKEN="${3:-}"
LEASE_SECONDS="${4:-}"
PUBLISH_SOURCE="${5:-}"
PUBLISH_TARGET="${6:-}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 75
}

is_canonical_root() {
  local -r path="$1"
  local component
  local -a components=()

  [[ "$path" =~ ^/[A-Za-z0-9._/-]+$ ]] || return 1
  [[ "$path" != "/" && "$path" != */ && "$path" != *//* ]] || return 1

  local IFS='/'
  read -r -a components <<<"${path#/}"
  for component in "${components[@]}"; do
    [[ -n "$component" && "$component" != "." && "$component" != ".." ]] \
      || return 1
  done
}

portable_mode() {
  local -r path="$1"
  stat -c '%a' -- "$path" 2>/dev/null || stat -f '%Lp' -- "$path"
}

portable_mtime() {
  local -r path="$1"
  stat -c '%Y' -- "$path" 2>/dev/null || stat -f '%m' -- "$path"
}

require_regular_file() {
  local -r path="$1"
  [[ -f "$path" && ! -L "$path" ]] || die "unsafe deploy-lock file: $path"
}

require_lock_shape() {
  local entry
  local name

  [[ -d "$LOCK_DIR" && ! -L "$LOCK_DIR" ]] \
    || die "deploy lock is not a regular directory"
  [[ "$(portable_mode "$LOCK_DIR")" == 700 ]] \
    || die "deploy lock directory must have mode 700"

  for entry in "$LOCK_DIR"/* "$LOCK_DIR"/.[!.]* "$LOCK_DIR"/..?*; do
    [[ -e "$entry" || -L "$entry" ]] || continue
    name="${entry##*/}"
    case "$name" in
      owner|expires_at) require_regular_file "$entry" ;;
      *) die "deploy lock contains an unexpected entry: $name" ;;
    esac
  done
}

read_lock_metadata() {
  require_lock_shape
  require_regular_file "$OWNER_FILE"
  require_regular_file "$EXPIRES_FILE"
  [[ "$(portable_mode "$OWNER_FILE")" == 600 ]] \
    || die "deploy lock owner must have mode 600"
  [[ "$(portable_mode "$EXPIRES_FILE")" == 600 ]] \
    || die "deploy lock expiry must have mode 600"

  LOCK_OWNER="$(<"$OWNER_FILE")"
  LOCK_EXPIRES_AT="$(<"$EXPIRES_FILE")"
  [[ "$LOCK_OWNER" =~ ^[0-9a-f]{32}$ ]] \
    || die "deploy lock owner token is malformed"
  [[ "$LOCK_EXPIRES_AT" =~ ^(0|[1-9][0-9]*)$ && ${#LOCK_EXPIRES_AT} -le 12 ]] \
    || die "deploy lock expiry is malformed"
}

try_read_lock_metadata() {
  [[ -f "$OWNER_FILE" && ! -L "$OWNER_FILE" ]] || return 1
  [[ -f "$EXPIRES_FILE" && ! -L "$EXPIRES_FILE" ]] || return 1
  [[ "$(portable_mode "$OWNER_FILE")" == 600 ]] || return 1
  [[ "$(portable_mode "$EXPIRES_FILE")" == 600 ]] || return 1

  LOCK_OWNER="$(<"$OWNER_FILE")"
  LOCK_EXPIRES_AT="$(<"$EXPIRES_FILE")"
  [[ "$LOCK_OWNER" =~ ^[0-9a-f]{32}$ ]] || return 1
  [[ "$LOCK_EXPIRES_AT" =~ ^(0|[1-9][0-9]*)$ && ${#LOCK_EXPIRES_AT} -le 12 ]]
}

remove_owned_temp_file() {
  local -r path="$1"
  if [[ -e "$path" || -L "$path" ]]; then
    require_regular_file "$path"
    rm -f -- "$path"
  fi
}

atomic_metadata_write() {
  local -r target="$1"
  local -r value="$2"
  local -r temp="$3"

  remove_owned_temp_file "$temp"
  printf '%s\n' "$value" >"$temp"
  chmod 600 "$temp"
  require_regular_file "$temp"
  mv -f -- "$temp" "$target"
  require_regular_file "$target"
  [[ "$(portable_mode "$target")" == 600 ]] \
    || die "deploy lock metadata must have mode 600"
}

remove_lock_directory() {
  require_lock_shape
  if [[ -e "$OWNER_FILE" || -L "$OWNER_FILE" ]]; then
    require_regular_file "$OWNER_FILE"
    rm -f -- "$OWNER_FILE"
  fi
  if [[ -e "$EXPIRES_FILE" || -L "$EXPIRES_FILE" ]]; then
    require_regular_file "$EXPIRES_FILE"
    rm -f -- "$EXPIRES_FILE"
  fi
  rmdir -- "$LOCK_DIR"
}

write_expiry() {
  local -r expires_at="$1"
  atomic_metadata_write "$EXPIRES_FILE" "$expires_at" "$EXPIRY_TEMP"
}

create_lock() {
  local -r expires_at="$1"
  mkdir -m 700 -- "$LOCK_DIR"
  write_expiry "$expires_at"
  atomic_metadata_write "$OWNER_FILE" "$OWNER_TOKEN" "$OWNER_TEMP"
}

require_publish_paths() {
  local -r source_parent="${PUBLISH_SOURCE%/*}"
  local -r source_name="${PUBLISH_SOURCE##*/}"
  local -r target_parent="${PUBLISH_TARGET%/*}"
  local -r target_name="${PUBLISH_TARGET##*/}"

  [[ "$source_parent" == "$RELEASES_DIR" ]] \
    || die "publish source is outside the releases directory"
  [[ "$source_name" =~ ^\.incoming-[A-Za-z0-9._-]+-[A-Za-z0-9]{8}$ ]] \
    || die "publish source is not a validated staging directory"
  [[ "$target_parent" == "$RELEASES_DIR" ]] \
    || die "publish target is outside the releases directory"
  [[ "$target_name" =~ ^[A-Za-z0-9._-]+$ \
    && "$target_name" != "." && "$target_name" != ".." ]] \
    || die "publish target has an invalid release id"
  [[ -d "$PUBLISH_SOURCE" && ! -L "$PUBLISH_SOURCE" ]] \
    || die "publish source is not a regular directory"
  [[ ! -e "$PUBLISH_TARGET" && ! -L "$PUBLISH_TARGET" ]] \
    || die "publish target already exists"
}

lock_is_incomplete() {
  [[ ! -e "$OWNER_FILE" || ! -e "$EXPIRES_FILE" ]]
}

lock_recovery_window_expired() {
  local lock_mtime
  lock_mtime="$(portable_mtime "$LOCK_DIR")"
  [[ "$lock_mtime" =~ ^(0|[1-9][0-9]*)$ && ${#lock_mtime} -le 12 ]] \
    || die "deploy lock mtime is malformed"
  (( NOW_EPOCH - lock_mtime >= LEASE_SECONDS ))
}

[[ "$ACTION" == "acquire" || "$ACTION" == "renew" \
  || "$ACTION" == "assert" || "$ACTION" == "release" \
  || "$ACTION" == "publish" ]] \
  || die "unsupported deploy-lock action"
if [[ "$ACTION" == "publish" ]]; then
  (($# == 6)) || die "publish requires source and target paths"
else
  (($# == 4)) || die "unexpected deploy-lock arguments"
fi
is_canonical_root "$ROOT_DIR" || die "deployment root is not canonical"
[[ "$OWNER_TOKEN" =~ ^[0-9a-f]{32}$ ]] || die "owner token is malformed"
[[ "$LEASE_SECONDS" =~ ^[0-9]+$ ]] || die "lease duration is malformed"
(( LEASE_SECONDS >= 30 && LEASE_SECONDS <= 3600 )) \
  || die "lease duration must be between 30 and 3600 seconds"
command -v flock >/dev/null 2>&1 || die "flock is required on the deployment host"
command -v timeout >/dev/null 2>&1 || die "timeout is required on the deployment host"

readonly SHARED_DIR="$ROOT_DIR/shared"
readonly RELEASES_DIR="$ROOT_DIR/releases"
readonly LOCK_DIR="$SHARED_DIR/.platform-deploy.lock"
readonly OWNER_FILE="$LOCK_DIR/owner"
readonly EXPIRES_FILE="$LOCK_DIR/expires_at"
readonly OWNER_TEMP="$SHARED_DIR/.platform-deploy-owner-${OWNER_TOKEN}.tmp"
readonly EXPIRY_TEMP="$SHARED_DIR/.platform-deploy-expiry-${OWNER_TOKEN}.tmp"
NOW_EPOCH="$(date +%s)"
readonly NOW_EPOCH
NEW_EXPIRES_AT="$((NOW_EPOCH + LEASE_SECONDS))"
readonly NEW_EXPIRES_AT
LOCK_OWNER=""
LOCK_EXPIRES_AT=""

if [[ "$ACTION" == "acquire" ]]; then
  if [[ -e "$ROOT_DIR" || -L "$ROOT_DIR" ]]; then
    [[ -d "$ROOT_DIR" && ! -L "$ROOT_DIR" ]] \
      || die "deployment root must be a regular directory"
  else
    install -d -m 700 -- "$ROOT_DIR"
  fi
  if [[ -e "$SHARED_DIR" || -L "$SHARED_DIR" ]]; then
    [[ -d "$SHARED_DIR" && ! -L "$SHARED_DIR" ]] \
      || die "shared deployment directory is unsafe"
  else
    install -d -m 700 -- "$SHARED_DIR"
  fi
  if [[ -e "$RELEASES_DIR" || -L "$RELEASES_DIR" ]]; then
    [[ -d "$RELEASES_DIR" && ! -L "$RELEASES_DIR" ]] \
      || die "releases deployment directory is unsafe"
  else
    install -d -m 700 -- "$RELEASES_DIR"
  fi
  [[ "$(portable_mode "$SHARED_DIR")" == 700 ]] \
    || die "shared deployment directory must have mode 700"
  require_regular_file "$SHARED_DIR/.env"
  [[ -s "$SHARED_DIR/.env" && "$(portable_mode "$SHARED_DIR/.env")" == 600 ]] \
    || die "shared production environment must be non-empty with mode 600"
else
  [[ -d "$ROOT_DIR" && ! -L "$ROOT_DIR" ]] \
    || die "deployment root must be a regular directory"
  [[ -d "$SHARED_DIR" && ! -L "$SHARED_DIR" ]] \
    || die "shared deployment directory is unsafe"
  [[ -d "$RELEASES_DIR" && ! -L "$RELEASES_DIR" ]] \
    || die "releases deployment directory is unsafe"
fi

# Every metadata transition is serialized on the already root-owned shared
# directory. The expiring lease remains recoverable even if the caller or its
# SSH connection is killed; the directory flock itself is process-scoped.
exec 9<"$SHARED_DIR"
flock -x -w 10 9

case "$ACTION" in
  acquire)
    if [[ -e "$LOCK_DIR" || -L "$LOCK_DIR" ]]; then
      require_lock_shape
      if lock_is_incomplete; then
        if lock_recovery_window_expired; then
          remove_lock_directory
        else
          die "deploy lock metadata is incomplete and its recovery lease is active"
        fi
      elif ! try_read_lock_metadata; then
        if lock_recovery_window_expired; then
          remove_lock_directory
        else
          die "deploy lock metadata is malformed and its recovery lease is active"
        fi
      else
        if (( LOCK_EXPIRES_AT > NOW_EPOCH )); then
          if [[ "$LOCK_OWNER" != "$OWNER_TOKEN" ]]; then
            die "another deployment owns the active deploy lease"
          fi
          write_expiry "$NEW_EXPIRES_AT"
          printf 'acquired %s\n' "$NEW_EXPIRES_AT"
          exit 0
        fi
        remove_lock_directory
      fi
    fi
    create_lock "$NEW_EXPIRES_AT"
    printf 'acquired %s\n' "$NEW_EXPIRES_AT"
    ;;
  renew)
    read_lock_metadata
    [[ "$LOCK_OWNER" == "$OWNER_TOKEN" ]] \
      || die "deploy lease owner changed before renewal"
    (( LOCK_EXPIRES_AT > NOW_EPOCH )) \
      || die "deploy lease expired before renewal"
    write_expiry "$NEW_EXPIRES_AT"
    printf 'renewed %s\n' "$NEW_EXPIRES_AT"
    ;;
  assert)
    read_lock_metadata
    [[ "$LOCK_OWNER" == "$OWNER_TOKEN" ]] \
      || die "deploy lease is owned by another process"
    (( LOCK_EXPIRES_AT > NOW_EPOCH )) || die "deploy lease has expired"
    printf 'held %s\n' "$LOCK_EXPIRES_AT"
    ;;
  release)
    if [[ ! -e "$LOCK_DIR" && ! -L "$LOCK_DIR" ]]; then
      printf 'already-released\n'
      exit 0
    fi
    read_lock_metadata
    [[ "$LOCK_OWNER" == "$OWNER_TOKEN" ]] \
      || die "deploy lease is owned by another process"
    remove_lock_directory
    remove_owned_temp_file "$OWNER_TEMP"
    remove_owned_temp_file "$EXPIRY_TEMP"
    printf 'released\n'
    ;;
  publish)
    read_lock_metadata
    [[ "$LOCK_OWNER" == "$OWNER_TOKEN" ]] \
      || die "deploy lease is owned by another process"
    (( LOCK_EXPIRES_AT > NOW_EPOCH )) || die "deploy lease has expired"
    require_publish_paths
    mv -- "$PUBLISH_SOURCE" "$PUBLISH_TARGET"
    printf 'published\n'
    ;;
esac
