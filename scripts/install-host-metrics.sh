#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
readonly INSTALL_DIR="${FB_AGENT_HOST_METRICS_INSTALL_DIR:-/usr/local/libexec/fb-agent-host-metrics}"
readonly TEXTFILE_DIR="${FB_AGENT_TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
readonly STATE_DIR="${FB_AGENT_HOST_METRICS_STATE_DIR:-/var/lib/fb-agent/host-metrics}"
readonly UNIT_DIR="/etc/systemd/system"
VERIFY_ONLY=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --verify-only) VERIFY_ONLY=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done
[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
for command in install python3 stat systemctl; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
for path in "$INSTALL_DIR" "$TEXTFILE_DIR" "$STATE_DIR"; do
  [[ "$path" = /* && "$path" != *".."* ]] || die "unsafe absolute path: $path"
  [[ ! -L "$path" ]] || die "refusing symlinked directory: $path"
done

verify_layout() {
  [[ -x "$INSTALL_DIR/host_metrics.py" && ! -L "$INSTALL_DIR/host_metrics.py" ]] \
    || die "installed host metrics writer is missing or symlinked"
  [[ -f "$UNIT_DIR/fb-agent-host-operation-failed@.service" \
    && ! -L "$UNIT_DIR/fb-agent-host-operation-failed@.service" ]] \
    || die "host operation failure unit is missing or symlinked"
  [[ "$(stat -Lc '%U:%G:%a' "$INSTALL_DIR/host_metrics.py")" == "root:root:755" ]] \
    || die "host metrics writer must be root:root mode 755"
  [[ "$(stat -Lc '%U:%G:%a' "$TEXTFILE_DIR")" == "root:root:755" ]] \
    || die "textfile directory must be root:root mode 755"
  [[ "$(stat -Lc '%U:%G:%a' "$STATE_DIR")" == "root:root:700" ]] \
    || die "host metric state directory must be root:root mode 700"
}
if [[ "$VERIFY_ONLY" == true ]]; then
  verify_layout
  printf 'Root-owned atomic host metrics writer is installed\n'
  exit 0
fi

install -d -o root -g root -m 0755 "$INSTALL_DIR" "$TEXTFILE_DIR"
install -d -o root -g root -m 0700 "$STATE_DIR"
install -o root -g root -m 0755 "$SCRIPT_DIR/host_metrics.py" \
  "$INSTALL_DIR/host_metrics.py"
install -o root -g root -m 0644 \
  "$PROJECT_DIR/deploy/systemd/fb-agent-host-operation-failed@.service" \
  "$UNIT_DIR/fb-agent-host-operation-failed@.service"

verify_layout
systemctl daemon-reload
printf 'Installed root-owned atomic host metrics writer\n'
