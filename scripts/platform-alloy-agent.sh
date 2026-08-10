#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly CURRENT_DIR="${FB_AGENT_RELEASE_DIR:-$ROOT_DIR/current}"
readonly STATE_DIR="$ROOT_DIR/shared"
readonly DEPLOY_LOCK_FILE="$STATE_DIR/deploy.lock"
if [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
  [[ "${FB_AGENT_VERIFIED_RELEASE_SCHEMA}" == "fb-agent-verified-release-exec/v1" \
    && -n "${FB_AGENT_ACTIVE_STATE_DIR:-}" ]] \
    || { printf 'ERROR: verified application state is invalid\n' >&2; exit 1; }
  readonly APP_STATE_DIR="$FB_AGENT_ACTIVE_STATE_DIR"
else
  readonly APP_STATE_DIR="$ROOT_DIR/shared/active-state"
fi
readonly AGENT_ENV="$ROOT_DIR/shared/alloy-agent.env"
readonly COMPOSE_FILE="$CURRENT_DIR/deploy/monitoring/docker-compose.agent.yml"
readonly CURRENT_RELEASE_ENV="$CURRENT_DIR/release-images.env"
readonly CANONICAL_PROJECT="${ALLOY_CANONICAL_PROJECT:-fb_agent_telemetry_agent}"
readonly CANDIDATE_PROJECT="${ALLOY_CANDIDATE_PROJECT:-fb_agent_telemetry_candidate}"
readonly CANONICAL_HOST_PORT="${ALLOY_AGENT_HOST_PORT:-12345}"
readonly CANDIDATE_HOST_PORT="${ALLOY_CANDIDATE_HOST_PORT:-22345}"
readonly CANONICAL_NETWORK_ALIAS="alloy-agent"
readonly CANDIDATE_NETWORK_ALIAS="alloy-agent-candidate"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

for command in curl docker flock grep python3 readlink sed sleep stat; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable"
for file in "$AGENT_ENV" "$COMPOSE_FILE"; do
  [[ -f "$file" && ! -L "$file" ]] \
    || die "required regular Alloy agent file is missing: $file"
done
if [[ -z "${FB_AGENT_RELEASE_ID:-}" ]]; then
  [[ -f "$CURRENT_RELEASE_ENV" && ! -L "$CURRENT_RELEASE_ENV" ]] \
    || die "current immutable release manifest is missing: $CURRENT_RELEASE_ENV"
fi
[[ "$(stat -Lc '%a' "$AGENT_ENV")" == "600" ]] \
  || die "$AGENT_ENV must have mode 600"
for image_key in ALLOY_IMAGE NODE_EXPORTER_IMAGE CADVISOR_IMAGE; do
  image="$(sed -n "s/^${image_key}=//p" "$AGENT_ENV" | tail -n 1)"
  [[ "$image" =~ ^[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] \
    || die "$image_key must be an immutable image@sha256 reference"
done
for project in "$CANONICAL_PROJECT" "$CANDIDATE_PROJECT"; do
  [[ "$project" =~ ^[a-z0-9][a-z0-9_-]+$ ]] \
    || die "unsafe Alloy Compose project: $project"
done
[[ "$CANONICAL_PROJECT" != "$CANDIDATE_PROJECT" ]] \
  || die "candidate Alloy project must be isolated from the canonical project"
for port in "$CANONICAL_HOST_PORT" "$CANDIDATE_HOST_PORT"; do
  [[ "$port" =~ ^[1-9][0-9]{3,4}$ && "$port" -le 65535 ]] \
    || die "invalid Alloy host port: $port"
done
[[ "$CANONICAL_HOST_PORT" != "$CANDIDATE_HOST_PORT" ]] \
  || die "candidate Alloy port must differ from the canonical port"
agent_platform_network="$(sed -n 's/^PLATFORM_NETWORK=//p' "$AGENT_ENV" | tail -n 1)"
PLATFORM_NETWORK="${PLATFORM_NETWORK:-${agent_platform_network:-fb_agent_safety_first_platform}}"
[[ "$PLATFORM_NETWORK" == fb_agent_safety_first_platform ]] \
  || die "Alloy must use the canonical safety-first platform network"
export PLATFORM_NETWORK
CURRENT_RELEASE_ID="${FB_AGENT_RELEASE_ID:-$(
  sed -n 's/^RELEASE_ID=//p' "$CURRENT_RELEASE_ENV" | tail -n 1
)}"
[[ "$CURRENT_RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] \
  || die "current telemetry release id is invalid"
FB_AGENT_BOOTSTRAP_CLUSTER_ID="${FB_AGENT_BOOTSTRAP_CLUSTER_ID:-$(
  sed -n 's/^FB_AGENT_BOOTSTRAP_CLUSTER_ID=//p' \
    "$APP_STATE_DIR/app.env" 2>/dev/null | tail -n 1
)}"
[[ "$FB_AGENT_BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] \
  || die "telemetry bootstrap cluster id is invalid"
export FB_AGENT_BOOTSTRAP_CLUSTER_ID

export ALLOY_AGENT_ENV_FILE="$AGENT_ENV"

acquire_runtime_mutation_lock() {
  local inherited_fd=""
  local canonical_active_state=""

  inherited_fd="${FB_AGENT_DEPLOY_LOCK_FD:-}"
  if [[ -n "$inherited_fd" ]]; then
    [[ "$inherited_fd" =~ ^[0-9]+$ ]] \
      || die "inherited deployment lock fd is invalid"
    python3 - "$inherited_fd" "$DEPLOY_LOCK_FILE" <<'PY' \
      || die "inherited deployment lock does not guard the canonical lock file"
import os
import sys

try:
    descriptor = os.fstat(int(sys.argv[1]))
    target = os.stat(sys.argv[2], follow_symlinks=True)
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(
    0
    if (descriptor.st_dev, descriptor.st_ino) == (target.st_dev, target.st_ino)
    else 1
)
PY
    flock -n "$inherited_fd" \
      || die "inherited deployment lock is not held"
  elif [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
    exec 9>"$DEPLOY_LOCK_FILE"
    flock -n 9 || die "another deployment or reconciliation is already running"
    export FB_AGENT_DEPLOY_LOCK_FD=9
  else
    die "mutating runtime command requires the verified launcher or inherited deployment lock"
  fi

  [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]] || return 0
  canonical_active_state="$(
    python3 - "$STATE_DIR/active-state" <<'PY'
import sys
from pathlib import Path

pointer = Path(sys.argv[1])
if not pointer.is_symlink():
    raise SystemExit(1)
try:
    print(pointer.resolve(strict=True))
except OSError:
    raise SystemExit(1)
PY
  )" || die "canonical active application state is unavailable"
  [[ "$canonical_active_state" == "$APP_STATE_DIR" ]] \
    || die "active application state changed after verification; refusing mutation"
}

compose_for() {
  local -r project="$1"
  local -r host_port="$2"
  local -r network_alias="$3"
  local -r compose_file="$4"
  local -r release_id="$5"
  shift 5
  ALLOY_AGENT_HOST_PORT="$host_port" \
    ALLOY_AGENT_NETWORK_ALIAS="$network_alias" \
    FB_AGENT_TELEMETRY_RELEASE_ID="$release_id" \
    docker compose -p "$project" --env-file "$AGENT_ENV" -f "$compose_file" "$@"
}

canonical_compose() {
  compose_for "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
    "$CANONICAL_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" "$@"
}

candidate_compose() {
  compose_for "$CANDIDATE_PROJECT" "$CANDIDATE_HOST_PORT" \
    "$CANDIDATE_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" "$@"
}

remote_backends_ready() {
  local key=""
  local transport=""
  local status=""
  local url=""
  local expected_suffix=""
  transport="$(sed -n 's/^MONITORING_TRANSPORT=//p' "$AGENT_ENV" | tail -n 1)"
  [[ "$transport" == "private_https" || "$transport" == "same_host" ]] || return 1
  for key in PROMETHEUS_READY_URL LOKI_READY_URL TEMPO_READY_URL; do
    url="$(sed -n "s/^${key}=//p" "$AGENT_ENV" | tail -n 1)"
    if [[ "$transport" == "private_https" ]]; then
      [[ "$url" == https://* ]] || return 1
    else
      case "$key:$url" in
        PROMETHEUS_READY_URL:http://172.17.0.1:9090/-/ready|\
        LOKI_READY_URL:http://172.17.0.1:3100/ready|\
        TEMPO_READY_URL:http://172.17.0.1:3200/ready) ;;
        *) return 1 ;;
      esac
    fi
    [[ "$url" != *"@"* && "$url" != *"?"* && "$url" != *"#"* ]] || return 1
    case "$key" in
      PROMETHEUS_READY_URL) expected_suffix="/-/ready" ;;
      LOKI_READY_URL|TEMPO_READY_URL) expected_suffix="/ready" ;;
    esac
    [[ "$url" == *"$expected_suffix" ]] || return 1
    status="$(curl --silent --show-error --output /dev/null \
      --write-out '%{http_code}' --max-time 5 "$url")" || return 1
    [[ "$status" =~ ^2[0-9][0-9]$ ]] || return 1
  done
}

local_agent_ready() {
  local -r project="$1"
  local -r host_port="$2"
  local -r network_alias="$3"
  local -r compose_file="$4"
  local -r release_id="$5"
  local running=""
  local status=""
  status="$(curl --silent --show-error --output /dev/null \
    --write-out '%{http_code}' --max-time 3 \
    "http://127.0.0.1:${host_port}/-/ready")" || return 1
  [[ "$status" =~ ^2[0-9][0-9]$ ]] || return 1
  running="$(compose_for "$project" "$host_port" "$network_alias" \
    "$compose_file" "$release_id" ps --status running --services)" || return 1
  grep -qx alloy-agent <<<"$running" || return 1
  grep -qx node-exporter <<<"$running" || return 1
  grep -qx cadvisor <<<"$running" || return 1
}

project_release_matches() {
  local -r project="$1"
  local -r host_port="$2"
  local -r network_alias="$3"
  local -r compose_file="$4"
  local -r release_id="$5"
  local service="" container_id="" actual_release=""
  for service in alloy-agent node-exporter cadvisor; do
    container_id="$(compose_for "$project" "$host_port" "$network_alias" \
      "$compose_file" "$release_id" ps -q "$service")" || return 1
    [[ -n "$container_id" && "$container_id" != *$'\n'* ]] || return 1
    actual_release="$(FB_AGENT_TELEMETRY_RELEASE_ID="$release_id" \
      docker inspect --format \
      '{{index .Config.Labels "com.fb-agent.release"}}' "$container_id")" \
      || return 1
    [[ "$actual_release" == "$release_id" ]] || return 1
  done
}

wait_local_agent() {
  local -r project="$1"
  local -r host_port="$2"
  local -r network_alias="$3"
  local -r compose_file="$4"
  local -r release_id="$5"
  local attempt=0
  for ((attempt = 1; attempt <= 30; attempt++)); do
    if local_agent_ready "$project" "$host_port" "$network_alias" \
      "$compose_file" "$release_id" \
      && project_release_matches "$project" "$host_port" "$network_alias" \
        "$compose_file" "$release_id"; then
      return 0
    fi
    sleep 2
  done
  return 1
}

canonical_ready() {
  local_agent_ready "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
    "$CANONICAL_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" || return 1
  project_release_matches "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
    "$CANONICAL_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" || return 1
  remote_backends_ready || return 1
}

candidate_ready() {
  local_agent_ready "$CANDIDATE_PROJECT" "$CANDIDATE_HOST_PORT" \
    "$CANDIDATE_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" || return 1
  project_release_matches "$CANDIDATE_PROJECT" "$CANDIDATE_HOST_PORT" \
    "$CANDIDATE_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" || return 1
  remote_backends_ready || return 1
}

candidate_cleanup() {
  candidate_compose down --volumes --remove-orphans
}

candidate_start() {
  candidate_compose config --quiet || return 1
  remote_backends_ready || return 1
  # A deploy lock guarantees there is at most one candidate. Remove only the
  # isolated candidate project left by a killed preflight; the incumbent
  # canonical project is never addressed on this path.
  candidate_cleanup || return 1
  candidate_compose pull alloy-agent node-exporter cadvisor || return 1
  candidate_compose up -d alloy-agent node-exporter cadvisor || return 1
  wait_local_agent "$CANDIDATE_PROJECT" "$CANDIDATE_HOST_PORT" \
    "$CANDIDATE_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" || return 1
  remote_backends_ready || return 1
}

restore_incumbent() {
  local -r previous_release_dir="$1"
  local previous_compose=""
  local previous_release_id=""

  if [[ -z "$previous_release_dir" ]]; then
    canonical_compose down --remove-orphans || return 1
    return 0
  fi
  previous_compose="$previous_release_dir/deploy/monitoring/docker-compose.agent.yml"
  previous_release_id="$(sed -n 's/^RELEASE_ID=//p' \
    "$previous_release_dir/release-images.env" | tail -n 1)"
  [[ "$previous_release_id" =~ ^[A-Za-z0-9._-]+$ ]] || return 1
  compose_for "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
    "$CANONICAL_NETWORK_ALIAS" "$previous_compose" "$previous_release_id" \
    config --quiet || return 1
  canonical_compose down --remove-orphans || return 1
  compose_for "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
    "$CANONICAL_NETWORK_ALIAS" "$previous_compose" "$previous_release_id" \
    up -d alloy-agent node-exporter cadvisor || return 1
  wait_local_agent "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
    "$CANONICAL_NETWORK_ALIAS" "$previous_compose" "$previous_release_id" \
    || return 1
  remote_backends_ready || return 1
}

prevalidate_incumbent() {
  local -r previous_release_dir="$1"
  local previous_compose=""
  local previous_release_env=""
  local previous_release_id=""
  local image=""
  [[ -n "$previous_release_dir" ]] || return 0
  previous_compose="$previous_release_dir/deploy/monitoring/docker-compose.agent.yml"
  previous_release_env="$previous_release_dir/release-images.env"
  for file in "$previous_compose" "$previous_release_env"; do
    [[ -f "$file" && ! -L "$file" ]] || return 1
  done
  python3 "$CURRENT_DIR/scripts/release-state.py" manifest-verify \
    --release-dir "$previous_release_dir" \
    --manifest "$previous_release_dir/.fb-agent-source-manifest.json" \
    --require-read-only >/dev/null || return 1
  previous_release_id="$(sed -n 's/^RELEASE_ID=//p' \
    "$previous_release_env" | tail -n 1)"
  [[ "$previous_release_id" =~ ^[A-Za-z0-9._-]+$ ]] || return 1
  compose_for "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
    "$CANONICAL_NETWORK_ALIAS" "$previous_compose" "$previous_release_id" \
    config --quiet || return 1
  while IFS= read -r image; do
    [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || return 1
  done < <(
    compose_for "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
      "$CANONICAL_NETWORK_ALIAS" "$previous_compose" "$previous_release_id" \
      config --images
  )
}

promote_candidate() {
  canonical_compose config --quiet || return 1
  candidate_ready || return 1
  # This is the first canonical-project mutation and is invoked only after the
  # application release has committed. The isolated candidate remains healthy
  # until the replacement is proven.
  canonical_compose down --remove-orphans || return 1
  canonical_compose up -d alloy-agent node-exporter cadvisor || return 1
  wait_local_agent "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
    "$CANONICAL_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" || return 1
  remote_backends_ready || return 1
}

command="${1:-status}"
shift || true
case "$command" in
  up|candidate-up|candidate-cleanup|promote|stop|restart|compose)
    acquire_runtime_mutation_lock
    ;;
esac
case "$command" in
  up)
    (($# == 0)) || die "up does not accept arguments"
    canonical_compose config --quiet
    remote_backends_ready \
      || die "private Prometheus, Loki or Tempo readiness endpoint is not healthy"
    canonical_compose pull alloy-agent node-exporter cadvisor
    canonical_compose up -d alloy-agent node-exporter cadvisor
    wait_local_agent "$CANONICAL_PROJECT" "$CANONICAL_HOST_PORT" \
      "$CANONICAL_NETWORK_ALIAS" "$COMPOSE_FILE" "$CURRENT_RELEASE_ID" \
      || die "Alloy agent did not become ready within 60 seconds"
    remote_backends_ready \
      || die "private monitoring backend readiness failed after Alloy start"
    printf 'Application-host telemetry agent and exporters are ready\n'
    ;;
  candidate-up)
    (($# == 0)) || die "candidate-up does not accept arguments"
    candidate_status=0
    candidate_start || candidate_status=$?
    if ((candidate_status == 0)); then
      printf 'Isolated Alloy candidate and exporters are ready\n'
      exit 0
    fi
    cleanup_status=0
    candidate_cleanup || cleanup_status=$?
    if ((cleanup_status != 0)); then
      die "Alloy candidate preflight failed and isolated candidate cleanup also failed"
    fi
    die "Alloy candidate preflight failed; isolated candidate was removed"
    ;;
  candidate-ready)
    (($# == 0)) || die "candidate-ready does not accept arguments"
    candidate_ready || die "isolated Alloy candidate is not ready"
    printf 'Isolated Alloy candidate and exporters are ready\n'
    ;;
  candidate-cleanup)
    (($# == 0)) || die "candidate-cleanup does not accept arguments"
    candidate_cleanup
    printf 'Isolated Alloy candidate was removed\n'
    ;;
  promote)
    previous_release_dir=""
    while (($#)); do
      case "$1" in
        --previous-release-dir)
          previous_release_dir="${2:?missing --previous-release-dir value}"
          shift 2
          ;;
        *) die "unknown promote argument: $1" ;;
      esac
    done
    if [[ -n "$previous_release_dir" ]]; then
      canonical_previous="$(readlink -f "$previous_release_dir")"
      [[ "$canonical_previous" == "$previous_release_dir" \
        && "$canonical_previous" == "$ROOT_DIR/releases/"* ]] \
        || die "previous release directory is outside the immutable release root"
      previous_compose="$previous_release_dir/deploy/monitoring/docker-compose.agent.yml"
      [[ -f "$previous_compose" && ! -L "$previous_compose" ]] \
        || die "previous Alloy Compose file is unavailable"
    fi
    prevalidate_incumbent "$previous_release_dir" \
      || die "previous Alloy rollback contract failed prevalidation"
    if canonical_ready; then
      candidate_cleanup \
        || die "current Alloy release is ready but candidate cleanup failed"
      printf 'Alloy release was already adopted; isolated candidate was removed\n'
      exit 0
    fi
    if ! candidate_ready; then
      candidate_start \
        || die "Alloy candidate could not be reconstructed for post-commit adoption"
    fi
    promotion_status=0
    promote_candidate || promotion_status=$?
    if ((promotion_status == 0)); then
      candidate_cleanup \
        || die "Alloy was promoted but isolated candidate cleanup failed"
      printf 'Alloy candidate was promoted after application commit\n'
      exit 0
    fi
    restore_status=0
    restore_incumbent "$previous_release_dir" || restore_status=$?
    cleanup_status=0
    candidate_cleanup || cleanup_status=$?
    if ((restore_status != 0 || cleanup_status != 0)); then
      die "CRITICAL: Alloy promotion failed and incumbent restore or candidate cleanup failed"
    fi
    die "Alloy promotion failed; incumbent was restored and candidate removed"
    ;;
  ready)
    (($# == 0)) || die "ready does not accept arguments"
    canonical_ready || die "Alloy or a required host exporter/backend is not ready"
    printf 'Application-host telemetry agent and exporters are ready\n'
    ;;
  stop)
    (($# == 0)) || die "stop does not accept arguments"
    canonical_compose stop --timeout 30 alloy-agent node-exporter cadvisor
    ;;
  restart)
    (($# == 0)) || die "restart does not accept arguments"
    canonical_compose stop --timeout 30 alloy-agent node-exporter cadvisor
    exec "$0" up
    ;;
  status) canonical_compose ps "$@" ;;
  logs) canonical_compose logs --tail="${LOG_TAIL:-200}" "$@" ;;
  compose) canonical_compose "$@" ;;
  *) die "unsupported command: $command" ;;
esac
