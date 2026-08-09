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
# shellcheck source=scripts/browser-control-env.sh
source "$SCRIPT_DIR/browser-control-env.sh"
readonly COMPOSE_FILE="$PROJECT_DIR/deploy/compose/docker-compose.desktop-agent.yml"
readonly STATE_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}/shared"
readonly DESKTOP_STATES_DIR="$STATE_DIR/desktop-states"
readonly ACTIVE_DESKTOP_STATE="$STATE_DIR/active-desktop-state"
readonly DESKTOP_READINESS_DIR="$STATE_DIR/desktop-readiness"
readonly DESKTOP_READINESS_STATES="$DESKTOP_READINESS_DIR/states"
readonly ACTIVE_DESKTOP_READINESS="$DESKTOP_READINESS_DIR/active.env"
readonly DESKTOP_TRANSACTION="$STATE_DIR/desktop-transaction.env"
RELEASE_ENV=""
APP_ENV=""
PROFILE_SEED_DIR=""
PREFLIGHT_ONLY=false
ROLLBACK_ONLY=false
DEADLINE_EPOCH=""
FORWARD_DEADLINE_EPOCH=""
readonly ROLLBACK_RESERVE_SECONDS=60
PREVIOUS_STATE=""
PREVIOUS_MANIFEST=""
PREVIOUS_APP_ENV=""
PREVIOUS_RELEASE_DIR=""
PREVIOUS_VISION_ENV=""
CANDIDATE_STATE=""
JOURNAL_CANDIDATE_STATE=""
JOURNAL_PREVIOUS_STATE=""
ROLLBACK_ARMED=false
ROLLBACK_IN_PROGRESS=false
PRESERVE_MAINTENANCE_LEASE=false
STATE_COMMITTED=false
TRANSACTION_PREPARED=false
EXPECTED_PROFILE_ID=""
EXPECTED_CONFIGURATION_REVISION=""
EXPECTED_BROWSER_CONTRACT_VERSION=""
CANDIDATE_BROWSER_CONTRACT_VERSION=""

die() {
  printf 'ERROR: %s\n' "$*" >&2
  if [[ "$ROLLBACK_ARMED" == true ]]; then
    rollback 1
  fi
  exit 1
}
log() { printf '[desktop-release] %s\n' "$*" >&2; }

phase_deadline_epoch() {
  if [[ "$ROLLBACK_IN_PROGRESS" == true || "$ROLLBACK_ONLY" == true ]]; then
    printf '%s\n' "$DEADLINE_EPOCH"
  else
    printf '%s\n' "$FORWARD_DEADLINE_EPOCH"
  fi
}

remaining_phase_seconds() {
  local deadline=""
  local now=""
  local remaining=0
  # Registry preflight occurs before public cutover is armed and is bounded by
  # its caller. The absolute deadline applies to every later runtime action.
  if [[ "$PREFLIGHT_ONLY" == true ]]; then
    printf '600\n'
    return 0
  fi
  deadline="$(phase_deadline_epoch)"
  now="$(date +%s)"
  remaining=$((deadline - now))
  ((remaining > 0)) || return 1
  printf '%s\n' "$remaining"
}

require_phase_deadline() {
  local -r label="$1"
  remaining_phase_seconds >/dev/null \
    || {
      printf 'ERROR: desktop %s refused after the absolute %s deadline\n' \
        "$label" \
        "$([[ "$ROLLBACK_IN_PROGRESS" == true ]] && printf rollback || printf forward)" \
        >&2
      return 70
    }
}

timeout_cap() {
  local -r requested="$1"
  local remaining=""
  remaining="$(remaining_phase_seconds)" || return 1
  if ((remaining < requested)); then
    printf '%s\n' "$remaining"
  else
    printf '%s\n' "$requested"
  fi
}

run_before_deadline() {
  local -r label="$1"
  shift
  local remaining=""
  local soft_timeout=0
  local status=0
  remaining="$(remaining_phase_seconds)" \
    || {
      printf 'ERROR: desktop step %s has no remaining deadline budget\n' "$label" >&2
      return 70
    }
  soft_timeout=$((remaining - 5))
  ((soft_timeout > 0)) \
    || {
      printf 'ERROR: desktop step %s has no shutdown grace before deadline\n' \
        "$label" >&2
      return 70
    }
  if timeout --signal=TERM --kill-after=5 "${soft_timeout}s" "$@"; then
    return 0
  else
    status=$?
  fi
  if ((status == 124 || status == 137)); then
    printf 'ERROR: desktop step %s exhausted the absolute deadline\n' "$label" >&2
    return 70
  fi
  return "$status"
}

sleep_before_deadline() {
  local -r requested="$1"
  local duration=""
  duration="$(timeout_cap "$requested")" || return 70
  sleep "$duration"
}

mark_desktop_rollback_failed() {
  local -r failure="$1"
  python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
    --state-root "$STATE_DIR" --failure "$failure" >/dev/null 2>&1 || true
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-desktop \
    "CRITICAL desktop rollback failed: $failure" >/dev/null 2>&1 || true
}
cleanup() {
  local exit_code=$?
  trap - EXIT
  if [[ "$PRESERVE_MAINTENANCE_LEASE" == true ]]; then
    browser_maintenance_stop_renewal
    if [[ -n "$BROWSER_MAINTENANCE_RUNTIME_DIR" ]]; then
      rm -rf -- "$BROWSER_MAINTENANCE_RUNTIME_DIR"
      BROWSER_MAINTENANCE_RUNTIME_DIR=""
    fi
  else
    if ! browser_maintenance_leave; then
      log "CRITICAL: browser maintenance lease could not be released; it will expire"
      if ((exit_code == 0)); then
        exit_code=1
      fi
    fi
  fi
  [[ -z "${TEMP_DIR:-}" ]] || rm -rf -- "$TEMP_DIR"
  exit "$exit_code"
}
trap cleanup EXIT

dotenv_value() {
  local -r file="$1"
  local -r key="$2"
  sed -n "s/^${key}=//p" "$file" | tail -n 1
}

