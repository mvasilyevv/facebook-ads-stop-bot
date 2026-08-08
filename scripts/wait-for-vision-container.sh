#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
DESKTOP_RELEASE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly DESKTOP_RELEASE_DIR
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly STATE_DIR="$ROOT_DIR/shared"
VERIFIED_RUNTIME=false
if [[ -n "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" ]]; then
  [[ "${FB_AGENT_VERIFIED_RELEASE_SCHEMA}" == "fb-agent-verified-release-exec/v1" \
    && -n "${FB_AGENT_ACTIVE_STATE_DIR:-}" ]] \
    || { printf 'ERROR: verified desktop state is invalid\n' >&2; exit 1; }
  ACTIVE_STATE="$FB_AGENT_ACTIVE_STATE_DIR"
  VERIFIED_RUNTIME=true
else
  ACTIVE_STATE="$STATE_DIR/active-desktop-state"
fi
readonly ACTIVE_STATE VERIFIED_RUNTIME
readonly DESKTOP_STATES_DIR="$STATE_DIR/desktop-states"
readonly VISION_CONTAINER="vision-webtop"
readonly VISION_COMPOSE_PROJECT="fb_agent_vision"
readonly KASMVNC_COMPOSE_SERVICE="kasmvnc"
readonly TIMEOUT_SECONDS="${VISION_WAIT_TIMEOUT_SECONDS:-180}"
readonly POLL_SECONDS="${VISION_WAIT_INTERVAL_SECONDS:-2}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

dotenv_value() {
  local -r file="$1"
  local -r key="$2"
  awk -v expected_key="$key" '
    index($0, expected_key "=") == 1 {
      count += 1
      value = substr($0, length(expected_key) + 2)
    }
    END {
      if (count == 1 && length(value) > 0) {
        print value
      } else {
        exit 1
      }
    }
  ' "$file"
}

validate_owned_path() {
  local -r path="$1"
  local -r expected_mode="$2"
  local -r expected_kind="$3"
  python3 - "$path" "$expected_mode" "$expected_kind" <<'PY'
import os
import stat
import sys

path, raw_mode, expected_kind = sys.argv[1:]
try:
    metadata = os.lstat(path)
except OSError:
    raise SystemExit(1)
kind_ok = (
    stat.S_ISDIR(metadata.st_mode)
    if expected_kind == "directory"
    else stat.S_ISREG(metadata.st_mode)
)
ok = (
    kind_ok
    and not stat.S_ISLNK(metadata.st_mode)
    and stat.S_IMODE(metadata.st_mode) == int(raw_mode, 8)
    and metadata.st_uid == os.geteuid()
    and metadata.st_gid == os.getegid()
)
raise SystemExit(0 if ok else 1)
PY
}

