#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
COLOR=""
SITE_FILE="${CADDY_APP_SITE:-/etc/caddy/sites-enabled/app.adpulse.su.caddy}"
DESKTOP_SITE_FILE="${CADDY_DESKTOP_SITE:-/etc/caddy/sites-enabled/desktop.adpulse.su.caddy}"
CADDY_CONFIG="${CADDY_CONFIG:-/etc/caddy/Caddyfile}"
CADDY_ENV_FILE="${CADDY_ENV_FILE:-/etc/fb-agent/caddy.env}"
APP_ENV=""
DRY_RUN=false
NO_RELOAD=false
NEW_ROUTE_ACTIVE=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
  if [[ -n "${TEMP_DIR:-}" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT

while (($#)); do
  case "$1" in
    --color) COLOR="${2:?missing --color value}"; shift 2 ;;
    --site-file) SITE_FILE="${2:?missing --site-file value}"; shift 2 ;;
    --desktop-site-file) DESKTOP_SITE_FILE="${2:?missing --desktop-site-file value}"; shift 2 ;;
    --caddy-config) CADDY_CONFIG="${2:?missing --caddy-config value}"; shift 2 ;;
    --state-dir) : "${2:?missing --state-dir value}"; shift 2 ;;
    --app-env) APP_ENV="${2:?missing --app-env value}"; shift 2 ;;
    --no-reload) NO_RELOAD=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

case "$COLOR" in
  blue) API_PORT=18100; WEB_PORT=18080; TMA_PORT=18081 ;;
  green) API_PORT=28100; WEB_PORT=28080; TMA_PORT=28081 ;;
  *) die "--color must be blue or green" ;;
esac
for command in cp diff mktemp python3; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -f "$SITE_FILE" ]] || die "Caddy site is missing: $SITE_FILE"
[[ -f "$DESKTOP_SITE_FILE" ]] || die "desktop Caddy site is missing: $DESKTOP_SITE_FILE"

TEMP_DIR="$(mktemp -d)"
readonly APP_CANDIDATE="$TEMP_DIR/app.caddy"
readonly APP_PREVIOUS="$TEMP_DIR/app.previous.caddy"
readonly DESKTOP_CANDIDATE="$TEMP_DIR/desktop.caddy"
readonly DESKTOP_PREVIOUS="$TEMP_DIR/desktop.previous.caddy"
cp -- "$SITE_FILE" "$APP_PREVIOUS"
cp -- "$DESKTOP_SITE_FILE" "$DESKTOP_PREVIOUS"
python3 - \
  "$SITE_FILE" "$APP_CANDIDATE" \
  "$DESKTOP_SITE_FILE" "$DESKTOP_CANDIDATE" \
  "$API_PORT" "$WEB_PORT" "$TMA_PORT" <<'PY'
import pathlib
import re
import sys

app_source, app_target, desktop_source, desktop_target = map(pathlib.Path, sys.argv[1:5])
api_port, web_port, tma_port = sys.argv[5:]
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

def render(source: pathlib.Path, target: pathlib.Path, replacements: tuple[tuple[str, str], ...], label: str) -> None:
    text = source.read_text(encoding="utf-8")
    counts = []
    for pattern, replacement in replacements:
        text, count = re.subn(pattern, replacement, text)
        counts.append(count)
    if any(count == 0 for count in counts):
        raise SystemExit(f"{label} upstream replacement incomplete: counts={counts}")
    target.write_text(text, encoding="utf-8")

render(app_source, app_target, app_replacements, "app")
render(desktop_source, desktop_target, (api_replacement,), "desktop")
PY

if [[ "$DRY_RUN" == true ]]; then
  for pair in "$SITE_FILE:$APP_CANDIDATE" "$DESKTOP_SITE_FILE:$DESKTOP_CANDIDATE"; do
    source="${pair%%:*}"
    candidate="${pair#*:}"
    if diff -u "$source" "$candidate"; then
      :
    else
      diff_status=$?
      [[ "$diff_status" == 1 ]] || exit "$diff_status"
    fi
  done
  printf 'Caddy switch dry-run: %s\n' "$COLOR"
  exit 0
fi

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
for command in caddy install stat systemctl; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -f "$CADDY_CONFIG" ]] || die "Caddy config is missing: $CADDY_CONFIG"
[[ -f "$CADDY_ENV_FILE" ]] || die "Caddy env is missing: $CADDY_ENV_FILE"
[[ -f "$APP_ENV" ]] || die "--app-env is required for an atomic credential/route switch"
[[ "$(stat -Lc '%a' "$APP_ENV")" == "600" ]] || die "$APP_ENV must have mode 600"
install -m 0600 "$CADDY_ENV_FILE" "$TEMP_DIR/caddy.previous.env"

restore_site() {
  local failed=0
  install -m 0644 "$APP_PREVIOUS" "$SITE_FILE" || failed=1
  install -m 0644 "$DESKTOP_PREVIOUS" "$DESKTOP_SITE_FILE" || failed=1
  install -m 0600 "$TEMP_DIR/caddy.previous.env" "$CADDY_ENV_FILE" || failed=1
  return "$failed"
}

rollback_after_reload() {
  local -r exit_code=$?
  trap - ERR
  local rollback_failed=0
  restore_site || rollback_failed=1
  if [[ "$NEW_ROUTE_ACTIVE" == true ]]; then
    caddy validate --config "$CADDY_CONFIG" --adapter caddyfile \
      --envfile "$CADDY_ENV_FILE" >/dev/null 2>&1 || rollback_failed=1
    systemctl reload caddy >/dev/null 2>&1 || rollback_failed=1
  fi
  ((rollback_failed == 0)) || exit 70
  exit "$exit_code"
}
trap rollback_after_reload ERR

python3 "$SCRIPT_DIR/sync-caddy-env.py" \
  --source "$APP_ENV" \
  --target "$CADDY_ENV_FILE" \
  --scope api
install -m 0644 "$APP_CANDIDATE" "$SITE_FILE"
install -m 0644 "$DESKTOP_CANDIDATE" "$DESKTOP_SITE_FILE"
if ! caddy validate --config "$CADDY_CONFIG" --adapter caddyfile \
  --envfile "$CADDY_ENV_FILE"; then
  restore_site || die "candidate Caddy validation failed and disk rollback also failed"
  die "candidate Caddy configuration is invalid; previous site restored"
fi
if [[ "$NO_RELOAD" != true ]]; then
  if ! systemctl reload caddy; then
    restore_site || die "Caddy reload failed and disk rollback also failed"
    caddy validate --config "$CADDY_CONFIG" --adapter caddyfile \
      --envfile "$CADDY_ENV_FILE" >/dev/null 2>&1 \
      || die "restored Caddy configuration is invalid"
    systemctl reload caddy >/dev/null 2>&1 \
      || die "Caddy reload failed and the restored route could not be reloaded"
    die "Caddy reload failed; previous site restored"
  fi
  NEW_ROUTE_ACTIVE=true
fi
trap - ERR
printf 'Caddy route prepared for %s (active-state is unchanged)\n' "$COLOR"