validate_image() {
  local -r file="$1"
  local -r key="$2"
  local value=""
  value="$(dotenv_value "$file" "$key")"
  [[ "$value" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || die "$key must be an immutable image@sha256 reference"
}

desktop_fingerprint() {
  local -r release_env="$1"
  local -r app_env="$2"
  local -r release_dir="$3"
  [[ -f "$release_dir/.fb-agent-source-manifest.json" \
    && ! -L "$release_dir/.fb-agent-source-manifest.json" ]] \
    || die "immutable source manifest is required for desktop fingerprint"
  python3 "$SCRIPT_DIR/release-state.py" desktop-digest \
    --release-dir "$release_dir" \
    --app-env "$app_env" \
    --release-env "$release_env"
}

validate_desktop_state() {
  local -r state_dir="$1"
  local -r state_name="${state_dir##*/}"
  local release_dir=""
  [[ -d "$state_dir" && ! -L "$state_dir" \
    && "$(dirname -- "$state_dir")" == "$DESKTOP_STATES_DIR" ]] \
    || die "active desktop state must be one real child of $DESKTOP_STATES_DIR"
  [[ "$state_name" =~ ^[A-Za-z0-9._-]{1,160}$ ]] \
    || die "active desktop state name is invalid"
  [[ "$(stat -Lc '%a' "$state_dir")" == "700" ]] \
    || die "desktop state must have mode 700"
  for file in app.env release-images.env fingerprint; do
    [[ -f "$state_dir/$file" && ! -L "$state_dir/$file" \
      && "$(stat -Lc '%a' "$state_dir/$file")" == "600" ]] \
      || die "desktop state file is missing, linked or has unsafe mode: $state_dir/$file"
  done
  [[ "$(<"$state_dir/fingerprint")" =~ ^[0-9a-f]{64}$ ]] \
    || die "desktop state fingerprint is invalid"
  [[ -L "$state_dir/release" ]] || die "desktop state release pointer is missing"
  release_dir="$(readlink -f "$state_dir/release")"
  [[ -d "$release_dir" && ! -L "$release_dir" \
    && "$(dirname -- "$release_dir")" == "${FB_AGENT_ROOT:-/opt/fb-agent}/releases" ]] \
    || die "desktop state release directory is outside the immutable release root"
  python3 "$SCRIPT_DIR/release-state.py" desktop-verify \
    --state-root "$STATE_DIR" \
    --state-dir "$state_dir" >/dev/null \
    || die "desktop state cryptographic contract is invalid"
}

atomic_relative_symlink() {
  local -r target="$1"
  local -r destination="$2"
  local temporary=""
  temporary="${destination}.new.$$"
  [[ ! -e "$temporary" && ! -L "$temporary" ]] \
    || die "temporary desktop state pointer already exists: $temporary"
  ln -s "$target" "$temporary"
  mv -Tf -- "$temporary" "$destination"
  sync -f "$(dirname -- "$destination")"
}

prepare_desktop_state() {
  local -r fingerprint="$1"
  local state_id=""
  local destination=""
  local temporary=""
  local readiness_destination=""
  local readiness_temporary=""
  local user=""
  local password=""

  state_id="${release_id}-${fingerprint:0:16}"
  [[ "$state_id" =~ ^[A-Za-z0-9._-]{1,160}$ ]] \
    || die "derived desktop state id is invalid"
  install -d -m 0700 \
    "$DESKTOP_STATES_DIR" "$DESKTOP_READINESS_DIR" "$DESKTOP_READINESS_STATES"
  destination="$DESKTOP_STATES_DIR/$state_id"
  if [[ -e "$destination" || -L "$destination" ]]; then
    validate_desktop_state "$destination"
    cmp -s -- "$APP_ENV" "$destination/app.env" \
      || die "desktop state id already exists with different application environment"
    cmp -s -- "$RELEASE_ENV" "$destination/release-images.env" \
      || die "desktop state id already exists with different image manifest"
    [[ "$(<"$destination/fingerprint")" == "$fingerprint" ]] \
      || die "desktop state id already exists with a different fingerprint"
    [[ "$(readlink -f "$destination/release")" == "$PROJECT_DIR" ]] \
      || die "desktop state id already references another release directory"
  else
    temporary="$(mktemp -d "$DESKTOP_STATES_DIR/.prepare-${state_id}-XXXXXXXX")"
    install -m 0600 "$APP_ENV" "$temporary/app.env"
    install -m 0600 "$RELEASE_ENV" "$temporary/release-images.env"
    printf '%s\n' "$fingerprint" >"$temporary/fingerprint"
    chmod 0600 "$temporary/fingerprint"
    ln -s "$PROJECT_DIR" "$temporary/release"
    sync -f "$temporary/app.env"
    sync -f "$temporary/release-images.env"
    sync -f "$temporary/fingerprint"
    sync -f "$temporary"
    mv -- "$temporary" "$destination"
    sync -f "$DESKTOP_STATES_DIR"
  fi
  CANDIDATE_STATE="$destination"

  user="$(dotenv_value "$APP_ENV" DESKTOP_KASM_SERVICE_USER)"
  password="$(dotenv_value "$APP_ENV" DESKTOP_KASM_SERVICE_PASSWORD)"
  [[ -n "$user" && "$user" != *:* && ${#password} -ge 16 ]] \
    || die "candidate desktop readiness credentials are invalid"
  readiness_destination="$DESKTOP_READINESS_STATES/$state_id.env"
  if [[ -e "$readiness_destination" || -L "$readiness_destination" ]]; then
    [[ -f "$readiness_destination" && ! -L "$readiness_destination" \
      && "$(stat -Lc '%a' "$readiness_destination")" == "600" ]] \
      || die "desktop readiness state is not a mode-600 regular file"
    expected_readiness="$TEMP_DIR/readiness.expected"
    {
      printf 'DESKTOP_KASM_SERVICE_USER=%s\n' "$user"
      printf 'DESKTOP_KASM_SERVICE_PASSWORD=%s\n' "$password"
    } >"$expected_readiness"
    chmod 0600 "$expected_readiness"
    cmp -s -- "$expected_readiness" "$readiness_destination" \
      || die "desktop readiness state id already exists with different credentials"
  else
    readiness_temporary="$(mktemp "$DESKTOP_READINESS_STATES/.prepare-${state_id}-XXXXXXXX")"
    {
      printf 'DESKTOP_KASM_SERVICE_USER=%s\n' "$user"
      printf 'DESKTOP_KASM_SERVICE_PASSWORD=%s\n' "$password"
    } >"$readiness_temporary"
    chmod 0600 "$readiness_temporary"
    sync -f "$readiness_temporary"
    mv -- "$readiness_temporary" "$readiness_destination"
    sync -f "$DESKTOP_READINESS_STATES"
  fi
}

ports_for_active_color() {
  local color=""
  [[ -f "$STATE_DIR/active-color" ]] \
    || die "active blue/green application release is required before desktop release"
  color="$(<"$STATE_DIR/active-color")"
  case "$color" in
    blue) API_PORT=18100 ;;
    green) API_PORT=28100 ;;
    *) die "invalid active-color state: $color" ;;
  esac
}

acquire_browser_maintenance() {
  browser_maintenance_enter \
    || die "durable browser maintenance fence could not be acquired"
}

browser_maintenance_is_held() {
  browser_maintenance_assert_held
}

release_browser_maintenance() {
  browser_maintenance_leave
}

assert_control_plane_quiescent() {
  browser_maintenance_assert_quiescent \
    || die "desktop release control plane is not quiescent"
}

browser_identity_is_exact() {
  local -r release_dir="$1"
  local -r manifest="$2"
  local -r app_env="$3"
  local container_id=""
  local expected_image=""
  local expected_release=""
  local expected_cluster=""
  local webtop_id=""
  local inspection=""
  expected_image="$(dotenv_value "$manifest" BROWSER_AGENT_IMAGE)"
  expected_release="$(dotenv_value "$manifest" RELEASE_ID)"
  expected_cluster="$(dotenv_value "$app_env" FB_AGENT_BOOTSTRAP_CLUSTER_ID)"
  webtop_id="$(docker inspect --format '{{.Id}}' vision-webtop 2>/dev/null)" \
    || return 1
  [[ -n "$webtop_id" && "$webtop_id" != *$'\n'* ]] || return 1
  container_id="$(run_browser_compose \
    "$release_dir" "$manifest" "$app_env" ps -q browser-agent)"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || return 1
  inspection="$(docker inspect --format \
    '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.fb-agent.managed"}}|{{index .Config.Labels "com.fb-agent.cluster-id"}}|{{index .Config.Labels "com.fb-agent.purpose"}}|{{index .Config.Labels "com.fb-agent.release"}}|{{.Config.Image}}|{{.HostConfig.NetworkMode}}' \
    "$container_id")" || return 1
  [[ "$inspection" == \
    "true|healthy|fb_agent_desktop|browser-agent|true|${expected_cluster}|vision|${expected_release}|${expected_image}|container:${webtop_id}" ]]
}

