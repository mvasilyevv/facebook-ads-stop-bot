#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
TARGET=""
ROOT_DIR="${DEPLOY_ROOT:-/opt/fb-agent}"
RELEASE_ENV=""
PUBLIC_URL="${PUBLIC_URL:-https://app.adpulse.su}"
readonly CANONICAL_PUBLIC_URL="https://app.adpulse.su"
DOCKER_CONFIG_OVERRIDE="${DEPLOY_DOCKER_CONFIG:-${DESKTOP_DOCKER_CONFIG:-}}"
DRY_RUN=false
TEMP_DIR=""
REMOTE_LOCK=""
REMOTE_LOCK_HELD=false
REMOTE_LOCK_OWNER=""
REMOTE_LOCK_RENEWER_PID=""
REMOTE_LOCK_FAILURE_FILE=""
REMOTE_STAGING=""
readonly REMOTE_LOCK_LEASE_SECONDS=90
readonly REMOTE_LOCK_RENEW_INTERVAL_SECONDS=30
readonly -a REMOTE_LOCK_SSH_OPTIONS=(
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=10
  -o ServerAliveCountMax=2
)

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

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

remote_lock_action() {
  local -r action="$1"
  # Arguments are independently constrained to a canonical path, a lowercase
  # hex owner token, and integers before they reach the remote shell.
  ssh "${REMOTE_LOCK_SSH_OPTIONS[@]}" "$TARGET" bash -s -- \
    "$action" "$ROOT_DIR" "$REMOTE_LOCK_OWNER" "$REMOTE_LOCK_LEASE_SECONDS" \
    <"$SCRIPT_DIR/deploy-lock-remote.sh"
}

remote_lock_publish() {
  local -r staging="$1"
  local -r release="$2"
  ssh "${REMOTE_LOCK_SSH_OPTIONS[@]}" "$TARGET" bash -s -- \
    publish "$ROOT_DIR" "$REMOTE_LOCK_OWNER" "$REMOTE_LOCK_LEASE_SECONDS" \
    "$staging" "$release" \
    <"$SCRIPT_DIR/deploy-lock-remote.sh"
}

cleanup_remote_staging() {
  local -r staging="$1"
  ssh "${REMOTE_LOCK_SSH_OPTIONS[@]}" "$TARGET" \
    timeout 30s bash -s -- "$staging" <<'REMOTE_CLEANUP'
set -Eeuo pipefail
staging="$1"
if [[ -d "$staging" && ! -L "$staging" ]]; then
  find "$staging" -mindepth 1 -delete
  rmdir "$staging"
fi
REMOTE_CLEANUP
}

stop_remote_lock_renewer() {
  if [[ -n "$REMOTE_LOCK_RENEWER_PID" ]]; then
    kill -TERM "$REMOTE_LOCK_RENEWER_PID" >/dev/null 2>&1 || true
    wait "$REMOTE_LOCK_RENEWER_PID" >/dev/null 2>&1 || true
    REMOTE_LOCK_RENEWER_PID=""
  fi
}

start_remote_lock_renewer() {
  local -r parent_pid="$$"
  local -r parent_started="$(
    ps -o lstart= -p "$parent_pid" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
  )"
  [[ -n "$parent_started" ]] || die "deploy owner process identity is unavailable"
  (
    trap - EXIT
    local sleep_pid=""
    local observed_start=""
    parent_is_exact() {
      kill -0 "$parent_pid" >/dev/null 2>&1 || return 1
      observed_start="$(
        ps -o lstart= -p "$parent_pid" 2>/dev/null \
          | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
      )"
      [[ -n "$observed_start" && "$observed_start" == "$parent_started" ]]
    }
    trap '
      if [[ -n "$sleep_pid" ]]; then
        kill -TERM "$sleep_pid" >/dev/null 2>&1 || true
        wait "$sleep_pid" >/dev/null 2>&1 || true
      fi
      exit 0
    ' INT TERM
    while parent_is_exact; do
      sleep "$REMOTE_LOCK_RENEW_INTERVAL_SECONDS" &
      sleep_pid=$!
      wait "$sleep_pid" || exit 0
      sleep_pid=""
      # SIGKILL/OOM cannot run the deploy owner's EXIT trap. The renewer is a
      # direct child. A PID reused by an unrelated process may pass kill -0,
      # but cannot retain the captured process start identity.
      parent_is_exact || exit 0
      if ! remote_lock_action renew >/dev/null 2>&1; then
        printf 'remote deploy lease renewal failed\n' >"$REMOTE_LOCK_FAILURE_FILE"
        kill -TERM "$parent_pid" >/dev/null 2>&1 || true
        exit 1
      fi
    done
  ) &
  REMOTE_LOCK_RENEWER_PID=$!
}

