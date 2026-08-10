#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly SHARED_ENV_FILE="${APP_ENV_OVERRIDE:-$ROOT_DIR/shared/active-app.env}"
readonly CADDY_ENV_FILE="/etc/fb-agent/caddy.env"
readonly CADDY_FILE="/etc/caddy/Caddyfile"
readonly APP_CADDY_SITE="/etc/caddy/sites-enabled/app.adpulse.su.caddy"
readonly DESKTOP_CADDY_SITE="/etc/caddy/sites-enabled/desktop.adpulse.su.caddy"
readonly CADDY_LOG_DIR="/var/log/caddy"
readonly APP_ACCESS_LOG="$CADDY_LOG_DIR/fb-agent-access.log"
readonly DESKTOP_ACCESS_LOG="$CADDY_LOG_DIR/fb-agent-desktop-access.log"
TEMP_DIR=""
CADDY_ONLY=false
SYNC_SCOPE=all

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
  if [[ -n "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --caddy-only) CADDY_ONLY=true; shift ;;
    --sync-scope) SYNC_SCOPE="${2:?missing --sync-scope value}"; shift 2 ;;
    *) die "unknown argument: $1" ;;
  esac
done
case "$SYNC_SCOPE" in
  all|api|desktop|none) ;;
  *) die "--sync-scope must be all, api, desktop or none" ;;
esac

[[ "${EUID:-$(id -u)}" -eq 0 ]] || { printf 'ERROR: run as root\n' >&2; exit 1; }
for command in caddy chmod chown install python3 systemctl touch; do
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

# A credential-sync release validates the candidate env before touching Caddy.
# Route-only preflight deliberately keeps the incumbent credential snapshot;
# the candidate was already validated when production.env was rendered.
if [[ "$SYNC_SCOPE" != none ]]; then
  python3 "$PROJECT_DIR/scripts/prepare_production_env.py" \
    --input "$SHARED_ENV_FILE" \
    --validate-only
fi

TEMP_DIR="$(mktemp -d)"
install -m 0600 "$CADDY_ENV_FILE" "$TEMP_DIR/caddy.env"

# API and desktop credentials have independent release boundaries. The helper
# parses dotenv as data (never shell source/eval) and atomically updates only
# the explicitly selected scope, preserving operator-managed BasicAuth values.
if [[ "$SYNC_SCOPE" != none ]]; then
  python3 "$PROJECT_DIR/scripts/sync-caddy-env.py" \
    --source "$SHARED_ENV_FILE" \
    --target "$CADDY_ENV_FILE" \
    --scope "$SYNC_SCOPE"
fi
install -d -m 0755 /etc/caddy/sites-enabled /etc/systemd/system/caddy.service.d
install -d -o caddy -g caddy -m 0755 "$CADDY_LOG_DIR"
for access_log in "$APP_ACCESS_LOG" "$DESKTOP_ACCESS_LOG"; do
  [[ ! -L "$access_log" ]] || die "refusing symlinked Caddy access log: $access_log"
  touch -- "$access_log"
  [[ -f "$access_log" ]] || die "Caddy access log is not a regular file: $access_log"
  chown -- caddy:caddy "$access_log"
  chmod -- 0600 "$access_log"
done

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
  install -m 0600 "$TEMP_DIR/caddy.env" "$CADDY_ENV_FILE"
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

cp -- "$PROJECT_DIR/deploy/caddy/app.adpulse.su.caddy" "$TEMP_DIR/app-site.new.caddy"
cp -- "$PROJECT_DIR/deploy/caddy/desktop.adpulse.su.caddy" "$TEMP_DIR/desktop-site.new.caddy"
if [[ -f "$ROOT_DIR/shared/active-color" ]]; then
  active_color="$(<"$ROOT_DIR/shared/active-color")"
  case "$active_color" in
    blue) active_api=18100; active_web=18080; active_tma=18081 ;;
    green) active_api=28100; active_web=28080; active_tma=28081 ;;
    *) die "invalid active-color state: $active_color" ;;
  esac
  python3 - \
    "$TEMP_DIR/app-site.new.caddy" \
    "$TEMP_DIR/desktop-site.new.caddy" \
    "$active_api" "$active_web" "$active_tma" <<'PY'
import pathlib
import re
import sys

app_path = pathlib.Path(sys.argv[1])
desktop_path = pathlib.Path(sys.argv[2])
api_port, web_port, tma_port = sys.argv[3:]
api_replacement = (
    r"((?:reverse_proxy|forward_auth)[ \t]+)127\.0\.0\.1:(?:18100|28100)\b",
    rf"\g<1>127.0.0.1:{api_port}",
)
app_replacements = (
    api_replacement,
    (
        r"(reverse_proxy[ \t]+)127\.0\.0\.1:(?:18080|28080)\b",
        rf"\g<1>127.0.0.1:{web_port}",
    ),
    (
        r"(reverse_proxy[ \t]+)127\.0\.0\.1:(?:18081|28081)\b",
        rf"\g<1>127.0.0.1:{tma_port}",
    ),
)

def render(path: pathlib.Path, replacements: tuple[tuple[str, str], ...], label: str) -> None:
    text = path.read_text(encoding="utf-8")
    for pattern, replacement in replacements:
        text, count = re.subn(pattern, replacement, text)
        if count == 0:
            raise SystemExit(f"{label} active upstream replacement failed: {pattern}")
    path.write_text(text, encoding="utf-8")

render(app_path, app_replacements, "app")
render(desktop_path, (api_replacement,), "desktop")
PY
fi
install -m 0644 "$TEMP_DIR/app-site.new.caddy" "$APP_CADDY_SITE"
install -m 0644 "$TEMP_DIR/desktop-site.new.caddy" "$DESKTOP_CADDY_SITE"
install -m 0644 "$PROJECT_DIR/deploy/systemd/caddy-fb-agent-env.conf" \
  /etc/systemd/system/caddy.service.d/fb-agent-env.conf

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

if [[ "$CADDY_ONLY" == true ]]; then
  printf 'Candidate Caddy sites installed without changing application units\n'
  exit 0
fi

"$SCRIPT_DIR/install-host-metrics.sh"
for unit in \
  fb-agent.service \
  fb-agent-desktop-agent.service \
  fb-agent-desktop-heal.service fb-agent-desktop-heal.timer \
  fb-agent-alloy-agent.service \
  fb-agent-healthcheck.service fb-agent-healthcheck.timer \
  fb-agent-pgbackrest-full.service fb-agent-pgbackrest-full.timer \
  fb-agent-pgbackrest-diff.service fb-agent-pgbackrest-diff.timer \
  fb-agent-restore-drill.service fb-agent-restore-drill.timer; do
  install -m 0644 "$PROJECT_DIR/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload

systemctl enable \
  fb-agent.service fb-agent-desktop-agent.service \
  fb-agent-desktop-heal.timer fb-agent-healthcheck.timer
systemctl start \
  fb-agent.service fb-agent-healthcheck.timer
# The desktop units are enabled here so a committed desktop survives reboot,
# but they must not start before platform-desktop-release has durably selected
# active-desktop-state.  The desktop release starts both units idempotently only
# after its journal, Caddy/readiness pointers and exact runtime identity commit.
# server-platform-release.sh invokes install-platform-units.sh immediately
# after the initial full+PITR evidence is accepted. Subsequent releases verify
# that every recurring timer remains enabled and active.
printf 'Systemd units and Caddy site installed successfully\n'
