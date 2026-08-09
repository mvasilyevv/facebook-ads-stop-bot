#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
# shellcheck source=scripts/browser-maintenance-lease.sh
source "$SCRIPT_DIR/browser-maintenance-lease.sh"
readonly SOURCE_DIR="$PROJECT_DIR/deploy/vision-webtop"
readonly TARGET_DIR="${VISION_WEBTOP_ROOT:-/opt/vision-webtop}"
readonly COMPOSE_ENV_FILE="${VISION_COMPOSE_ENV_FILE:-$PROJECT_DIR/.env}"
readonly ACTIVE_MANIFEST_FILE="$TARGET_DIR/.production-manifest.sha256"
readonly PLATFORM_STATE_DIR="${FB_AGENT_STATE_DIR:-/opt/fb-agent/shared}"
readonly ROLLBACK_ENV_FILE="${VISION_ROLLBACK_ENV_FILE:-}"
readonly CONTROL_APP_ENV_FILE="${FB_AGENT_CONTROL_APP_ENV_FILE:-$PLATFORM_STATE_DIR/active-app.env}"
readonly CONTROL_APP_COLOR_FILE="${FB_AGENT_CONTROL_APP_COLOR_FILE:-$PLATFORM_STATE_DIR/active-color}"
readonly UPDATE_JOURNAL="$TARGET_DIR/.vision-update.env"
PROFILE_SEED_DIR=""
VALIDATE_PROFILE_SEED_ONLY=false
DEFER_COMMIT=false
RECONCILE_PENDING_UPDATE=false
PLATFORM_NETWORK=""
BOOTSTRAP_CLUSTER_ID=""

ACTIVE_COMPOSE_BACKUP=""
ACTIVE_MANIFEST_BACKUP=""
CONFIG_SNAPSHOT=""
BASELINE_FILE=""
MANIFEST_CHANGED=true
STACK_MUTATED=false
SEEDED_CONFIG=false
UPDATE_PHASE=""
UPDATE_CANDIDATE_HASH=""
UPDATE_PREVIOUS_HASH=""
RECOVER_PREVIOUS_SNAPSHOT=false
RECOVER_PREVIOUS_WITHOUT_SNAPSHOT=false

die() {
  printf 'ERROR: %s\n' "$*" >&2
  if [[ "$STACK_MUTATED" == true || "$SEEDED_CONFIG" == true ]]; then
    rollback 1
  fi
  exit 1
}

compose() {
  compose_with_env "$COMPOSE_ENV_FILE" "$@"
}

compose_with_env() {
  local -r env_file="$1"
  shift
  docker compose \
    --env-file "$env_file" \
    --project-directory "$TARGET_DIR" \
    -f "$TARGET_DIR/compose.yaml" \
    "$@"
}