run_browser_compose() {
  local -r release_dir="$1"
  local -r manifest="$2"
  local -r app_env="$3"
  local operation=""
  shift 3
  operation="${1:-command}"
  FB_AGENT_BOOTSTRAP_CLUSTER_ID="$(
    dotenv_value "$app_env" FB_AGENT_BOOTSTRAP_CLUSTER_ID
  )"
  [[ "$FB_AGENT_BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] \
    || die "desktop environment has no durable bootstrap cluster identity"
  BROWSER_CONTROL_ENV_FILE="$PLATFORM_STATE_DIR/browser-control.env"
  browser_control_env_require "$BROWSER_CONTROL_ENV_FILE" \
    || die "browser control environment failed the private-file contract"
  export BROWSER_CONTROL_ENV_FILE
  BROWSER_AGENT_AM_COLUMNS_QS="$(
    dotenv_value "$app_env" BROWSER_AGENT_AM_COLUMNS_QS
  )"
  BROWSER_AUTHORITY_CONSUME_URL="$(
    dotenv_value "$app_env" BROWSER_AUTHORITY_CONSUME_URL
  )"
  BROWSER_MAINTENANCE_CONSUME_URL="$(
    dotenv_value "$app_env" BROWSER_MAINTENANCE_CONSUME_URL
  )"
  [[ "$BROWSER_AUTHORITY_CONSUME_URL" == \
    "https://app.adpulse.su/api/v1/internal/browser-operations/consume" ]] \
    || die "browser authority consume URL is not the canonical HTTPS endpoint"
  [[ "$BROWSER_MAINTENANCE_CONSUME_URL" == \
    "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume" ]] \
    || die "browser maintenance consume URL is not the canonical HTTPS endpoint"
  export BROWSER_AGENT_AM_COLUMNS_QS BROWSER_AUTHORITY_CONSUME_URL
  export BROWSER_MAINTENANCE_CONSUME_URL
  export RELEASE_ID FB_AGENT_BOOTSTRAP_CLUSTER_ID
  RELEASE_ID="$(dotenv_value "$manifest" RELEASE_ID)"
  run_before_deadline "browser_compose_${operation}" \
    docker compose -p fb_agent_desktop --env-file "$manifest" \
      -f "$release_dir/deploy/compose/docker-compose.desktop-agent.yml" "$@"
}

remove_browser_container() {
  local -r release_dir="$1"
  local -r manifest="$2"
  local -r app_env="$3"
  run_browser_compose "$release_dir" "$manifest" "$app_env" \
    stop --timeout 20 browser-agent \
    && browser_maintenance_checkpoint \
    && run_browser_compose "$release_dir" "$manifest" "$app_env" \
      rm -f browser-agent \
    && browser_maintenance_assert_held
}

vision_namespace_is_exact() {
  local -r release_dir="$1"
  FB_AGENT_ROOT="${FB_AGENT_ROOT:-/opt/fb-agent}" \
    VISION_WAIT_TIMEOUT_SECONDS=0 \
    VISION_WAIT_INTERVAL_SECONDS=0 \
    "$release_dir/scripts/wait-for-vision-container.sh" >/dev/null 2>&1
}

committed_vision_namespace_is_exact() {
  local release_dir=""
  [[ -L "$ACTIVE_DESKTOP_STATE/release" ]] || return 1
  release_dir="$(readlink -f "$ACTIVE_DESKTOP_STATE/release")" || return 1
  [[ -n "$release_dir" && "$release_dir" != *$'\n'* ]] || return 1
  vision_namespace_is_exact "$release_dir"
}

render_desktop_env() {
  local -r manifest="$1"
  local -r input="$2"
  local -r output="$3"
  python3 "$SCRIPT_DIR/prepare_production_env.py" \
    --input "$input" \
    --output "$output" \
    --desktop-webtop-image "$(dotenv_value "$manifest" DESKTOP_WEBTOP_IMAGE)"
}

install_desktop_units() {
  local -r release_dir="$1"
  local unit=""
  require_phase_deadline "unit installation" || return $?
  for unit in \
    fb-agent-desktop-agent.service \
    fb-agent-desktop-heal.service \
    fb-agent-desktop-heal.timer; do
    install -m 0644 \
      "$release_dir/deploy/systemd/$unit" "/etc/systemd/system/$unit"
  done
  run_before_deadline "desktop_systemd_reload" systemctl daemon-reload
}

activate_desktop_units() {
  local -r verifier="/usr/local/libexec/fb-agent-release-verifier/current/verified-release-exec.py"
  [[ -x "$verifier" ]] \
    || die "stable root-owned release verifier is unavailable"
  run_before_deadline "desktop_units_enable" \
    systemctl enable fb-agent-desktop-agent.service fb-agent-desktop-heal.timer
  run_before_deadline "desktop_units_start" \
    systemctl start fb-agent-desktop-agent.service fb-agent-desktop-heal.timer
  run_before_deadline "desktop_agent_active" \
    systemctl is-active --quiet fb-agent-desktop-agent.service \
    || die "verified desktop agent unit did not become active"
  run_before_deadline "desktop_healer_active" \
    systemctl is-active --quiet fb-agent-desktop-heal.timer \
    || die "desktop healer timer did not become active"
}

vision_control_ready() {
  local container_id=""
  local response=""
  local probe_timeout=""
  container_id="$(docker ps \
    --filter label=com.docker.compose.project=fb_agent_desktop \
    --filter label=com.docker.compose.service=browser-agent \
    --format '{{.ID}}')"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || return 1
  probe_timeout="$(timeout_cap 25)" || return 1
  response="$(timeout --signal=TERM "$probe_timeout" docker exec "$container_id" \
    node dist/meta-api/health-probe-cli.js --json "$EXPECTED_PROFILE_ID")" \
    || return 1
  printf '%s' "$response" \
    | python3 "$PROJECT_DIR/scripts/desktop-vision-contract.py" browser-ready \
      --expected-profile-id "$EXPECTED_PROFILE_ID"
}

previous_vision_control_ready() {
  local container_id=""
  local response=""
  local probe_timeout=""
  [[ -n "$PREVIOUS_RELEASE_DIR" ]] || return 1
  container_id="$(docker ps \
    --filter label=com.docker.compose.project=fb_agent_desktop \
    --filter label=com.docker.compose.service=browser-agent \
    --format '{{.ID}}')"
  [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || return 1
  probe_timeout="$(timeout_cap 25)" || return 1
  response="$(timeout --signal=TERM "$probe_timeout" docker exec "$container_id" \
    node dist/meta-api/health-probe-cli.js --json "$EXPECTED_PROFILE_ID")" \
    || return 1
  printf '%s' "$response" \
    | python3 "$PREVIOUS_RELEASE_DIR/scripts/desktop-vision-contract.py" browser-ready \
      --expected-profile-id "$EXPECTED_PROFILE_ID"
}

previous_rollback_contract_is_compatible() {
  local previous_contract_version=""
  [[ -n "$PREVIOUS_RELEASE_DIR" \
    && -f "$PREVIOUS_RELEASE_DIR/scripts/desktop-vision-contract.py" \
    && ! -L "$PREVIOUS_RELEASE_DIR/scripts/desktop-vision-contract.py" ]] \
    || return 1
  previous_contract_version="$(
    run_before_deadline "previous_contract_version" \
      python3 "$PREVIOUS_RELEASE_DIR/scripts/desktop-vision-contract.py" \
        required-version
  )" || return 1
  [[ "$previous_contract_version" == "$CANDIDATE_BROWSER_CONTRACT_VERSION" ]]
}

