#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly SHARED_ENV_FILE="$ROOT_DIR/shared/.env"
readonly CADDY_ENV_FILE="/etc/fb-agent/caddy.env"
readonly CADDY_FILE="/etc/caddy/Caddyfile"
readonly APP_CADDY_SITE="/etc/caddy/sites-enabled/app.adpulse.su.caddy"
readonly DESKTOP_CADDY_SITE="/etc/caddy/sites-enabled/desktop.adpulse.su.caddy"
TEMP_DIR=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { [[ -n "$TEMP_DIR" ]] && rm -rf -- "$TEMP_DIR"; }
trap cleanup EXIT

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { printf 'ERROR: run as root\n' >&2; exit 1; }
for command in install systemctl caddy python3; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -s "$CADDY_ENV_FILE" ]] || {
  printf 'ERROR: %s must define PANEL_BASIC_AUTH_USER and PANEL_BASIC_AUTH_HASH\n' "$CADDY_ENV_FILE" >&2
  exit 1
}
[[ "$(stat -c '%a' "$CADDY_ENV_FILE")" == "600" ]] || {
  printf 'ERROR: %s must have mode 600\n' "$CADDY_ENV_FILE" >&2
  exit 1
}
[[ -s "$SHARED_ENV_FILE" ]] || die "$SHARED_ENV_FILE is missing or empty"
[[ "$(stat -c '%a' "$SHARED_ENV_FILE")" == "600" ]] || {
  die "$SHARED_ENV_FILE must have mode 600"
}
[[ -f "$CADDY_FILE" ]] || die "$CADDY_FILE is missing"

# Caddy receives only the server-side API key required for upstream injection.
# The helper parses dotenv as data (never shell source/eval) and replaces the
# root-only target atomically, preserving the operator-managed BasicAuth values.
python3 "$PROJECT_DIR/scripts/sync-caddy-env.py" \
  --source "$SHARED_ENV_FILE" \
  --target "$CADDY_ENV_FILE"

TEMP_DIR="$(mktemp -d)"
install -d -m 0755 /etc/caddy/sites-enabled /etc/systemd/system/caddy.service.d

# Caddy-конфиг меняем с проверкой и возможностью вернуть предыдущую рабочую
# версию. Это особенно важно для публичного postback-route: ошибка в site-файле
# не должна ломать весь HTTPS-контур при очередном release.
app_site_existed=false
if [[ -f "$APP_CADDY_SITE" ]]; then
  app_site_existed=true
  cp -- "$APP_CADDY_SITE" "$TEMP_DIR/app-site.caddy"
fi
desktop_site_existed=false
if [[ -f "$DESKTOP_CADDY_SITE" ]]; then
  desktop_site_existed=true
  cp -- "$DESKTOP_CADDY_SITE" "$TEMP_DIR/desktop-site.caddy"
fi
cp -- "$CADDY_FILE" "$TEMP_DIR/Caddyfile"

restore_caddy_config() {
  cp -- "$TEMP_DIR/Caddyfile" "$CADDY_FILE"
  if [[ "$app_site_existed" == true ]]; then
    cp -- "$TEMP_DIR/app-site.caddy" "$APP_CADDY_SITE"
  else
    rm -f -- "$APP_CADDY_SITE"
  fi
  if [[ "$desktop_site_existed" == true ]]; then
    cp -- "$TEMP_DIR/desktop-site.caddy" "$DESKTOP_CADDY_SITE"
  else
    rm -f -- "$DESKTOP_CADDY_SITE"
  fi
}

install -m 0644 "$PROJECT_DIR/deploy/caddy/app.adpulse.su.caddy" "$APP_CADDY_SITE"
install -m 0644 "$PROJECT_DIR/deploy/caddy/desktop.adpulse.su.caddy" "$DESKTOP_CADDY_SITE"
install -m 0644 "$PROJECT_DIR"/deploy/systemd/fb-agent*.service /etc/systemd/system/
install -m 0644 "$PROJECT_DIR"/deploy/systemd/fb-agent*.timer /etc/systemd/system/
install -m 0644 "$PROJECT_DIR/deploy/systemd/caddy-fb-agent-env.conf" \
  /etc/systemd/system/caddy.service.d/fb-agent-env.conf

# Older installations defined desktop.adpulse.su directly in the root Caddyfile.
# Remove that exact block before importing the managed site to avoid a duplicate host.
if ! python3 "$PROJECT_DIR/scripts/remove-caddy-site-block.py" \
  "$CADDY_FILE" \
  "desktop.adpulse.su"; then
  restore_caddy_config
  die "Legacy desktop Caddy migration failed; previous configuration restored"
fi

if ! grep -Eq '^[[:space:]]*import[[:space:]]+/etc/caddy/sites-enabled/\*' "$CADDY_FILE"; then
  printf '\nimport /etc/caddy/sites-enabled/*\n' >>"$CADDY_FILE"
fi

if ! caddy validate --config "$CADDY_FILE" --adapter caddyfile \
  --envfile "$CADDY_ENV_FILE"; then
  restore_caddy_config
  die "Caddy validation failed; previous configuration restored"
fi

systemctl daemon-reload
if ! systemctl reload caddy; then
  restore_caddy_config
  caddy validate --config "$CADDY_FILE" --adapter caddyfile \
    --envfile "$CADDY_ENV_FILE" >/dev/null 2>&1 || true
  systemctl reload caddy >/dev/null 2>&1 || true
  die "Caddy reload failed; previous configuration restored"
fi
systemctl enable fb-agent.service fb-agent-backup.timer fb-agent-healthcheck.timer
systemctl start fb-agent.service fb-agent-backup.timer fb-agent-healthcheck.timer
printf 'Systemd units and Caddy site installed successfully\n'