dotenv_value() {
  local key=$1
  local file="${2:-$COMPOSE_ENV_FILE}"
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

atomic_write_line() {
  local -r destination="$1"
  local -r mode="$2"
  local -r value="$3"
  local temporary=""
  temporary="$(mktemp "${destination}.new.XXXXXXXX")"
  printf '%s\n' "$value" >"$temporary"
  chmod "$mode" "$temporary"
  sync -f "$temporary"
  mv -Tf -- "$temporary" "$destination"
  sync -f "$(dirname -- "$destination")"
}

profile_tree_digest() {
  local -r root="$1"
  tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    --format=gnu --create --file=- --directory="$root" . \
    | sha256sum | awk '{print $1}'
}

bootstrap_marker_file() {
  printf '%s\n' "$TARGET_DIR/config/.fb-agent-seed-bootstrap-v1"
}

write_bootstrap_marker() {
  local -r config_dir="$1"
  local -r seed_digest="$2"
  local -r candidate_hash="$3"
  local marker=""
  local temporary=""
  marker="$config_dir/.fb-agent-seed-bootstrap-v1"
  temporary="$(mktemp "$config_dir/.seed-bootstrap.new.XXXXXXXX")"
  {
    printf 'schema=fb-agent-vision-seed-bootstrap-v1\n'
    printf 'seed_sha256=%s\n' "$seed_digest"
    printf 'candidate_manifest_sha256=%s\n' "$candidate_hash"
  } >"$temporary"
  chmod 0600 "$temporary"
  chown 0:0 "$temporary"
  sync -f "$temporary"
  mv -Tf -- "$temporary" "$marker"
  sync -f "$config_dir"
}

validate_bootstrap_marker() {
  local -r marker="$(bootstrap_marker_file)"
  local seed_digest=""
  local candidate_hash=""
  [[ -f "$marker" && ! -L "$marker" ]] || return 1
  [[ "$(stat -Lc '%a:%u:%g' "$marker")" == "600:0:0" ]] || return 1
  [[ "$(dotenv_value schema "$marker")" == "fb-agent-vision-seed-bootstrap-v1" ]] \
    || return 1
  seed_digest="$(dotenv_value seed_sha256 "$marker")"
  candidate_hash="$(dotenv_value candidate_manifest_sha256 "$marker")"
  [[ "$seed_digest" =~ ^[0-9a-f]{64}$ \
    && "$candidate_hash" =~ ^[0-9a-f]{64}$ ]]
}

remove_bootstrap_marker() {
  local marker=""
  marker="$(bootstrap_marker_file)"
  rm -f -- "$marker"
  sync -f "$TARGET_DIR/config"
}

write_update_journal() {
  local -r phase="$1"
  local -r candidate_hash="$2"
  local -r previous_hash="$3"
  local -r snapshot="$4"
  local -r baseline="$5"
  local temporary=""
  temporary="$(mktemp "${UPDATE_JOURNAL}.new.XXXXXXXX")"
  {
    printf 'schema=fb-agent-vision-update-v1\n'
    printf 'phase=%s\n' "$phase"
    printf 'candidate_manifest_sha256=%s\n' "$candidate_hash"
    printf 'previous_manifest_sha256=%s\n' "$previous_hash"
    printf 'snapshot=%s\n' "$snapshot"
    printf 'baseline=%s\n' "$baseline"
  } >"$temporary"
  chmod 0600 "$temporary"
  sync -f "$temporary"
  mv -Tf -- "$temporary" "$UPDATE_JOURNAL"
  sync -f "$TARGET_DIR"
}

read_update_journal() {
  [[ -f "$UPDATE_JOURNAL" && ! -L "$UPDATE_JOURNAL" \
    && "$(stat -Lc '%a' "$UPDATE_JOURNAL")" == "600" ]] \
    || die "Vision update journal is missing, linked or has unsafe mode"
  [[ "$(dotenv_value schema "$UPDATE_JOURNAL")" == "fb-agent-vision-update-v1" ]] \
    || die "Vision update journal schema is unsupported"
  UPDATE_PHASE="$(dotenv_value phase "$UPDATE_JOURNAL")"
  UPDATE_CANDIDATE_HASH="$(dotenv_value candidate_manifest_sha256 "$UPDATE_JOURNAL")"
  UPDATE_PREVIOUS_HASH="$(dotenv_value previous_manifest_sha256 "$UPDATE_JOURNAL")"
  CONFIG_SNAPSHOT="$(dotenv_value snapshot "$UPDATE_JOURNAL")"
  BASELINE_FILE="$(dotenv_value baseline "$UPDATE_JOURNAL")"
  [[ "$UPDATE_PHASE" == "prepared" || "$UPDATE_PHASE" == "snapshot_ready" ]] \
    || die "Vision update journal phase is invalid"
  [[ "$UPDATE_CANDIDATE_HASH" =~ ^[0-9a-f]{64}$ \
    && "$UPDATE_PREVIOUS_HASH" =~ ^[0-9a-f]{64}$ ]] \
    || die "Vision update journal hashes are invalid"
  [[ "$BASELINE_FILE" == "$TARGET_DIR/backups/"* \
    && -f "$BASELINE_FILE" && ! -L "$BASELINE_FILE" ]] \
    || die "Vision update journal baseline is invalid"
  if [[ "$UPDATE_PHASE" == "snapshot_ready" ]]; then
    [[ "$CONFIG_SNAPSHOT" == "$TARGET_DIR/backups/"* \
      && -f "$CONFIG_SNAPSHOT" && ! -L "$CONFIG_SNAPSHOT" ]] \
      || die "Vision update journal snapshot is invalid"
  else
    [[ -z "$CONFIG_SNAPSHOT" ]] \
      || die "prepared Vision update journal unexpectedly has a snapshot"
  fi
}

load_update_journal() {
  read_update_journal
  [[ "$UPDATE_CANDIDATE_HASH" == "$manifest_hash" ]] \
    || die "another immutable Vision update is pending recovery"
  [[ "$UPDATE_PREVIOUS_HASH" == "$active_manifest_hash" ]] \
    || die "Vision update journal does not match the committed manifest"
}

remove_update_journal() {
  rm -f -- "$UPDATE_JOURNAL"
  sync -f "$TARGET_DIR"
}

control_app_env() {
  [[ -f "$CONTROL_APP_ENV_FILE" ]] \
    || die "committed active application environment is required"
  printf '%s\n' "$CONTROL_APP_ENV_FILE"
}

api_base_url() {
  local color=""
  [[ -f "$CONTROL_APP_COLOR_FILE" ]] \
    || die "active blue/green application release is required"
  color="$(<"$CONTROL_APP_COLOR_FILE")"
  case "$color" in
    blue) printf 'http://127.0.0.1:18100\n' ;;
    green) printf 'http://127.0.0.1:28100\n' ;;
    *) die "invalid active-color state: $color" ;;
  esac
}