previous_runtime_direct_is_exact_and_ready() {
  vision_namespace_is_exact "$PREVIOUS_RELEASE_DIR" \
    && desktop_auth_ready "$PREVIOUS_VISION_ENV" \
    && browser_identity_is_exact \
      "$PREVIOUS_RELEASE_DIR" "$PREVIOUS_MANIFEST" "$PREVIOUS_APP_ENV" \
    && previous_vision_control_ready
}

previous_runtime_matches_active_app() {
  previous_runtime_direct_is_exact_and_ready \
    && committed_desktop_auth_ready
}

configured_vision_contract() {
  local response=""
  local request_timeout=""
  request_timeout="$(timeout_cap 25)" || return 1
  response="$(curl --silent --show-error --fail --max-time "$request_timeout" \
    --header "X-API-Key: $CONTROL_API_KEY" \
    --header "X-FB-Agent-Browser-Maintenance-Owner: $BROWSER_MAINTENANCE_OWNER" \
    "http://127.0.0.1:${API_PORT}/api/settings/vision")" || return 1
  python3 -c '
import base64, json, sys
payload = json.loads(sys.argv[1])
profile_id = payload.get("profile_id")
revision = payload.get("configuration_revision")
required_contract = payload.get("required_browser_contract_version")
if (
    not isinstance(profile_id, str)
    or not profile_id.strip()
    or any(char in profile_id for char in "\r\n\t")
    or not isinstance(revision, str)
    or not revision
    or not isinstance(required_contract, int)
    or isinstance(required_contract, bool)
    or required_contract <= 0
):
    raise SystemExit(1)
encoded_revision = base64.urlsafe_b64encode(revision.encode("utf-8")).decode("ascii")
print(f"{profile_id.strip()}\t{encoded_revision}\t{required_contract}")
' "$response"
}

load_configured_vision_contract() {
  local contract=""
  browser_maintenance_assert_held || return 1
  contract="$(configured_vision_contract)" || return 1
  IFS=$'\t' read -r EXPECTED_PROFILE_ID EXPECTED_CONFIGURATION_REVISION \
    EXPECTED_BROWSER_CONTRACT_VERSION \
    <<<"$contract"
  [[ -n "$EXPECTED_PROFILE_ID" && -n "$EXPECTED_CONFIGURATION_REVISION" \
    && "$EXPECTED_BROWSER_CONTRACT_VERSION" == "$CANDIDATE_BROWSER_CONTRACT_VERSION" ]] \
    || {
      printf 'ERROR: active app requires browser contract %s but desktop candidate provides %s\n' \
        "${EXPECTED_BROWSER_CONTRACT_VERSION:-missing}" \
        "${CANDIDATE_BROWSER_CONTRACT_VERSION:-missing}" >&2
      return 1
    }
  browser_maintenance_assert_held
}

configured_vision_contract_is_unchanged() {
  local contract=""
  local current_profile_id=""
  local current_configuration_revision=""
  local current_browser_contract_version=""
  browser_maintenance_assert_held || return 1
  contract="$(configured_vision_contract)" || return 1
  IFS=$'\t' read -r current_profile_id current_configuration_revision \
    current_browser_contract_version \
    <<<"$contract"
  [[ "$current_profile_id" == "$EXPECTED_PROFILE_ID" \
    && "$current_configuration_revision" == "$EXPECTED_CONFIGURATION_REVISION" \
    && "$current_browser_contract_version" == "$EXPECTED_BROWSER_CONTRACT_VERSION" ]] \
    && browser_maintenance_assert_held
}

desktop_auth_ready() {
  local -r env_file="$1"
  local anonymous_status=""
  local authenticated_status=""
  local user=""
  local password=""
  local request_timeout=""
  request_timeout="$(timeout_cap 10)" || return 1
  user="$(dotenv_value "$env_file" DESKTOP_KASM_SERVICE_USER)"
  password="$(dotenv_value "$env_file" DESKTOP_KASM_SERVICE_PASSWORD)"
  anonymous_status="$(curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time "$request_timeout" \
    http://127.0.0.1:8444/)" || return 1
  authenticated_status="$(curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time "$request_timeout" \
    --user "$user:$password" http://127.0.0.1:8444/)" || return 1
  [[ "$anonymous_status" == "401" && "$authenticated_status" == "200" ]]
}

candidate_desktop_auth_ready() {
  desktop_auth_ready "$APP_ENV"
}

committed_desktop_auth_ready() {
  local response=""
  local request_timeout=""
  request_timeout="$(timeout_cap 10)" || return 1
  response="$(curl --silent --show-error --fail --max-time "$request_timeout" \
    "http://127.0.0.1:${API_PORT}/desktop-readyz")" || return 1
  python3 -c '
import json, sys
payload = json.loads(sys.argv[1])
checks = payload.get("checks")
required = ("configured", "auth_challenge", "authenticated")
raise SystemExit(
    0 if payload.get("status") == "ok"
    and isinstance(checks, dict)
    and all(checks.get(key) is True for key in required)
    else 1
)
' "$response"
}

ensure_cdp() {
  local response=""
  local request_timeout=""
  browser_maintenance_assert_held || return 1
  request_timeout="$(timeout_cap 120)" || return 1
  response="$(curl --silent --show-error --fail --max-time "$request_timeout" \
    --request POST \
    --header "X-API-Key: $CONTROL_API_KEY" \
    --header "X-FB-Agent-Browser-Maintenance-Owner: $BROWSER_MAINTENANCE_OWNER" \
    "http://127.0.0.1:${API_PORT}/api/vision/ensure-cdp")" || return 1
  python3 -c '
import json, sys
raise SystemExit(0 if json.loads(sys.argv[1]).get("ok") is True else 1)
' "$response" || return 1
  browser_maintenance_assert_held
}

wait_for_candidate_desktop_readiness() {
  local attempt=0
  for attempt in $(seq 1 60); do
    if vision_control_ready && candidate_desktop_auth_ready; then
      log "desktop CDP and candidate-credential Kasm readiness are confirmed"
      return 0
    fi
    if ((attempt == 1 || attempt % 10 == 0)); then
      ensure_cdp || true
    fi
    sleep_before_deadline 2 || return 70
  done
  die "desktop cutover refused: CDP and candidate Kasm authentication did not converge"
}

wait_for_committed_desktop_readiness() {
  local attempt=0
  for attempt in $(seq 1 30); do
    if committed_vision_namespace_is_exact \
      && vision_control_ready \
      && committed_desktop_auth_ready; then
      log "committed desktop credentials and API readiness are confirmed"
      return 0
    fi
    sleep_before_deadline 2 || return 70
  done
  die "desktop commit refused: API /desktop-readyz did not accept committed credentials"
}

