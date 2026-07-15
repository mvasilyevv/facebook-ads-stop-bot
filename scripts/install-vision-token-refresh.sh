#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly SOURCE_DIR="$PROJECT_DIR/deploy"
readonly ENV_PATH="${FB_AGENT_ENV:-/opt/fb-agent/shared/.env}"

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { printf 'ERROR: run as root\n' >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf 'ERROR: python3 is unavailable\n' >&2; exit 1; }
command -v systemctl >/dev/null 2>&1 || { printf 'ERROR: systemctl is unavailable\n' >&2; exit 1; }
[[ -f "$ENV_PATH" ]] || { printf 'ERROR: production env is missing: %s\n' "$ENV_PATH" >&2; exit 1; }

for key in VISION_USERNAME VISION_PASSWORD VISION_TEAM_ID VISION_FOLDER_ID VISION_PROFILE_ID; do
  grep -Eq "^${key}=.+" "$ENV_PATH" || {
    printf 'ERROR: %s is missing or empty in %s\n' "$key" "$ENV_PATH" >&2
    exit 1
  }
done

python3 -m py_compile "$SOURCE_DIR/vision-refresh-token.py"
install -m 0755 "$SOURCE_DIR/vision-refresh-token.py" /usr/local/bin/vision-refresh-token.py
install -m 0644 "$SOURCE_DIR/vision-token-refresh.service" /etc/systemd/system/vision-token-refresh.service
install -m 0644 "$SOURCE_DIR/vision-token-refresh.timer" /etc/systemd/system/vision-token-refresh.timer

systemctl daemon-reload
systemctl enable --now vision-token-refresh.timer
systemctl start vision-token-refresh.service
systemctl is-active --quiet vision-token-refresh.timer

printf 'Vision token refresh installed; timer is active\n'