service_is_healthy() {
  local -r service="$1"
  local -r env_file="${2:-$COMPOSE_ENV_FILE}"
  local container_id=""
  container_id="$(compose_with_env "$env_file" ps -q "$service")"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] \
    && [[ "$(docker inspect "$container_id" --format '{{.State.Health.Status}}')" == "healthy" ]]
}

ensure_images_available() {
  local -r env_file="${1:-$COMPOSE_ENV_FILE}"
  local image=""
  local missing_image=false
  while IFS= read -r image; do
    [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || return 1
    if ! docker image inspect "$image" >/dev/null 2>&1; then
      missing_image=true
    fi
  done < <(compose_with_env "$env_file" config --images)
  if [[ "$missing_image" == true ]]; then
    compose_with_env "$env_file" pull
  fi
}

assert_database_quiescent() {
  browser_maintenance_checkpoint \
    || die "Vision mutation requires a renewable lease and quiescent browser control plane"
}

capture_runtime_contract() {
  local destination=$1
  local api_key=""
  local api_url=""
  api_key="$(sed -n 's/^API_KEY=//p' "$(control_app_env)" | tail -n 1)"
  api_url="$(api_base_url)"
  [[ -n "$api_key" ]] || die "API_KEY is missing; cannot capture Vision contract"
  {
    printf 'display=:1\n'
    docker exec vision-webtop sh -eu -c \
      'DISPLAY=:1 xdpyinfo | awk '\''/dimensions:/{print "dimensions=" $2; exit}'\'''
    curl --silent --show-error --fail --max-time 25 \
      --header "X-API-Key: $api_key" \
      "$api_url/api/settings/vision"
    printf '\n'
  } >"$destination"
  chmod 0600 "$destination"
}

validate_profile_seed() {
  local canonical=""
  local invalid=""
  [[ "$PROFILE_SEED_DIR" = /* && "$PROFILE_SEED_DIR" != *".."* ]] \
    || die "fresh desktop requires --profile-seed-dir with a safe absolute path"
  [[ -d "$PROFILE_SEED_DIR" && ! -L "$PROFILE_SEED_DIR" ]] \
    || die "desktop profile seed must be a real directory: $PROFILE_SEED_DIR"
  canonical="$(readlink -f "$PROFILE_SEED_DIR")"
  [[ "$canonical" == "$PROFILE_SEED_DIR" ]] \
    || die "desktop profile seed path must already be canonical"
  [[ "$(stat -Lc '%a' "$PROFILE_SEED_DIR")" == "700" ]] \
    || die "desktop profile seed root must have mode 700"
  [[ "$(stat -Lc '%u:%g' "$PROFILE_SEED_DIR")" == "0:0" ]] \
    || die "desktop profile seed root must be owned by root:root"
  [[ -f "$PROFILE_SEED_DIR/.fb-agent-vision-profile-v1" \
    && ! -L "$PROFILE_SEED_DIR/.fb-agent-vision-profile-v1" ]] \
    || die "desktop profile seed marker is missing"
  [[ "$(stat -Lc '%a' "$PROFILE_SEED_DIR/.fb-agent-vision-profile-v1")" == "600" ]] \
    || die "desktop profile seed marker must have mode 600"
  [[ "$(<"$PROFILE_SEED_DIR/.fb-agent-vision-profile-v1")" == \
    "fb-agent-vision-profile-v1" ]] \
    || die "desktop profile seed marker has an unsupported version"
  invalid="$(find "$PROFILE_SEED_DIR" -mindepth 1 \
    \( -type l -o \( ! -type d ! -type f \) \
      -o ! -uid 0 -o ! -gid 0 -o -perm /022 \) -print -quit)"
  [[ -z "$invalid" ]] \
    || die "desktop profile seed contains an unsafe or non-root-owned entry: $invalid"
}

desktop_is_ready() {
  local -r env_file="${1:-$COMPOSE_ENV_FILE}"
  local anonymous_status=""
  local user=""
  local password=""
  user="$(dotenv_value DESKTOP_KASM_SERVICE_USER "$env_file")"
  password="$(dotenv_value DESKTOP_KASM_SERVICE_PASSWORD "$env_file")"
  anonymous_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
    --max-time 3 http://127.0.0.1:8444/ || true)"
  compose_with_env "$env_file" ps -q webtop >/dev/null \
    && service_is_healthy webtop "$env_file" \
    && [[ "$anonymous_status" == "401" ]] \
    && curl --silent --fail --max-time 3 \
      --user "$user:$password" http://127.0.0.1:8444/ >/dev/null \
    && docker exec vision-webtop sh -eu -c \
      'DISPLAY=:1 xdpyinfo | grep -Eq "dimensions:[[:space:]]+1366x768" \
        && pgrep -f "X(kasmvnc|vnc).*:1" >/dev/null \
        && pgrep -x Vision >/dev/null'
}

vision_identity_is_exact() {
  local -r env_file="${1:-$COMPOSE_ENV_FILE}"
  local webtop_id=""
  local expected_image=""
  local expected_cluster=""
  local expected_release=""
  local inspection=""
  webtop_id="$(compose_with_env "$env_file" ps -q webtop)"
  expected_image="$(dotenv_value DESKTOP_WEBTOP_IMAGE "$env_file")"
  expected_cluster="$(dotenv_value FB_AGENT_BOOTSTRAP_CLUSTER_ID "$env_file")"
  expected_release="${FB_AGENT_VISION_RELEASE_ID:-}"
  [[ -n "$webtop_id" && "$webtop_id" != *$'\n'* ]] || return 1
  inspection="$(docker inspect --format \
    '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}|{{.Config.Image}}|{{index .Config.Labels "com.fb-agent.managed"}}|{{index .Config.Labels "com.fb-agent.cluster-id"}}|{{index .Config.Labels "com.fb-agent.purpose"}}|{{index .Config.Labels "com.fb-agent.release"}}' \
    "$webtop_id")" || return 1
  [[ "$inspection" == \
    "true|healthy|${expected_image}|true|${expected_cluster}|vision|${expected_release}" ]]
}

assert_browser_agent_absent() {
  local container_ids=""
  container_ids="$(docker ps -a \
    --filter label=com.docker.compose.project=fb_agent_desktop \
    --filter label=com.docker.compose.service=browser-agent \
    --format '{{.ID}}')"
  [[ -z "$container_ids" ]] \
    || die "browser-agent must be stopped and removed before Vision reconciliation"
}

# shellcheck disable=SC2317,SC2329 # EXIT trap callback is intentionally indirect.
cleanup() {
  [[ -z "$ACTIVE_COMPOSE_BACKUP" ]] || rm -f -- "$ACTIVE_COMPOSE_BACKUP"
  [[ -z "$ACTIVE_MANIFEST_BACKUP" ]] || rm -f -- "$ACTIVE_MANIFEST_BACKUP"
}
trap cleanup EXIT

mark_desktop_rollback_failed() {
  local -r failure="$1"
  python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
    --state-root "$PLATFORM_STATE_DIR" --failure "$failure" >/dev/null 2>&1 || true
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-desktop \
    "CRITICAL Vision desktop rollback failed: $failure" >/dev/null 2>&1 || true
}

restore_config_snapshot() {
  [[ "$CONFIG_SNAPSHOT" == "$TARGET_DIR/backups/"* \
    && -f "$CONFIG_SNAPSHOT" && ! -L "$CONFIG_SNAPSHOT" ]] \
    || return 1
  if [[ -e "$TARGET_DIR/config" || -L "$TARGET_DIR/config" ]]; then
    [[ -d "$TARGET_DIR/config" && ! -L "$TARGET_DIR/config" ]] || return 1
    find "$TARGET_DIR/config" -mindepth 1 -delete || return 1
    rmdir "$TARGET_DIR/config" || return 1
  fi
  tar --extract --gzip --file "$CONFIG_SNAPSHOT" --directory "$TARGET_DIR" || return 1
  [[ -d "$TARGET_DIR/config" && ! -L "$TARGET_DIR/config" ]] || return 1
  sync -f "$TARGET_DIR"
}

rollback() {
  # shellcheck disable=SC2319 # ERR trap preserves the triggering status here.
  local exit_code="${1:-$?}"
  local rollback_failed=false
  trap - ERR
  printf 'ERROR: Kasm desktop update failed; restoring compose and exact /config snapshot\n' >&2
  if ! browser_maintenance_checkpoint; then
    mark_desktop_rollback_failed "vision_inner_rollback_fence_lost"
    exit 70
  fi
  # A snapshot_ready journal proves that a previous process already crossed
  # the stop/snapshot boundary.  After a host crash its candidate containers
  # may already be running even though this process has not set STACK_MUTATED
  # yet.  Always stop that stack before replacing /config from the snapshot.
  if [[ "$STACK_MUTATED" == true || -n "$CONFIG_SNAPSHOT" ]]; then
    compose down --remove-orphans --timeout 90 >/dev/null 2>&1 \
      || rollback_failed=true
  fi
  if [[ -n "$CONFIG_SNAPSHOT" ]]; then
    restore_config_snapshot || rollback_failed=true
  fi
  if [[ -n "$ACTIVE_COMPOSE_BACKUP" && -f "$ACTIVE_COMPOSE_BACKUP" ]]; then
    install -m 0600 "$ACTIVE_COMPOSE_BACKUP" "$TARGET_DIR/compose.yaml" \
      || rollback_failed=true
  fi
  if [[ -n "$ACTIVE_MANIFEST_BACKUP" && -f "$ACTIVE_MANIFEST_BACKUP" ]]; then
    install -m 0600 "$ACTIVE_MANIFEST_BACKUP" "$ACTIVE_MANIFEST_FILE" \
      || rollback_failed=true
  else
    rm -f -- "$ACTIVE_MANIFEST_FILE" || rollback_failed=true
  fi
  if [[ -n "$ROLLBACK_ENV_FILE" && "$rollback_failed" == false ]]; then
    if ensure_images_available "$ROLLBACK_ENV_FILE" \
      && compose_with_env "$ROLLBACK_ENV_FILE" up -d --remove-orphans \
      && {
        for _ in $(seq 1 90); do
          if desktop_is_ready "$ROLLBACK_ENV_FILE" \
            && vision_identity_is_exact "$ROLLBACK_ENV_FILE"; then
            break
          fi
          sleep 2
        done
        desktop_is_ready "$ROLLBACK_ENV_FILE" \
          && vision_identity_is_exact "$ROLLBACK_ENV_FILE"
      }; then
      :
    else
      rollback_failed=true
    fi
  fi
  if [[ "$rollback_failed" == true ]]; then
    mark_desktop_rollback_failed "vision_inner_rollback_nonconvergent"
    exit 70
  fi
  if [[ -n "$CONFIG_SNAPSHOT" ]]; then
    remove_update_journal
  fi
  exit "$exit_code"
}

while (($#)); do
  case "$1" in
    --profile-seed-dir) PROFILE_SEED_DIR="${2:?missing value}"; shift 2 ;;
    --validate-profile-seed-only) VALIDATE_PROFILE_SEED_ONLY=true; shift ;;
    --defer-commit) DEFER_COMMIT=true; shift ;;
    --reconcile-pending-update) RECONCILE_PENDING_UPDATE=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
for command in \
  awk chmod chown cp curl date dirname docker find grep install logger mktemp \
  mv python3 readlink rm rmdir sed seq sha256sum sleep sort stat sync tail tar \
  timeout; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
if [[ "$VALIDATE_PROFILE_SEED_ONLY" == true ]]; then
  validate_profile_seed
  printf 'Desktop profile seed contract validated\n'
  exit 0
fi
[[ "${FB_AGENT_VISION_RELEASE_ID:-}" =~ ^[A-Za-z0-9._-]{1,128}$ ]] \
  || die "Vision Compose requires an immutable release identity"
browser_maintenance_adopt "${FB_AGENT_BROWSER_MAINTENANCE_OWNER:-}" \
  || die "Vision installer requires the caller's durable browser maintenance lease"
assert_database_quiescent
assert_browser_agent_absent
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable"
[[ -s "$COMPOSE_ENV_FILE" ]] || die "release environment is missing: $COMPOSE_ENV_FILE"
[[ "$(stat -Lc '%a' "$COMPOSE_ENV_FILE")" == "600" ]] || die "$COMPOSE_ENV_FILE must have mode 600"
if [[ -n "$ROLLBACK_ENV_FILE" ]]; then
  [[ -f "$ROLLBACK_ENV_FILE" && ! -L "$ROLLBACK_ENV_FILE" \
    && "$(stat -Lc '%a' "$ROLLBACK_ENV_FILE")" == "600" ]] \
    || die "Vision rollback environment must be a mode-600 regular file"
fi

for key in DESKTOP_WEBTOP_IMAGE; do
  image="$(dotenv_value "$key")"
  [[ "$image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || die "$key must be an immutable image@sha256 reference"
done
PLATFORM_NETWORK="$(dotenv_value PLATFORM_NETWORK)"
PLATFORM_NETWORK="${PLATFORM_NETWORK:-fb_agent_safety_first_platform}"
[[ "$PLATFORM_NETWORK" == "fb_agent_safety_first_platform" ]] \
  || die "Vision requires the canonical safety-first platform network"
BOOTSTRAP_CLUSTER_ID="$(dotenv_value FB_AGENT_BOOTSTRAP_CLUSTER_ID)"
[[ "$BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] \
  || die "Vision release has no durable bootstrap cluster identity"
export PLATFORM_NETWORK
docker network inspect "$PLATFORM_NETWORK" >/dev/null 2>&1 \
  || die "canonical safety-first platform network is missing"
network_owner="$(docker network inspect --format \
  '{{index .Labels "com.fb-agent.cluster-id"}}' "$PLATFORM_NETWORK")"
network_contract="$(docker network inspect --format \
  '{{index .Labels "com.fb-agent.network-contract"}}' "$PLATFORM_NETWORK")"
[[ "$network_owner" == "$BOOTSTRAP_CLUSTER_ID" \
  && "$network_contract" == "safety-first-v1" ]] \
  || die "platform network is not owned by this safety-first cluster"

manifest_hash="$({
  sha256sum \
    "$SOURCE_DIR/compose.yaml" \
    "$SOURCE_DIR/Dockerfile" \
    "$SOURCE_DIR/entrypoint.sh" \
    "$SOURCE_DIR/healthcheck.sh" \
    "$SOURCE_DIR/kasmvnc.yaml" \
    "$SOURCE_DIR/vision-window-fit.sh" \
    "$SOURCE_DIR/THIRD_PARTY_NOTICES.md" \
    "$SOURCE_DIR/kasm-client/package.json" \
    "$SOURCE_DIR/kasm-client/package-lock.json" \
    "$SOURCE_DIR/kasm-client/apply-patch.mjs" \
    "$SOURCE_DIR/kasm-client/fb-agent-client.js" \
    "$SOURCE_DIR/kasm-client/fb-agent-client.css" \
    "$SOURCE_DIR/kasm-client/vite.config.js"
  {
    sed -n \
      -e '/^DESKTOP_WEBTOP_IMAGE=/p' \
      -e '/^DESKTOP_KASM_SERVICE_USER=/p' \
      -e '/^DESKTOP_KASM_SERVICE_PASSWORD=/p' \
      "$COMPOSE_ENV_FILE"
    printf 'FB_AGENT_VISION_RELEASE_ID=%s\n' "${FB_AGENT_VISION_RELEASE_ID:-}"
  } | sort | sha256sum
} | sha256sum | awk '{print $1}')"

install -d -m 0700 "$TARGET_DIR"
active_manifest_hash=""
if [[ -e "$ACTIVE_MANIFEST_FILE" || -L "$ACTIVE_MANIFEST_FILE" ]]; then
  [[ -f "$ACTIVE_MANIFEST_FILE" && ! -L "$ACTIVE_MANIFEST_FILE" ]] \
    || die "desktop runtime manifest must be a regular file"
  [[ "$(stat -Lc '%a' "$ACTIVE_MANIFEST_FILE")" == "600" ]] \
    || die "desktop runtime manifest must have mode 600"
  active_manifest_hash="$(<"$ACTIVE_MANIFEST_FILE")"
  [[ "$active_manifest_hash" =~ ^[0-9a-f]{64}$ ]] \
    || die "desktop runtime manifest is invalid"
fi

if [[ -e "$TARGET_DIR/config" || -L "$TARGET_DIR/config" ]]; then
  [[ -d "$TARGET_DIR/config" && ! -L "$TARGET_DIR/config" ]] \
    || die "persistent desktop config must be a real directory"
  [[ "$(stat -Lc '%a' "$TARGET_DIR/config")" == "700" ]] \
    || die "persistent desktop config root must have mode 700"
fi
if [[ ! -d "$TARGET_DIR/config" ]]; then
  local_seed_before=""
  local_seed_after=""
  staged_seed_digest=""
  seed_staging="$(mktemp -d "$TARGET_DIR/.seed-config.XXXXXXXX")"
  validate_profile_seed
  local_seed_before="$(profile_tree_digest "$PROFILE_SEED_DIR")"
  if ! cp -a -- "$PROFILE_SEED_DIR/." "$seed_staging/"; then
    die "desktop profile seed copy failed"
  fi
  local_seed_after="$(profile_tree_digest "$PROFILE_SEED_DIR")"
  staged_seed_digest="$(profile_tree_digest "$seed_staging")"
  [[ "$local_seed_before" == "$local_seed_after" \
    && "$local_seed_before" == "$staged_seed_digest" ]] \
    || die "desktop profile seed changed while its snapshot was created"
  chown -R 1000:1000 "$seed_staging"
  chmod 0700 "$seed_staging"
  write_bootstrap_marker "$seed_staging" "$local_seed_before" "$manifest_hash"
  sync -f "$seed_staging"
  mv -- "$seed_staging" "$TARGET_DIR/config"
  sync -f "$TARGET_DIR"
  SEEDED_CONFIG=true
elif [[ -z "$active_manifest_hash" ]]; then
  validate_bootstrap_marker \
    || die "unmanaged desktop config cannot resume clean bootstrap"
  marker_seed_digest="$(dotenv_value seed_sha256 "$(bootstrap_marker_file)")"
  marker_candidate_hash="$(
    dotenv_value candidate_manifest_sha256 "$(bootstrap_marker_file)"
  )"
  [[ "$marker_candidate_hash" == "$manifest_hash" ]] \
    || die "pending desktop seed belongs to another immutable release"
  if [[ "$RECONCILE_PENDING_UPDATE" == false ]]; then
    validate_profile_seed
    [[ "$marker_seed_digest" == "$(profile_tree_digest "$PROFILE_SEED_DIR")" ]] \
      || die "pending desktop seed no longer matches its root-owned source"
  fi
  SEEDED_CONFIG=true
fi

if [[ "$active_manifest_hash" == "$manifest_hash" ]]; then
  MANIFEST_CHANGED=false
fi

if [[ "$RECONCILE_PENDING_UPDATE" == true ]]; then
  if [[ -e "$UPDATE_JOURNAL" || -L "$UPDATE_JOURNAL" ]]; then
    read_update_journal
    if [[ "$manifest_hash" == "$UPDATE_CANDIDATE_HASH" ]]; then
      [[ "$active_manifest_hash" == "$UPDATE_PREVIOUS_HASH" \
        || "$active_manifest_hash" == "$UPDATE_CANDIDATE_HASH" ]] \
        || die "pending Vision candidate does not follow the committed manifest"
      if desktop_is_ready && vision_identity_is_exact; then
        atomic_write_line "$ACTIVE_MANIFEST_FILE" 0600 "$manifest_hash"
        validate_bootstrap_marker && remove_bootstrap_marker
        remove_update_journal
        printf 'Pending Vision candidate committed from active desktop state\n'
        exit 0
      fi
      printf 'Pending Vision candidate is not running; resuming its durable update\n' >&2
      RECONCILE_PENDING_UPDATE=false
    elif [[ "$manifest_hash" == "$UPDATE_PREVIOUS_HASH" ]]; then
      [[ "$active_manifest_hash" == "$UPDATE_PREVIOUS_HASH" \
        || "$active_manifest_hash" == "$UPDATE_CANDIDATE_HASH" ]] \
        || die "pending Vision rollback does not follow the committed manifest"
      if [[ "$UPDATE_PHASE" == "snapshot_ready" ]]; then
        RECOVER_PREVIOUS_SNAPSHOT=true
      else
        # prepared is written before the first reversible compose stop.  No
        # profile mutation has occurred yet, so previous may be restarted from
        # the current exact config even if the host crashed before snapshot.
        RECOVER_PREVIOUS_WITHOUT_SNAPSHOT=true
      fi
      MANIFEST_CHANGED=false
    else
      die "active desktop state matches neither side of the pending Vision update"
    fi
  elif [[ "$active_manifest_hash" == "$manifest_hash" ]]; then
    if desktop_is_ready && vision_identity_is_exact; then
      validate_bootstrap_marker && remove_bootstrap_marker
      exit 0
    fi
    printf 'Committed Vision runtime is not running; restoring it from active state\n' >&2
    RECONCILE_PENDING_UPDATE=false
  elif [[ -z "$active_manifest_hash" ]] \
    && validate_bootstrap_marker \
    && [[ "$(dotenv_value candidate_manifest_sha256 "$(bootstrap_marker_file)")" \
      == "$manifest_hash" ]]; then
    if desktop_is_ready && vision_identity_is_exact; then
      atomic_write_line "$ACTIVE_MANIFEST_FILE" 0600 "$manifest_hash"
      remove_bootstrap_marker
      exit 0
    fi
    printf 'Pending fresh Vision runtime is not running; resuming bootstrap\n' >&2
    RECONCILE_PENDING_UPDATE=false
  else
    die "no recoverable Vision update matches the active desktop state"
  fi
fi

if [[ -e "$UPDATE_JOURNAL" || -L "$UPDATE_JOURNAL" ]]; then
  [[ -f "$UPDATE_JOURNAL" && ! -L "$UPDATE_JOURNAL" ]] \
    || die "Vision update journal must be a regular file"
  if [[ "$RECOVER_PREVIOUS_SNAPSHOT" == false \
    && "$RECOVER_PREVIOUS_WITHOUT_SNAPSHOT" == false ]]; then
    journal_candidate="$(dotenv_value candidate_manifest_sha256 "$UPDATE_JOURNAL")"
    if [[ "$MANIFEST_CHANGED" == false \
      && "$journal_candidate" == "$manifest_hash" ]]; then
      remove_update_journal
    else
      load_update_journal
    fi
  fi
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
  browser_maintenance_assert_held \
    || die "browser maintenance lease expired before Vision mutation"
  assert_database_quiescent
  install -d -m 0700 "$TARGET_DIR/backups"
  if [[ "$SEEDED_CONFIG" == false ]]; then
    if [[ -z "$BASELINE_FILE" ]]; then
      BASELINE_FILE="$(mktemp "$TARGET_DIR/backups/pre-desktop-baseline.XXXXXXXX")"
      capture_runtime_contract "$BASELINE_FILE"
      sync -f "$BASELINE_FILE"
      write_update_journal \
        prepared "$manifest_hash" "$active_manifest_hash" "" "$BASELINE_FILE"
      UPDATE_PHASE=prepared
    fi
  fi
fi

install -m 0600 "$SOURCE_DIR/compose.yaml" "$TARGET_DIR/compose.yaml"

trap rollback ERR
compose config --quiet
while IFS= read -r image; do
  [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] \
    || die "desktop image is not digest-pinned: $image"
done < <(compose config --images)

if [[ "$RECOVER_PREVIOUS_SNAPSHOT" == true \
  || "$RECOVER_PREVIOUS_WITHOUT_SNAPSHOT" == true ]]; then
  ensure_images_available || die "immutable Vision desktop image is unavailable"
  assert_database_quiescent
  STACK_MUTATED=true
  compose stop --timeout 90
  assert_database_quiescent
  if [[ "$RECOVER_PREVIOUS_SNAPSHOT" == true ]]; then
    restore_config_snapshot \
      || die "pending Vision rollback could not restore the exact profile snapshot"
  fi
  assert_database_quiescent
  compose down --remove-orphans --timeout 90
  [[ -z "$(compose ps -q)" ]] \
    || die "Vision desktop container did not stop for pending rollback recovery"
  atomic_write_line "$ACTIVE_MANIFEST_FILE" 0600 "$manifest_hash"
  remove_update_journal
  active_manifest_hash="$manifest_hash"
fi

if [[ "$MANIFEST_CHANGED" == true ]]; then
  ensure_images_available || die "immutable Vision desktop image is unavailable"
  assert_database_quiescent
  STACK_MUTATED=true
  # Stop is reversible and keeps the previous container definitions intact.
  # The durable exact profile snapshot and snapshot_ready journal are persisted
  # before the destructive `compose down`, eliminating the prepared/down crash
  # hole that previously made rollback impossible after reboot.
  compose stop --timeout 90
  assert_database_quiescent
  while IFS= read -r stopped_container; do
    [[ -z "$stopped_container" ]] && continue
    [[ "$(docker inspect --format '{{.State.Running}}' "$stopped_container")" == false ]] \
      || die "Vision desktop container is still running before profile snapshot"
  done < <(compose ps -q)
  if [[ "$SEEDED_CONFIG" == false ]]; then
    if [[ "$UPDATE_PHASE" == "snapshot_ready" ]]; then
      restore_config_snapshot \
        || die "durable pre-update desktop profile snapshot could not be restored"
    else
      snapshot_temporary="$(mktemp "$TARGET_DIR/backups/.config-snapshot.XXXXXXXX")"
      CONFIG_SNAPSHOT="$TARGET_DIR/backups/pre-desktop-config-$(
        date -u +%Y%m%dT%H%M%SZ
      )-${manifest_hash:0:12}.tar.gz"
      tar --create --gzip --file "$snapshot_temporary" \
        --directory "$TARGET_DIR" config
      chmod 0600 "$snapshot_temporary"
      sync -f "$snapshot_temporary"
      mv -Tf -- "$snapshot_temporary" "$CONFIG_SNAPSHOT"
      sync -f "$TARGET_DIR/backups"
      write_update_journal \
        snapshot_ready "$manifest_hash" "$active_manifest_hash" \
        "$CONFIG_SNAPSHOT" "$BASELINE_FILE"
      UPDATE_PHASE=snapshot_ready
    fi
  fi
  assert_database_quiescent
  compose down --remove-orphans --timeout 90
  [[ -z "$(compose ps -q)" ]] \
    || die "Vision desktop container did not stop after durable profile snapshot"
fi

# Remove files from the retired split runtime only after the rollback snapshot
# is durable. The immutable entrypoint now owns Caps Lock and window fit.
rm -f -- \
  "$TARGET_DIR/vision-service-run" \
  "$TARGET_DIR/vision-window-fit-run" \
  "$TARGET_DIR/config/.local/bin/disable-server-capslock" \
  "$TARGET_DIR/config/.config/autostart/disable-server-capslock.desktop"

assert_database_quiescent
if [[ "$MANIFEST_CHANGED" == true ]]; then
  compose up -d --remove-orphans --force-recreate
else
  compose up -d --remove-orphans
fi

for _ in $(seq 1 90); do
  if desktop_is_ready && vision_identity_is_exact; then
    assert_database_quiescent
    if [[ "$DEFER_COMMIT" == false ]]; then
      atomic_write_line "$ACTIVE_MANIFEST_FILE" 0600 "$manifest_hash"
      validate_bootstrap_marker && remove_bootstrap_marker
      [[ ! -e "$UPDATE_JOURNAL" && ! -L "$UPDATE_JOURNAL" ]] \
        || remove_update_journal
    fi
    trap - ERR

    if [[ "$DEFER_COMMIT" == true ]]; then
      printf 'Vision desktop is ready; manifest commit is deferred to desktop state\n'
    else
      printf 'Vision desktop is ready on 127.0.0.1:8444\n'
    fi
    exit 0
  fi
  sleep 2
done
die "Vision desktop did not become ready"