while (($#)); do
  case "$1" in
    --release-env) RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --app-env) APP_ENV="${2:?missing value}"; shift 2 ;;
    --profile-seed-dir) PROFILE_SEED_DIR="${2:?missing value}"; shift 2 ;;
    --deadline-epoch) DEADLINE_EPOCH="${2:?missing value}"; shift 2 ;;
    --preflight-only) PREFLIGHT_ONLY=true; shift ;;
    --rollback-only) ROLLBACK_ONLY=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "$PREFLIGHT_ONLY" == false || "$ROLLBACK_ONLY" == false ]] \
  || die "--preflight-only and --rollback-only are mutually exclusive"

for command in \
  chmod cmp curl date dirname docker env flock install ln logger mktemp mv python3 \
  readlink rm sed seq sleep stat sync systemctl timeout; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
for file in "$RELEASE_ENV" "$APP_ENV" "$COMPOSE_FILE"; do
  [[ -f "$file" ]] || die "required desktop release file is missing: $file"
done
[[ "$(stat -Lc '%a' "$RELEASE_ENV")" == "600" ]] || die "$RELEASE_ENV must have mode 600"
[[ "$(stat -Lc '%a' "$APP_ENV")" == "600" ]] || die "$APP_ENV must have mode 600"
for key in BROWSER_AGENT_IMAGE DESKTOP_WEBTOP_IMAGE; do
  validate_image "$RELEASE_ENV" "$key"
done
release_id="$(dotenv_value "$RELEASE_ENV" RELEASE_ID)"
[[ "$release_id" =~ ^[A-Za-z0-9._-]+$ ]] || die "desktop release id is invalid"
if [[ "$PREFLIGHT_ONLY" == false ]]; then
  [[ "$DEADLINE_EPOCH" =~ ^[0-9]+$ ]] \
    || die "desktop cutover requires --deadline-epoch"
  now_epoch="$(date +%s)"
  ((DEADLINE_EPOCH > now_epoch && DEADLINE_EPOCH <= now_epoch + 180)) \
    || die "desktop absolute deadline must be live and within 180 seconds"
  if [[ "$ROLLBACK_ONLY" == true ]]; then
    FORWARD_DEADLINE_EPOCH="$DEADLINE_EPOCH"
    export FB_AGENT_BROWSER_MAINTENANCE_DEADLINE_EPOCH="$DEADLINE_EPOCH"
  else
    FORWARD_DEADLINE_EPOCH=$((DEADLINE_EPOCH - ROLLBACK_RESERVE_SECONDS))
    ((FORWARD_DEADLINE_EPOCH > now_epoch)) \
      || die "desktop cutover has no forward budget before its rollback reserve"
    export FB_AGENT_BROWSER_MAINTENANCE_DEADLINE_EPOCH="$FORWARD_DEADLINE_EPOCH"
  fi
elif [[ -n "$DEADLINE_EPOCH" ]]; then
  die "--preflight-only cannot accept a cutover deadline"
fi
install -d -m 0700 "$STATE_DIR" "$DESKTOP_STATES_DIR"
exec 8>"$STATE_DIR/desktop-release.lock"
flock -n 8 || die "another desktop release or healer is already running"
if [[ -e "$ACTIVE_DESKTOP_STATE" || -L "$ACTIVE_DESKTOP_STATE" ]]; then
  [[ -L "$ACTIVE_DESKTOP_STATE" ]] || die "active desktop state must be a symlink"
  PREVIOUS_STATE="$(readlink -f "$ACTIVE_DESKTOP_STATE")"
  validate_desktop_state "$PREVIOUS_STATE"
  PREVIOUS_MANIFEST="$PREVIOUS_STATE/release-images.env"
  PREVIOUS_APP_ENV="$PREVIOUS_STATE/app.env"
  PREVIOUS_RELEASE_DIR="$(readlink -f "$PREVIOUS_STATE/release")"
  for key in BROWSER_AGENT_IMAGE DESKTOP_WEBTOP_IMAGE; do
    validate_image "$PREVIOUS_MANIFEST" "$key"
  done
  previous_release_id="$(dotenv_value "$PREVIOUS_MANIFEST" RELEASE_ID)"
  [[ "$previous_release_id" =~ ^[A-Za-z0-9._-]+$ ]] \
    || die "active desktop release id is invalid"
else
  for legacy in \
    "$STATE_DIR/active-desktop.env" \
    "$STATE_DIR/active-desktop-release-images.env"; do
    [[ ! -e "$legacy" && ! -L "$legacy" ]] \
      || die "legacy desktop state is unsupported; reset it before clean adoption: $legacy"
  done
  [[ -n "$PROFILE_SEED_DIR" ]] \
    || die "fresh desktop release requires --profile-seed-dir"
fi

if [[ "$PREFLIGHT_ONLY" == false || -n "$PREVIOUS_STATE" ]]; then
  ports_for_active_color
  CONTROL_APP_ENV="$STATE_DIR/active-app.env"
  [[ -f "$CONTROL_APP_ENV" ]] \
    || die "committed active application environment is missing"
  CONTROL_API_KEY="$(dotenv_value "$CONTROL_APP_ENV" API_KEY)"
  [[ -n "$CONTROL_API_KEY" && "$CONTROL_API_KEY" != *$'\n'* ]] \
    || die "active API key is missing"
fi

TEMP_DIR="$(mktemp -d)"
if [[ -n "$PREVIOUS_MANIFEST" ]]; then
  PREVIOUS_VISION_ENV="$TEMP_DIR/previous-vision.env"
  render_desktop_env "$PREVIOUS_MANIFEST" "$PREVIOUS_APP_ENV" "$PREVIOUS_VISION_ENV"
  chmod 0600 "$PREVIOUS_VISION_ENV"
fi
if [[ -e "$ACTIVE_DESKTOP_READINESS" || -L "$ACTIVE_DESKTOP_READINESS" ]]; then
  [[ -L "$ACTIVE_DESKTOP_READINESS" ]] \
    || die "active desktop readiness state must be a symlink"
fi

candidate_fingerprint="$(desktop_fingerprint "$RELEASE_ENV" "$APP_ENV" "$PROJECT_DIR")"
CANDIDATE_BROWSER_CONTRACT_VERSION="$(
  python3 "$PROJECT_DIR/scripts/desktop-vision-contract.py" required-version
)"
[[ "$CANDIDATE_BROWSER_CONTRACT_VERSION" =~ ^[1-9][0-9]*$ ]] \
  || die "desktop candidate browser contract version is invalid"
expected_candidate_state_id="${release_id}-${candidate_fingerprint:0:16}"
[[ "$expected_candidate_state_id" =~ ^[A-Za-z0-9._-]{1,160}$ ]] \
  || die "derived desktop state id is invalid"
if [[ "$PREFLIGHT_ONLY" == true ]]; then
  log "pre-pulling immutable desktop images before application cutover"
  run_browser_compose "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" config --quiet
  while IFS= read -r image; do
    [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] \
      || die "desktop preflight found a non-immutable image: $image"
  done < <(
    run_browser_compose "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" config --images
  )
  run_browser_compose "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" pull browser-agent
  run_before_deadline "desktop_webtop_pull" \
    docker pull "$(dotenv_value "$RELEASE_ENV" DESKTOP_WEBTOP_IMAGE)"
  if [[ -z "$PREVIOUS_STATE" ]]; then
    "$SCRIPT_DIR/install-vision-webtop.sh" \
      --profile-seed-dir "$PROFILE_SEED_DIR" \
      --validate-profile-seed-only
    log "fresh desktop candidate images and profile seed passed preflight"
    exit 0
  fi

  previous_rollback_contract_is_compatible \
    || die "previous desktop release cannot satisfy the exact browser rollback contract"

  acquire_browser_maintenance
  assert_control_plane_quiescent
  load_configured_vision_contract \
    || die "incumbent app/browser contract is incompatible with the candidate"
  previous_runtime_matches_active_app \
    || die "previous desktop runtime is not exactly restorable before cutover"
  browser_maintenance_checkpoint \
    || die "browser maintenance lease was lost during desktop preflight"
  release_browser_maintenance
  log "candidate images and previous rollback path passed desktop preflight"
  exit 0
