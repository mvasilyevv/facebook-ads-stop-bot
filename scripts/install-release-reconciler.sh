#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly INSTALL_ROOT="${FB_AGENT_RECONCILER_INSTALL_ROOT:-/usr/local/libexec/fb-agent-release-reconciler}"
readonly VERIFIER_INSTALL_ROOT="${FB_AGENT_VERIFIER_INSTALL_ROOT:-/usr/local/libexec/fb-agent-release-verifier}"
readonly UNIT_PATH="/etc/systemd/system/fb-agent-release-reconcile.service"
TEMP_DIR=""
RECONCILER_LINK=""
VERIFIER_LINK=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
  [[ -z "$RECONCILER_LINK" ]] || rm -f -- "$RECONCILER_LINK"
  [[ -z "$VERIFIER_LINK" ]] || rm -f -- "$VERIFIER_LINK"
}
trap cleanup EXIT

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
[[ "$ROOT_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "FB_AGENT_ROOT is unsafe"
for command in chmod chown cmp cut install ln mktemp mv python3 sha256sum stat systemctl; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
python3 "$SCRIPT_DIR/release-state.py" manifest-verify \
  --release-dir "$PROJECT_DIR" \
  --manifest "$PROJECT_DIR/.fb-agent-source-manifest.json" \
  --require-read-only >/dev/null

readonly -a RUNTIME_FILES=(
  browser-control-env.sh
  host_metrics.py
  release-state.py
  reconcile-platform-release.sh
  bluegreen-switch-caddy.sh
  bluegreen-worker-handoff.sh
  sync-caddy-env.py
)
readonly -a VERIFIER_FILES=(
  release-state.py
  verified-release-exec.py
)
for file in "${RUNTIME_FILES[@]}"; do
  [[ -f "$SCRIPT_DIR/$file" ]] || die "reconciler runtime file is missing: $file"
done
for file in "${VERIFIER_FILES[@]}"; do
  [[ -f "$SCRIPT_DIR/$file" ]] || die "release verifier runtime file is missing: $file"
done
"$SCRIPT_DIR/install-host-metrics.sh"

runtime_digest="$({
  for file in "${RUNTIME_FILES[@]}"; do
    printf '%s %s\n' "$(sha256sum "$SCRIPT_DIR/$file" | cut -d' ' -f1)" "$file"
  done
} | sha256sum | cut -c1-16)"
[[ "$runtime_digest" =~ ^[0-9a-f]{16}$ ]] || die "failed to derive runtime digest"
destination="$INSTALL_ROOT/releases/$runtime_digest"
install -d -o root -g root -m 0755 "$INSTALL_ROOT/releases"
if [[ ! -d "$destination" ]]; then
  TEMP_DIR="$(mktemp -d "$INSTALL_ROOT/releases/.prepare-XXXXXXXX")"
  for file in "${RUNTIME_FILES[@]}"; do
    mode=0555
    [[ "$file" == *.py ]] && mode=0444
    install -o root -g root -m "$mode" "$SCRIPT_DIR/$file" "$TEMP_DIR/$file"
  done
  chown root:root "$TEMP_DIR"
  chmod 0555 "$TEMP_DIR"
  mv -- "$TEMP_DIR" "$destination"
  TEMP_DIR=""
else
  for file in "${RUNTIME_FILES[@]}"; do
    cmp -s -- "$SCRIPT_DIR/$file" "$destination/$file" \
      || die "installed reconciler digest collision: $destination/$file"
    expected_mode=555
    [[ "$file" == *.py ]] && expected_mode=444
    [[ "$(stat -Lc '%u:%g:%a' "$destination/$file")" == "0:0:$expected_mode" ]] \
      || die "installed reconciler runtime is not sealed: $destination/$file"
  done
  [[ "$(stat -Lc '%u:%g:%a' "$destination")" == "0:0:555" ]] \
    || die "installed reconciler directory is not sealed: $destination"
fi

verifier_digest="$({
  for file in "${VERIFIER_FILES[@]}"; do
    printf '%s %s\n' "$(sha256sum "$SCRIPT_DIR/$file" | cut -d' ' -f1)" "$file"
  done
} | sha256sum | cut -c1-16)"
[[ "$verifier_digest" =~ ^[0-9a-f]{16}$ ]] \
  || die "failed to derive release verifier digest"
verifier_destination="$VERIFIER_INSTALL_ROOT/releases/$verifier_digest"
install -d -o root -g root -m 0755 "$VERIFIER_INSTALL_ROOT/releases"
if [[ ! -d "$verifier_destination" ]]; then
  TEMP_DIR="$(mktemp -d "$VERIFIER_INSTALL_ROOT/releases/.prepare-XXXXXXXX")"
  install -o root -g root -m 0444 \
    "$SCRIPT_DIR/release-state.py" "$TEMP_DIR/release-state.py"
  install -o root -g root -m 0555 \
    "$SCRIPT_DIR/verified-release-exec.py" "$TEMP_DIR/verified-release-exec.py"
  chown root:root "$TEMP_DIR"
  chmod 0555 "$TEMP_DIR"
  mv -- "$TEMP_DIR" "$verifier_destination"
  TEMP_DIR=""
else
  for contract in \
    "release-state.py:444" \
    "verified-release-exec.py:555"; do
    file="${contract%%:*}"
    expected_mode="${contract#*:}"
    cmp -s -- "$SCRIPT_DIR/$file" "$verifier_destination/$file" \
      || die "installed release verifier digest collision: $verifier_destination/$file"
    [[ "$(stat -Lc '%u:%g:%a' "$verifier_destination/$file")" \
      == "0:0:$expected_mode" ]] \
      || die "installed release verifier runtime is not sealed: $verifier_destination/$file"
  done
  [[ "$(stat -Lc '%u:%g:%a' "$verifier_destination")" == "0:0:555" ]] \
    || die "installed release verifier directory is not sealed: $verifier_destination"
fi

RECONCILER_LINK="$INSTALL_ROOT/.current.$$.new"
VERIFIER_LINK="$VERIFIER_INSTALL_ROOT/.current.$$.new"
ln -s "$destination" "$RECONCILER_LINK"
mv -Tf -- "$RECONCILER_LINK" "$INSTALL_ROOT/current"
RECONCILER_LINK=""
ln -s "$verifier_destination" "$VERIFIER_LINK"
mv -Tf -- "$VERIFIER_LINK" "$VERIFIER_INSTALL_ROOT/current"
VERIFIER_LINK=""
install -d -m 0755 /etc/fb-agent
printf 'FB_AGENT_ROOT=%s\n' "$ROOT_DIR" \
  | install -m 0600 /dev/stdin /etc/fb-agent/release-reconciler.env
install -m 0644 \
  "$PROJECT_DIR/deploy/systemd/fb-agent-release-reconcile.service" "$UNIT_PATH"
systemctl daemon-reload
systemctl enable fb-agent-release-reconcile.service >/dev/null
printf 'Installed crash reconciler %s and release verifier %s\n' \
  "$runtime_digest" "$verifier_digest"