[[ "$TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] \
  || die "VISION_WAIT_TIMEOUT_SECONDS must be a non-negative integer"
[[ "$POLL_SECONDS" =~ ^[0-9]+$ ]] \
  || die "VISION_WAIT_INTERVAL_SECONDS must be a non-negative integer"
for command in awk dirname docker python3 readlink sleep; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done

if [[ "$VERIFIED_RUNTIME" == true ]]; then
  [[ -d "$ACTIVE_STATE" && ! -L "$ACTIVE_STATE" \
    && "$(readlink -f "$ACTIVE_STATE")" == "$ACTIVE_STATE" \
    && "$(dirname -- "$ACTIVE_STATE")" == "$DESKTOP_STATES_DIR" ]] \
    || die "verified desktop state directory is unsafe"
  active_state_dir="$ACTIVE_STATE"
else
  [[ -L "$ACTIVE_STATE" ]] || die "active desktop state pointer is missing"
  active_state_dir="$(readlink -f "$ACTIVE_STATE")"
fi
[[ -d "$active_state_dir" && ! -L "$active_state_dir" \
  && "$(dirname -- "$active_state_dir")" == "$DESKTOP_STATES_DIR" ]] \
  || die "active desktop state pointer is unsafe"
validate_owned_path "$active_state_dir" 0700 directory \
  || die "active desktop state directory is unsafe"
app_env="$active_state_dir/app.env"
release_env="$active_state_dir/release-images.env"
for file in "$app_env" "$release_env"; do
  validate_owned_path "$file" 0600 file \
    || die "active desktop state file is missing or unsafe: $file"
done
[[ -L "$active_state_dir/release" \
  && "$(readlink -f "$active_state_dir/release")" == "$DESKTOP_RELEASE_DIR" ]] \
  || die "boot consumer does not match the committed desktop release"

expected_image="$(dotenv_value "$release_env" DESKTOP_WEBTOP_IMAGE)" \
  || die "committed Vision image is missing"
expected_kasm_image="$(dotenv_value "$release_env" DESKTOP_KASMVNC_IMAGE)" \
  || die "committed KasmVNC image is missing"
expected_release="$(dotenv_value "$release_env" RELEASE_ID)" \
  || die "committed Vision release identity is missing"
expected_cluster="$(dotenv_value "$app_env" FB_AGENT_BOOTSTRAP_CLUSTER_ID)" \
  || die "committed bootstrap cluster identity is missing"
[[ "$expected_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
  || die "committed Vision image is not digest-pinned"
[[ "$expected_kasm_image" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
  || die "committed KasmVNC image is not digest-pinned"
[[ "$expected_release" =~ ^[A-Za-z0-9._-]{1,128}$ ]] \
  || die "committed Vision release identity is invalid"
[[ "$expected_cluster" =~ ^[0-9a-f]{32}$ ]] \
  || die "committed bootstrap cluster identity is invalid"

expected_webtop_identity="$(
  printf '/%s|true|healthy|%s|fb_agent_vision|webtop|true|%s|vision|%s' \
    "$VISION_CONTAINER" \
    "$expected_image" \
    "$expected_cluster" \
    "$expected_release"
)"
readonly expected_webtop_identity
readonly DEADLINE=$((SECONDS + TIMEOUT_SECONDS))
while :; do
  webtop_id="$(
    docker inspect --format '{{.Id}}' "$VISION_CONTAINER" 2>/dev/null
  )" || webtop_id=""
  observed_webtop_identity="$(
    docker inspect --format \
      '{{.Name}}|{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}|{{.Config.Image}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.fb-agent.managed"}}|{{index .Config.Labels "com.fb-agent.cluster-id"}}|{{index .Config.Labels "com.fb-agent.purpose"}}|{{index .Config.Labels "com.fb-agent.release"}}' \
      "$VISION_CONTAINER" 2>/dev/null
  )" || observed_webtop_identity=""
  kasmvnc_container_id="$(
    docker ps -a \
      --filter "label=com.docker.compose.project=${VISION_COMPOSE_PROJECT}" \
      --filter "label=com.docker.compose.service=${KASMVNC_COMPOSE_SERVICE}" \
      --format '{{.ID}}' 2>/dev/null
  )" || kasmvnc_container_id=""
  observed_kasm_identity=""
  if [[ -n "$webtop_id" && "$webtop_id" != *$'\n'* \
    && -n "$kasmvnc_container_id" && "$kasmvnc_container_id" != *$'\n'* ]]; then
    observed_kasm_identity="$(
      docker inspect --format \
        '{{.State.Running}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}|{{.Config.Image}}|{{index .Config.Labels "com.docker.compose.project"}}|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.fb-agent.managed"}}|{{index .Config.Labels "com.fb-agent.cluster-id"}}|{{index .Config.Labels "com.fb-agent.purpose"}}|{{index .Config.Labels "com.fb-agent.release"}}|{{.HostConfig.NetworkMode}}|{{.HostConfig.IpcMode}}' \
        "$kasmvnc_container_id" 2>/dev/null
    )" || observed_kasm_identity=""
  fi
  expected_kasm_identity="$(
    printf 'true|healthy|%s|fb_agent_vision|kasmvnc|true|%s|vision|%s|container:%s|container:%s' \
      "$expected_kasm_image" \
      "$expected_cluster" \
      "$expected_release" \
      "$webtop_id" \
      "$webtop_id"
  )"
  if [[ "$observed_webtop_identity" == "$expected_webtop_identity" \
    && "$observed_kasm_identity" == "$expected_kasm_identity" ]]; then
    printf 'Exact committed Vision/Kasm namespace is healthy: %s\n' \
      "$VISION_CONTAINER"
    exit 0
  fi
  if ((SECONDS >= DEADLINE)); then
    break
  fi
  sleep "$POLL_SECONDS"
done

printf 'ERROR: exact committed Vision/Kasm namespace did not become healthy within %ss: %s\n' \
  "$TIMEOUT_SECONDS" "$VISION_CONTAINER" >&2
exit 1