fi

acquire_browser_maintenance
assert_control_plane_quiescent
load_configured_vision_contract \
  || die "canonical Vision profile/revision contract is unavailable from the active API"
pending_desktop_transaction="$("$SCRIPT_DIR/platform-desktop-transaction.sh" status)"
case "$pending_desktop_transaction" in
  none) ;;
  candidate|previous|absent)
    journal_candidate="$(dotenv_value "$DESKTOP_TRANSACTION" candidate_state)"
    [[ "$journal_candidate" == "$expected_candidate_state_id" ]] \
      || die "another immutable desktop candidate owns the durable transaction"
    journal_previous="$(dotenv_value "$DESKTOP_TRANSACTION" previous_state)"
    JOURNAL_CANDIDATE_STATE="$DESKTOP_STATES_DIR/$journal_candidate"
    validate_desktop_state "$JOURNAL_CANDIDATE_STATE"
    if [[ -n "$journal_previous" ]]; then
      JOURNAL_PREVIOUS_STATE="$DESKTOP_STATES_DIR/$journal_previous"
      validate_desktop_state "$JOURNAL_PREVIOUS_STATE"
    fi

    # The active pointer selects transaction direction; it is not necessarily
    # the rollback target. On a crash-resumed candidate it already points at
    # N+1, while the authoritative rollback state remains journal.previous.
    PREVIOUS_STATE="$JOURNAL_PREVIOUS_STATE"
    PREVIOUS_MANIFEST=""
    PREVIOUS_APP_ENV=""
    PREVIOUS_RELEASE_DIR=""
    PREVIOUS_VISION_ENV=""
    if [[ -n "$PREVIOUS_STATE" ]]; then
      PREVIOUS_MANIFEST="$PREVIOUS_STATE/release-images.env"
      PREVIOUS_APP_ENV="$PREVIOUS_STATE/app.env"
      PREVIOUS_RELEASE_DIR="$(readlink -f "$PREVIOUS_STATE/release")"
      for key in BROWSER_AGENT_IMAGE DESKTOP_WEBTOP_IMAGE; do
        validate_image "$PREVIOUS_MANIFEST" "$key"
      done
      previous_release_id="$(dotenv_value "$PREVIOUS_MANIFEST" RELEASE_ID)"
      [[ "$previous_release_id" =~ ^[A-Za-z0-9._-]+$ ]] \
        || die "journal previous desktop release id is invalid"
      PREVIOUS_VISION_ENV="$TEMP_DIR/previous-vision.env"
      render_desktop_env \
        "$PREVIOUS_MANIFEST" "$PREVIOUS_APP_ENV" "$PREVIOUS_VISION_ENV"
      chmod 0600 "$PREVIOUS_VISION_ENV"
    fi
    ;;
  *) die "durable desktop transaction status is invalid" ;;
esac