assert_remote_lock_healthy() {
  [[ "$REMOTE_LOCK_HELD" == true ]] || die "remote deploy lease was not acquired"
  [[ ! -e "$REMOTE_LOCK_FAILURE_FILE" ]] \
    || die "remote deploy lease renewal failed"
  remote_lock_action assert >/dev/null \
    || die "remote deploy lease ownership or expiry check failed"
}

cleanup() {
  local -r exit_code=$?
  trap - EXIT INT TERM
  set +e
  stop_remote_lock_renewer
  if [[ -n "$REMOTE_STAGING" ]]; then
    cleanup_remote_staging "$REMOTE_STAGING" >/dev/null 2>&1 || true
  fi
  if [[ "$REMOTE_LOCK_HELD" == true && -n "$REMOTE_LOCK" ]]; then
    # Release is an owner-token CAS. A delayed cleanup can never remove a
    # successor's lease after stale takeover.
    remote_lock_action release >/dev/null 2>&1 || true
  fi
  if [[ -n "$TEMP_DIR" ]]; then
    [[ -z "$REMOTE_LOCK_FAILURE_FILE" ]] \
      || rm -f -- "$REMOTE_LOCK_FAILURE_FILE"
    rm -f -- "$TEMP_DIR/source-manifest.json"
    rmdir -- "$TEMP_DIR" >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

while (($#)); do
  case "$1" in
    --host) TARGET="${2:?missing value}"; shift 2 ;;
    --root) ROOT_DIR="${2:?missing value}"; shift 2 ;;
    --release-env) RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --public-url) PUBLIC_URL="${2:?missing value}"; shift 2 ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

for command in mktemp ps python3 rsync sed sha256sum ssh; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -n "$TARGET" ]] || die "--host is required; there is no implicit deployment target"
[[ "$TARGET" =~ ^[A-Za-z0-9._-]+@[A-Za-z0-9._:-]+$ ]] \
  || die "--host must be an explicit user@host target"
[[ -f "$RELEASE_ENV" ]] || die "immutable release manifest not found: $RELEASE_ENV"
is_canonical_root "$ROOT_DIR" \
  || die "--root must be a canonical non-root absolute path without . or .. segments"