rollback() {
  local -r exit_code="$1"
  local rollback_failed=false
  local rollback_failure=""
  local restored_ready=false
  local attempt=0
  local transaction_outcome=""
  local expected_transaction_outcome=""
  local rollback_wait_timeout=""
  local active_desktop_state=""
  local candidate_pointer_committed=false
  if [[ "$ROLLBACK_IN_PROGRESS" == true ]]; then
    PRESERVE_MAINTENANCE_LEASE=true
    exit 70
  fi
  ROLLBACK_IN_PROGRESS=true
  ROLLBACK_ARMED=false
  export FB_AGENT_BROWSER_MAINTENANCE_DEADLINE_EPOCH="$DEADLINE_EPOCH"
  trap - ERR
  # This rollback is already bounded by DEADLINE_EPOCH. Catch repeated
  # operator signals without aborting convergence; commands executed by the
  # shell keep their normal signal disposition and remain deadline-killable.
  trap ':' HUP TERM INT
  log "candidate desktop release failed"
  if ! require_phase_deadline "rollback"; then
    mark_desktop_rollback_failed "desktop_rollback_deadline_exhausted"
    PRESERVE_MAINTENANCE_LEASE=true
    exit 70
  fi
  if ! browser_maintenance_checkpoint; then
    mark_desktop_rollback_failed "desktop_rollback_fence_lost"
    PRESERVE_MAINTENANCE_LEASE=true
    exit 70
  fi
  if [[ -n "$CANDIDATE_STATE" \
    && ! -e "$DESKTOP_TRANSACTION" && ! -L "$DESKTOP_TRANSACTION" \
    && -L "$ACTIVE_DESKTOP_STATE" \
    && "$(readlink -f "$ACTIVE_DESKTOP_STATE")" == "$CANDIDATE_STATE" ]]; then
    # Deleting the durable transaction after exact readiness/unit activation
    # is the desktop commit point. A signal in the following instruction gap
    # must converge the app forward; rolling back only desktop would split the
    # two control planes.
    log "desktop commit point is durable; parent must converge candidate forward"
    exit 75
  fi
  remove_browser_container "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
    >/dev/null 2>&1 \
    || {
      rollback_failed=true
      rollback_failure="${rollback_failure},candidate_browser_remove"
    }
  if [[ -n "$CANDIDATE_STATE" && -L "$ACTIVE_DESKTOP_STATE" ]]; then
    active_desktop_state="$(readlink -f "$ACTIVE_DESKTOP_STATE")" || true
    if [[ "$active_desktop_state" == "$CANDIDATE_STATE" ]]; then
      candidate_pointer_committed=true
    fi
  fi
  if [[ "$candidate_pointer_committed" == true ]]; then
    require_phase_deadline "state pointer rollback" \
      || {
        mark_desktop_rollback_failed "desktop_rollback_deadline_exhausted"
        PRESERVE_MAINTENANCE_LEASE=true
        exit 70
      }
    if [[ -n "$PREVIOUS_STATE" ]]; then
      atomic_relative_symlink \
        "desktop-states/${PREVIOUS_STATE##*/}" "$ACTIVE_DESKTOP_STATE" \
        || {
          rollback_failed=true
          rollback_failure="${rollback_failure},desktop_state_pointer_restore"
        }
    else
      rm -f -- "$ACTIVE_DESKTOP_STATE" \
        || {
          rollback_failed=true
          rollback_failure="${rollback_failure},desktop_state_pointer_remove"
        }
    fi
    STATE_COMMITTED=false
  elif [[ "$STATE_COMMITTED" == true ]]; then
    rollback_failed=true
    rollback_failure="${rollback_failure},desktop_state_pointer_ambiguous"
  fi
  if [[ "$TRANSACTION_PREPARED" == true \
    || -e "$DESKTOP_TRANSACTION" || -L "$DESKTOP_TRANSACTION" ]]; then
    transaction_outcome="$(
      run_before_deadline "desktop_transaction_rollback_reconcile" \
        "$SCRIPT_DIR/platform-desktop-transaction.sh" reconcile
    )" \
      || {
        rollback_failed=true
        rollback_failure="${rollback_failure},desktop_transaction_reconcile"
      }
    expected_transaction_outcome=absent
    if [[ -n "$PREVIOUS_STATE" ]]; then
      expected_transaction_outcome=previous
    fi
    if [[ -n "$transaction_outcome" \
      && "$transaction_outcome" != "$expected_transaction_outcome" ]]; then
      rollback_failed=true
      rollback_failure="${rollback_failure},desktop_transaction_direction"
    fi
  fi
  if [[ -n "$PREVIOUS_MANIFEST" ]]; then
    log "restoring the previous Vision desktop/browser-agent image set"
    if [[ "$rollback_failed" == false ]]; then
      run_before_deadline "previous_vision_restore" env \
        FB_AGENT_VISION_RELEASE_ID="$previous_release_id" \
        VISION_COMPOSE_ENV_FILE="$PREVIOUS_VISION_ENV" \
        VISION_ROLLBACK_ENV_FILE="$APP_ENV" \
        "$PREVIOUS_RELEASE_DIR/scripts/install-vision-webtop.sh" \
          --reconcile-pending-update \
        || {
          rollback_failed=true
          rollback_failure="${rollback_failure},vision_restore"
        }
    fi
    if [[ "$rollback_failed" == false ]]; then
      rollback_wait_timeout="$(timeout_cap 45)" \
        || {
          rollback_failed=true
          rollback_failure="${rollback_failure},browser_restore_deadline"
        }
    fi
    if [[ "$rollback_failed" == false ]]; then
      run_browser_compose \
        "$PREVIOUS_RELEASE_DIR" "$PREVIOUS_MANIFEST" "$PREVIOUS_APP_ENV" \
        up -d --force-recreate --wait \
          --wait-timeout "$rollback_wait_timeout" browser-agent \
        >/dev/null 2>&1 \
        || {
          rollback_failed=true
          rollback_failure="${rollback_failure},browser_restore"
        }
    fi
    if [[ "$rollback_failed" == false ]]; then
      for attempt in $(seq 1 60); do
        if desktop_auth_ready "$PREVIOUS_VISION_ENV" \
          && previous_vision_control_ready \
          && browser_identity_is_exact \
            "$PREVIOUS_RELEASE_DIR" "$PREVIOUS_MANIFEST" "$PREVIOUS_APP_ENV"; then
          restored_ready=true
          break
        fi
        if ((attempt == 1 || attempt % 10 == 0)); then
          ensure_cdp || true
        fi
        sleep_before_deadline 2 \
          || {
            rollback_failed=true
            rollback_failure="${rollback_failure},semantic_recheck_deadline"
            break
          }
      done
      if [[ "$restored_ready" != true ]]; then
        rollback_failed=true
        rollback_failure="${rollback_failure},semantic_or_identity_recheck"
      fi
      if ! browser_maintenance_is_held; then
        rollback_failed=true
        rollback_failure="${rollback_failure},maintenance_lease_lost"
      fi
    fi
  else
    vision_root="${VISION_WEBTOP_ROOT:-/opt/vision-webtop}"
    if [[ "$rollback_failed" == false && -f "$vision_root/compose.yaml" ]]; then
      run_before_deadline "fresh_vision_stop" \
        docker compose --env-file "$APP_ENV" --project-directory "$vision_root" \
        -f "$vision_root/compose.yaml" down --remove-orphans --timeout 20 \
        >/dev/null 2>&1 \
        || {
          rollback_failed=true
          rollback_failure="${rollback_failure},fresh_vision_stop"
        }
    fi
  fi
  if [[ "$rollback_failed" == false \
    && ( "$TRANSACTION_PREPARED" == true \
      || -e "$DESKTOP_TRANSACTION" || -L "$DESKTOP_TRANSACTION" ) ]]; then
    expected_transaction_outcome=absent
    if [[ -n "$PREVIOUS_STATE" ]]; then
      expected_transaction_outcome=previous
    fi
    run_before_deadline "desktop_transaction_rollback_complete" \
      "$SCRIPT_DIR/platform-desktop-transaction.sh" complete \
        --expect "$expected_transaction_outcome" \
      || {
        rollback_failed=true
        rollback_failure="${rollback_failure},desktop_transaction_complete"
      }
    TRANSACTION_PREPARED=false
  fi
  if [[ "$rollback_failed" == true ]]; then
    rollback_failure="${rollback_failure#,}"
    mark_desktop_rollback_failed \
      "desktop_rollback_nonconvergent:${rollback_failure:0:120}"
    PRESERVE_MAINTENANCE_LEASE=true
    exit 70
  fi
  if ((exit_code == 70)); then
    # 70 from a forward step means the reserved forward budget ended. Once
    # rollback has converged successfully, return a normal failure so the
    # parent can restore/reconcile the application within the same terminal
    # epoch. Exit 70 is reserved for non-convergent rollback only.
    exit 1
  fi
  exit "$exit_code"
}
handle_termination() {
  local -r exit_code="$1"
  trap ':' HUP TERM INT
  if [[ "$ROLLBACK_ARMED" == true && "$ROLLBACK_IN_PROGRESS" == false ]]; then
    rollback "$exit_code"
  fi
  exit "$exit_code"
}
trap 'handle_termination 129' HUP
trap 'handle_termination 143' TERM
trap 'handle_termination 130' INT

ROLLBACK_ARMED=true
trap 'rollback $?' ERR

reconciled_desktop_outcome="$pending_desktop_transaction"
if [[ "$pending_desktop_transaction" != none ]]; then
  CANDIDATE_STATE="$JOURNAL_CANDIDATE_STATE"
  TRANSACTION_PREPARED=true
  if [[ "$pending_desktop_transaction" == candidate ]]; then
    STATE_COMMITTED=true
  fi
  reconciled_desktop_outcome="$(
    run_before_deadline "resume_desktop_transaction" \
      "$SCRIPT_DIR/platform-desktop-transaction.sh" reconcile
  )"
  [[ "$reconciled_desktop_outcome" == "$pending_desktop_transaction" ]] \
    || die "durable desktop transaction changed commit-point direction"
fi

case "$reconciled_desktop_outcome" in
  candidate)
    [[ -L "$ACTIVE_DESKTOP_READINESS" \
      && "$(readlink "$ACTIVE_DESKTOP_READINESS")" == \
        "states/${JOURNAL_CANDIDATE_STATE##*/}.env" ]] \
      || die "candidate desktop readiness did not reconcile with its pointer"
    ;;
  previous|none)
    if [[ -n "$PREVIOUS_STATE" ]]; then
      [[ -L "$ACTIVE_DESKTOP_READINESS" \
        && "$(readlink "$ACTIVE_DESKTOP_READINESS")" == \
          "states/${PREVIOUS_STATE##*/}.env" ]] \
        || die "previous desktop readiness did not reconcile with its pointer"
    else
      [[ ! -e "$ACTIVE_DESKTOP_READINESS" && ! -L "$ACTIVE_DESKTOP_READINESS" ]] \
        || die "desktop readiness exists without an active previous state"
    fi
    ;;
  absent)
    [[ ! -e "$ACTIVE_DESKTOP_READINESS" && ! -L "$ACTIVE_DESKTOP_READINESS" ]] \
      || die "fresh rollback left a desktop readiness pointer"
    ;;
  *) die "reconciled desktop transaction outcome is invalid" ;;
esac

if [[ "$ROLLBACK_ONLY" == true ]]; then
  case "$reconciled_desktop_outcome" in
    previous|absent)
      rollback 0
      ;;
    *)
      mark_desktop_rollback_failed \
        "desktop_rollback_only_refused_${reconciled_desktop_outcome}"
      PRESERVE_MAINTENANCE_LEASE=true
      exit 70
      ;;
  esac
fi

if [[ -n "$PREVIOUS_STATE" \
  && "$reconciled_desktop_outcome" != candidate ]]; then
  previous_rollback_contract_is_compatible \
    || die "previous desktop release cannot satisfy the exact rollback contract"
  previous_runtime_direct_is_exact_and_ready \
    || die "previous desktop runtime is not exactly restorable before mutation"
fi

require_phase_deadline "candidate state preparation"
prepare_desktop_state "$candidate_fingerprint"

# A retry of the exact already-committed immutable desktop needs no journal or
# runtime mutation. It is accepted only after the candidate app, direct Kasm,
# Vision contract and exact browser identity all pass again.
if [[ "$pending_desktop_transaction" == none \
  && -n "$PREVIOUS_STATE" \
  && "$CANDIDATE_STATE" == "$PREVIOUS_STATE" ]]; then
  log "exact desktop state is already committed; revalidating without mutation"
  wait_for_candidate_desktop_readiness
  wait_for_committed_desktop_readiness
  browser_identity_is_exact \
    "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
    || die "running browser-agent does not match the exact candidate desktop state"
  browser_maintenance_checkpoint \
    || die "browser maintenance lease expired during readiness"
  configured_vision_contract_is_unchanged \
    || die "Vision profile/revision changed during desktop release"
  run_before_deadline "exact_desktop_agent_active" \
    systemctl is-active --quiet fb-agent-desktop-agent.service \
    || die "exact desktop agent unit is not active"
  run_before_deadline "exact_desktop_healer_active" \
    systemctl is-active --quiet fb-agent-desktop-heal.timer \
    || die "exact desktop healer timer is not active"
  ROLLBACK_ARMED=false
  trap - ERR
  release_browser_maintenance
  exit 0
fi

# Persist both rollback identities before the first browser/Vision/unit
# mutation. A SIGKILL from this point therefore leaves an explicit pending
# direction which the parent may reconcile, never a false stable pointer.
if [[ "$pending_desktop_transaction" == none ]]; then
  run_before_deadline "desktop_transaction_prepare" \
    "$SCRIPT_DIR/platform-desktop-transaction.sh" prepare \
      --candidate-state "$CANDIDATE_STATE" \
      --previous-state "$PREVIOUS_STATE"
  TRANSACTION_PREPARED=true
fi

remove_browser_container "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
  || die "browser-agent could not be removed before Vision replacement"
vision_install_args=()
if [[ -z "$PREVIOUS_MANIFEST" ]]; then
  vision_install_args=(--profile-seed-dir "$PROFILE_SEED_DIR")
fi
run_before_deadline "candidate_vision_install" env \
  FB_AGENT_VISION_RELEASE_ID="$release_id" \
  VISION_COMPOSE_ENV_FILE="$APP_ENV" \
  VISION_ROLLBACK_ENV_FILE="$PREVIOUS_VISION_ENV" \
  "$SCRIPT_DIR/install-vision-webtop.sh" \
    --defer-commit "${vision_install_args[@]}"
run_browser_compose "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" config --quiet
while IFS= read -r image; do
  [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
    printf 'ERROR: non-immutable desktop image: %s\n' "$image" >&2
    false
  }
done < <(run_browser_compose "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" config --images)
run_browser_compose "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" pull browser-agent
browser_wait_timeout="$(timeout_cap 60)"
run_browser_compose "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
  up -d --force-recreate --wait \
    --wait-timeout "$browser_wait_timeout" browser-agent

wait_for_candidate_desktop_readiness
browser_identity_is_exact "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
  || die "candidate browser-agent container identity is not exact"
browser_maintenance_checkpoint \
  || die "browser maintenance lease expired before desktop commit"
configured_vision_contract_is_unchanged \
  || die "Vision profile/revision changed before desktop commit"
install_desktop_units "$PROJECT_DIR"
browser_maintenance_checkpoint \
  || die "browser maintenance lease expired before desktop pointer commit"
configured_vision_contract_is_unchanged \
  || die "Vision profile/revision changed before desktop pointer commit"
require_phase_deadline "desktop pointer commit"
atomic_relative_symlink \
  "desktop-states/${CANDIDATE_STATE##*/}" "$ACTIVE_DESKTOP_STATE"
STATE_COMMITTED=true
[[ "$(readlink -f "$ACTIVE_DESKTOP_STATE")" == "$CANDIDATE_STATE" ]] \
  || die "active desktop pointer did not commit the candidate state"
browser_identity_is_exact "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
  || die "browser-agent identity changed during final desktop commit"
browser_maintenance_is_held || die "browser maintenance lease expired after desktop commit"
[[ "$(
  run_before_deadline "desktop_transaction_forward_reconcile" \
    "$SCRIPT_DIR/platform-desktop-transaction.sh" reconcile
)" == candidate ]] \
  || die "desktop transaction did not converge toward the committed candidate"
wait_for_committed_desktop_readiness
browser_identity_is_exact "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
  || die "browser-agent changed during committed readiness verification"
browser_maintenance_checkpoint \
  || die "browser maintenance lease expired during desktop commit"
configured_vision_contract_is_unchanged \
  || die "Vision profile/revision changed during desktop commit"

# Finalizing a pending Vision manifest may resume a crash-interrupted runtime
# replacement. Remove the namespace-sharing browser first in every case, then
# recreate it and prove the binding to the current webtop container ID.
remove_browser_container "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
  || die "browser-agent could not be removed before final Vision reconciliation"
run_before_deadline "candidate_vision_commit" env \
  FB_AGENT_VISION_RELEASE_ID="$release_id" \
  VISION_COMPOSE_ENV_FILE="$APP_ENV" \
  "$SCRIPT_DIR/install-vision-webtop.sh" --reconcile-pending-update
browser_wait_timeout="$(timeout_cap 60)"
run_browser_compose "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
  up -d --force-recreate --wait \
    --wait-timeout "$browser_wait_timeout" browser-agent
wait_for_committed_desktop_readiness
browser_identity_is_exact "$PROJECT_DIR" "$RELEASE_ENV" "$APP_ENV" \
  || die "browser-agent namespace binding changed before transaction completion"
browser_maintenance_checkpoint \
  || die "browser maintenance lease expired before transaction completion"
configured_vision_contract_is_unchanged \
  || die "Vision profile/revision changed before transaction completion"
activate_desktop_units
browser_maintenance_checkpoint \
  || die "browser maintenance lease expired during desktop unit activation"
configured_vision_contract_is_unchanged \
  || die "Vision profile/revision changed during desktop unit activation"
run_before_deadline "desktop_transaction_complete" \
  "$SCRIPT_DIR/platform-desktop-transaction.sh" complete --expect candidate
ROLLBACK_ARMED=false
trap - ERR
TRANSACTION_PREPARED=false
STATE_COMMITTED=false
release_browser_maintenance
log "desktop release $release_id is active"