[[ "$PUBLIC_URL" =~ ^https://[A-Za-z0-9._:-]+/?$ ]] \
  || die "--public-url must be a safe HTTPS origin"
[[ "${PUBLIC_URL%/}" == "$CANONICAL_PUBLIC_URL" ]] \
  || die "only the canonical public URL $CANONICAL_PUBLIC_URL is supported"
PUBLIC_URL="$CANONICAL_PUBLIC_URL"
if [[ -n "$DOCKER_CONFIG_OVERRIDE" ]]; then
  [[ "$DOCKER_CONFIG_OVERRIDE" =~ ^/[-A-Za-z0-9._/]+$ ]] || die "unsafe Docker config path"
fi

release_id="$(sed -n 's/^RELEASE_ID=//p' "$RELEASE_ENV" | tail -n 1)"
[[ "$release_id" =~ ^[A-Za-z0-9._-]+$ \
  && "$release_id" != "." && "$release_id" != ".." ]] \
  || die "release manifest has invalid RELEASE_ID"
desktop_webtop_image="$(sed -n 's/^DESKTOP_WEBTOP_IMAGE=//p' "$RELEASE_ENV" | tail -n 1)"
[[ "$desktop_webtop_image" =~ ^[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] \
  || die "desktop release image is not digest-pinned"

remote_release="$ROOT_DIR/releases/$release_id"
readonly -a RSYNC_EXCLUDES=(
  --exclude '/.git/'
  --exclude '/.env'
  --exclude '/.coverage'
  --exclude '/.DS_Store'
  --exclude '.hypothesis/'
  --exclude '.experimental-vitest-cache/'
  --exclude '.mypy_cache/'
  --exclude '/.venv/'
  --exclude '.ruff_cache/'
  --exclude '.tanstack/'
  --exclude '.tox/'
  --exclude 'node_modules/'
  --exclude '/data/'
  --exclude '/.logs/'
  --exclude 'coverage/'
  --exclude 'dist/'
  --exclude 'htmlcov/'
  --exclude 'playwright-report/'
  --exclude 'storybook-static/'
  --exclude 'test-results/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '*.tsbuildinfo'
  --exclude '/.pytest_cache/'
  --exclude '/production.env'
  --exclude '/release-images.env'
  --exclude '/.fb-agent-release'
  --exclude '/.fb-agent-effective-config.sha256'
  --exclude '/.fb-agent-source-manifest.json'
)
TEMP_DIR="$(mktemp -d)"
source_manifest="$TEMP_DIR/source-manifest.json"
REMOTE_LOCK_FAILURE_FILE="$TEMP_DIR/remote-lock-renewal-failed"
python3 "$PROJECT_DIR/scripts/release-state.py" manifest-write \
  --release-dir "$PROJECT_DIR" \
  --manifest "$source_manifest" >/dev/null
source_manifest_sha="$(sha256sum "$source_manifest" | awk '{print $1}')"
release_env_sha="$(sha256sum "$RELEASE_ENV" | awk '{print $1}')"
release_state_sha="$(sha256sum "$PROJECT_DIR/scripts/release-state.py" | awk '{print $1}')"
server_release_sha="$(sha256sum "$PROJECT_DIR/scripts/server-platform-release.sh" | awk '{print $1}')"

if [[ "$DRY_RUN" == true ]]; then
  rsync -an --delete "${RSYNC_EXCLUDES[@]}" \
    "$PROJECT_DIR/" "$TARGET:$remote_release/"
  printf 'Source manifest: %s\n' "$source_manifest_sha"
  printf 'Dry run completed; no remote state changed\n'
  exit 0
fi

REMOTE_LOCK="$ROOT_DIR/shared/.platform-deploy.lock"
REMOTE_LOCK_OWNER="$(
  python3 -c 'import secrets; print(secrets.token_hex(16))'
)"
[[ "$REMOTE_LOCK_OWNER" =~ ^[0-9a-f]{32}$ ]] \
  || die "could not generate a deploy lease owner token"
remote_lock_action acquire >/dev/null
REMOTE_LOCK_HELD=true
start_remote_lock_renewer
assert_remote_lock_healthy

release_status="$(
  # shellcheck disable=SC2029
  ssh "$TARGET" \
    "if test -e '$remote_release' || test -L '$remote_release'; then printf existing; else printf new; fi"
)"
case "$release_status" in
  existing)
    # A repeated RELEASE_ID is a read-only reuse operation. It is accepted only
    # when both reviewed source bytes and the immutable image manifest match.
    # No rsync or release-directory write occurs on this path.
    remote_source_sha="$(
      # shellcheck disable=SC2029
      ssh "$TARGET" \
        "test -f '$remote_release/.fb-agent-source-manifest.json' && test ! -L '$remote_release/.fb-agent-source-manifest.json' && sha256sum '$remote_release/.fb-agent-source-manifest.json' | awk '{print \$1}'"
    )"
    remote_release_env_sha="$(
      # shellcheck disable=SC2029
      ssh "$TARGET" \
        "test -f '$remote_release/release-images.env' && test ! -L '$remote_release/release-images.env' && sha256sum '$remote_release/release-images.env' | awk '{print \$1}'"
    )"
    [[ "$remote_source_sha" == "$source_manifest_sha" ]] \
      || die "RELEASE_ID $release_id already exists with different source content"
    [[ "$remote_release_env_sha" == "$release_env_sha" ]] \
      || die "RELEASE_ID $release_id already exists with a different image manifest"
    # shellcheck disable=SC2029
    ssh "$TARGET" \
      "set -eu; test -f '$remote_release/.fb-agent-release' && test ! -L '$remote_release/.fb-agent-release'; test -x '$remote_release/scripts/server-platform-release.sh'; test \"\$(sha256sum '$remote_release/scripts/release-state.py' | awk '{print \$1}')\" = '$release_state_sha'; test \"\$(sha256sum '$remote_release/scripts/server-platform-release.sh' | awk '{print \$1}')\" = '$server_release_sha'; test \"\$(stat -c '%a' '$remote_release/.fb-agent-release')\" = 400; test \"\$(stat -c '%a' '$remote_release/.fb-agent-effective-config.sha256')\" = 400; test \"\$(stat -c '%a' '$remote_release/.fb-agent-source-manifest.json')\" = 400; test \"\$(stat -c '%a' '$remote_release/release-images.env')\" = 400; test \"\$(stat -c '%a' '$remote_release/production.env')\" = 400; test -L '$remote_release/.env' && test \"\$(readlink '$remote_release/.env')\" = production.env; cd '$remote_release'; sha256sum --check --strict .fb-agent-release >/dev/null; python3 scripts/release-state.py manifest-verify --release-dir '$remote_release' --manifest '$remote_release/.fb-agent-source-manifest.json' --require-read-only >/dev/null"
    ;;
  new)
    remote_staging_candidate="$(
      # shellcheck disable=SC2029
      ssh "$TARGET" "mktemp -d '$ROOT_DIR/releases/.incoming-${release_id}-XXXXXXXX'"
    )"
    staging_prefix="$ROOT_DIR/releases/.incoming-${release_id}-"
    staging_suffix="${remote_staging_candidate#"$staging_prefix"}"
    [[ "$remote_staging_candidate" == "$staging_prefix"* \
      && "$staging_suffix" =~ ^[A-Za-z0-9]{8}$ ]] \
      || die "remote staging directory is outside the release root"
    REMOTE_STAGING="$remote_staging_candidate"
    rsync -a --delete "${RSYNC_EXCLUDES[@]}" \
      "$PROJECT_DIR/" "$TARGET:$REMOTE_STAGING/"
    rsync -a "$source_manifest" \
      "$TARGET:$REMOTE_STAGING/.fb-agent-source-manifest.json"
    rsync -a "$RELEASE_ENV" "$TARGET:$REMOTE_STAGING/release-images.env"
    assert_remote_lock_healthy
    # Application secrets never leave the production host. Render them only in
    # the private staging directory, verify the source tree, then publish the
    # complete release with one same-filesystem rename.
    # shellcheck disable=SC2029
    ssh "$TARGET" \
      "set -eu; test -e '$ROOT_DIR/shared/pgbackrest.env' || install -m 0600 /dev/null '$ROOT_DIR/shared/pgbackrest.env'; test -f '$ROOT_DIR/shared/pgbackrest.env' && test ! -L '$ROOT_DIR/shared/pgbackrest.env' && test \"\$(stat -c '%a' '$ROOT_DIR/shared/pgbackrest.env')\" = 600; chmod 600 '$REMOTE_STAGING/.fb-agent-source-manifest.json' '$REMOTE_STAGING/release-images.env'; test -x '$REMOTE_STAGING/scripts/server-platform-release.sh'; test \"\$(sha256sum '$REMOTE_STAGING/scripts/release-state.py' | awk '{print \$1}')\" = '$release_state_sha'; test \"\$(sha256sum '$REMOTE_STAGING/scripts/server-platform-release.sh' | awk '{print \$1}')\" = '$server_release_sha'; python3 '$REMOTE_STAGING/scripts/release-state.py' manifest-verify --release-dir '$REMOTE_STAGING' --manifest '$REMOTE_STAGING/.fb-agent-source-manifest.json' >/dev/null; python3 '$REMOTE_STAGING/scripts/provision-bootstrap-secrets.py' --input '$ROOT_DIR/shared/.env' --output '$ROOT_DIR/shared/bootstrap-secrets.env' --lock '$ROOT_DIR/shared/bootstrap-secrets.lock' --browser-control-output '$ROOT_DIR/shared/browser-control.env' --browser-maintenance-output '$ROOT_DIR/shared/browser-maintenance.env' --browser-autopause-output '$ROOT_DIR/shared/browser-autopause.env' --browser-meta-api-output '$ROOT_DIR/shared/browser-meta-api.env' --browser-campaign-creator-output '$ROOT_DIR/shared/browser-campaign-creator.env' --browser-authority-output '$ROOT_DIR/shared/browser-authority.env'; python3 '$REMOTE_STAGING/scripts/prepare_production_env.py' --input '$ROOT_DIR/shared/.env' --bootstrap-secrets '$ROOT_DIR/shared/bootstrap-secrets.env' --output '$REMOTE_STAGING/production.env' --public-url '$CANONICAL_PUBLIC_URL' --desktop-webtop-image '$desktop_webtop_image'; chmod 600 '$REMOTE_STAGING/production.env'; cd '$REMOTE_STAGING'; sha256sum production.env | awk '{print \$1}' >.fb-agent-effective-config.sha256; chmod 600 .fb-agent-effective-config.sha256; ln -s 'production.env' .env; sha256sum .fb-agent-source-manifest.json .fb-agent-effective-config.sha256 release-images.env production.env >.fb-agent-release; chmod 600 .fb-agent-release; sha256sum --check --strict .fb-agent-release >/dev/null; chown -hR root:root '$REMOTE_STAGING'; find '$REMOTE_STAGING' -xdev -type f -exec chmod a-w {} +; find '$REMOTE_STAGING' -xdev -type d -exec chmod a-w {} +; python3 '$REMOTE_STAGING/scripts/release-state.py' manifest-verify --release-dir '$REMOTE_STAGING' --manifest '$REMOTE_STAGING/.fb-agent-source-manifest.json' --require-read-only >/dev/null"
    assert_remote_lock_healthy
    remote_lock_publish "$REMOTE_STAGING" "$remote_release" >/dev/null
    REMOTE_STAGING=""
    ;;
  *) die "unexpected remote release status: $release_status" ;;
esac

assert_remote_lock_healthy
if [[ -n "$DOCKER_CONFIG_OVERRIDE" ]]; then
  remote_command="FB_AGENT_ROOT='$ROOT_DIR' DOCKER_CONFIG='$DOCKER_CONFIG_OVERRIDE' '$remote_release/scripts/server-platform-release.sh'"
else
  remote_command="FB_AGENT_ROOT='$ROOT_DIR' '$remote_release/scripts/server-platform-release.sh'"
fi
# shellcheck disable=SC2029
ssh "$TARGET" "$remote_command"
assert_remote_lock_healthy
printf 'Deployment completed: %s\n' "$release_id"
