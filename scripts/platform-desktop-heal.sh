#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
DESKTOP_RELEASE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly DESKTOP_RELEASE_DIR
# shellcheck source=scripts/browser-maintenance-lease.sh
source "$SCRIPT_DIR/browser-maintenance-lease.sh"
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly STATE_DIR="$ROOT_DIR/shared"
[[ "${FB_AGENT_VERIFIED_RELEASE_SCHEMA:-}" == \
  "fb-agent-verified-release-exec/v1" \
  && -n "${FB_AGENT_ACTIVE_STATE_DIR:-}" \
  && -n "${FB_AGENT_APP_STATE_DIR:-}" ]] \
  || {
    printf 'ERROR: desktop healer requires verified desktop and app states\n' >&2
    exit 1
  }
readonly DESKTOP_STATE_DIR="$FB_AGENT_ACTIVE_STATE_DIR"
readonly APP_STATE_DIR="$FB_AGENT_APP_STATE_DIR"
readonly APP_ENV="$APP_STATE_DIR/app.env"
readonly APP_COLOR_FILE="$APP_STATE_DIR/color"
HOST_METRIC_STARTED=false
desktop_transaction_outcome=""
api_port=""
api_key=""

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '[desktop-heal] %s\n' "$*" >&2; }
record_host_metric() {
  local -r outcome="$1"
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || return 0
  if ! python3 "$SCRIPT_DIR/host_metrics.py" record \
    --operation desktop_healer --outcome "$outcome"; then
    log "CRITICAL failed to persist desktop_healer host metric ($outcome)"
  fi
}
# shellcheck disable=SC2317,SC2329 # EXIT trap callback is intentionally indirect.
finish() {
  local exit_code=$?
  trap - EXIT
  if ! browser_maintenance_leave; then
    log "CRITICAL failed to release browser maintenance lease"
    if ((exit_code == 0)); then
      exit_code=1
    fi
  fi
  if [[ "$HOST_METRIC_STARTED" == true ]]; then
    if ((exit_code == 0)); then
      record_host_metric success
    else
      record_host_metric failure
    fi
  fi
  exit "$exit_code"
}
trap finish EXIT

platform_desktop_compose() {
  FB_AGENT_ROOT="$ROOT_DIR" FB_AGENT_DESKTOP_LOCK_HELD=1 \
    "$DESKTOP_RELEASE_DIR/scripts/platform-desktop-compose.sh" "$@"
}

vision_tuple_is_exact() {
  VISION_WAIT_TIMEOUT_SECONDS=0 VISION_WAIT_INTERVAL_SECONDS=0 \
    "$DESKTOP_RELEASE_DIR/scripts/wait-for-vision-container.sh" \
    >/dev/null 2>&1
}

browser_identity_is_exact() {
  platform_desktop_compose verify >/dev/null 2>&1
}

vision_ready() {
  local response=""
  local -a owner_header=()
  if [[ "$BROWSER_MAINTENANCE_OWNER" =~ ^[0-9a-f]{32}$ ]]; then
    owner_header=(
      --header
      "X-FB-Agent-Browser-Maintenance-Owner: $BROWSER_MAINTENANCE_OWNER"
    )
  fi
  response="$(curl --silent --show-error --fail --max-time 25 \
    --header "X-API-Key: $api_key" \
    "${owner_header[@]}" \
    "http://127.0.0.1:${api_port}/api/settings/vision")" || return 1
  printf '%s' "$response" \
    | python3 "$DESKTOP_RELEASE_DIR/scripts/desktop-vision-contract.py" ready
}

committed_desktop_auth_ready() {
  local response=""
  response="$(curl --silent --show-error --fail --max-time 10 \
    "http://127.0.0.1:${api_port}/desktop-readyz")" || return 1
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

desktop_runtime_is_exact_and_ready() {
  vision_tuple_is_exact \
    && browser_identity_is_exact \
    && vision_ready \
    && committed_desktop_auth_ready
}

complete_desktop_transaction() {
  case "$desktop_transaction_outcome" in
    none) return 0 ;;
    candidate|previous|absent)
      "$DESKTOP_RELEASE_DIR/scripts/platform-desktop-transaction.sh" \
        complete --expect "$desktop_transaction_outcome"
      ;;
    *) die "desktop transaction returned an invalid outcome" ;;
  esac
}

ensure_cdp() {
  local response=""
  browser_maintenance_assert_held || return 1
  response="$(curl --silent --show-error --fail --max-time 120 \
    --request POST \
    --header "X-API-Key: $api_key" \
    --header "X-FB-Agent-Browser-Maintenance-Owner: $BROWSER_MAINTENANCE_OWNER" \
    "http://127.0.0.1:${api_port}/api/vision/ensure-cdp")" || return 1
  python3 -c '
import json, sys
raise SystemExit(0 if json.loads(sys.argv[1]).get("ok") is True else 1)
' "$response" || return 1
  browser_maintenance_assert_held
}

for command in \
  awk curl docker flock install mktemp python3 readlink rm sed seq sleep timeout; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -d "$DESKTOP_STATE_DIR" && ! -L "$DESKTOP_STATE_DIR" \
  && "$(readlink -f "$DESKTOP_STATE_DIR")" == "$DESKTOP_STATE_DIR" \
  && "$(dirname -- "$DESKTOP_STATE_DIR")" == "$STATE_DIR/desktop-states" ]] \
  || die "verified desktop state directory is unsafe"
[[ -d "$APP_STATE_DIR" && ! -L "$APP_STATE_DIR" \
  && "$(readlink -f "$APP_STATE_DIR")" == "$APP_STATE_DIR" \
  && "$(dirname -- "$APP_STATE_DIR")" == "$STATE_DIR/active-states" ]] \
  || die "verified application state directory is unsafe"
[[ -f "$APP_ENV" ]] || die "active application environment is missing"
[[ -f "$APP_COLOR_FILE" ]] || die "active application color is missing"
[[ "$(readlink -f "$DESKTOP_STATE_DIR/release")" == \
  "$DESKTOP_RELEASE_DIR" ]] \
  || die "desktop healer wrapper does not match committed desktop state"

color="$(<"$APP_COLOR_FILE")"
case "$color" in
  blue) api_port=18100 ;;
  green) api_port=28100 ;;
  *) die "active application color is invalid: $color" ;;
esac
api_key="$(sed -n 's/^API_KEY=//p' "$APP_ENV" | tail -n 1)"
[[ -n "$api_key" && "$api_key" != *$'\n'* ]] || die "active API key is missing"

[[ -f "$STATE_DIR/desktop-release.lock" \
  && ! -L "$STATE_DIR/desktop-release.lock" ]] \
  || die "desktop release lock is missing or unsafe"
exec 8<"$STATE_DIR/desktop-release.lock"
if ! flock -n 8; then
  # The timer can legitimately fire while an immutable desktop release owns
  # this lock. Treat that overlap as a no-op; the release owns reconciliation.
  log "desktop release owns the runtime; healer deferred to the next timer tick"
  exit 0
fi

# Healthy timer ticks are strictly read-only: no PostgreSQL lease, image pull,
# Compose mutation, transaction write or host-metric write is performed.
desktop_transaction_outcome="$(
  "$DESKTOP_RELEASE_DIR/scripts/platform-desktop-transaction.sh" status
)"
if [[ "$desktop_transaction_outcome" == none ]] \
  && desktop_runtime_is_exact_and_ready; then
  log "desktop/browser/CDP control plane is exact and ready"
  exit 0
fi

HOST_METRIC_STARTED=true
record_host_metric started
browser_maintenance_enter \
  || die "desktop healer could not acquire the durable browser maintenance fence"

# A release or another recovery attempt may have converged between the first
# observation and acquisition of the durable fence. Recheck under ownership
# before crossing any mutation boundary.
desktop_transaction_outcome="$(
  "$DESKTOP_RELEASE_DIR/scripts/platform-desktop-transaction.sh" status
)"
if [[ "$desktop_transaction_outcome" == none ]] \
  && desktop_runtime_is_exact_and_ready; then
  log "desktop runtime converged while the maintenance fence was acquired"
  exit 0
fi

desktop_transaction_outcome="$(
  "$DESKTOP_RELEASE_DIR/scripts/platform-desktop-transaction.sh" reconcile
)"
case "$desktop_transaction_outcome" in
  none|candidate|previous|absent) ;;
  *) die "desktop transaction returned an invalid outcome" ;;
esac

# A pending pointer/Caddy transaction can be the only degraded component. Do
# not touch Vision or the browser when reconciliation alone restored health.
if desktop_runtime_is_exact_and_ready; then
  browser_maintenance_checkpoint \
    || die "browser maintenance fence was lost before transaction completion"
  complete_desktop_transaction
  log "desktop transaction reconciled without runtime mutation"
  exit 0
fi

# browser-agent shares webtop's network namespace by container ID. It must be
# removed before any Vision reconciliation that can replace that container.
platform_desktop_compose remove
browser_maintenance_checkpoint \
  || die "browser maintenance fence was lost before Vision reconciliation"

vision_release_id="$(sed -n 's/^RELEASE_ID=//p' \
  "$DESKTOP_STATE_DIR/release-images.env" | tail -n 1)"
[[ "$vision_release_id" =~ ^[A-Za-z0-9._-]{1,128}$ ]] \
  || die "committed desktop release id is invalid"
FB_AGENT_VISION_RELEASE_ID="$vision_release_id" \
  VISION_COMPOSE_ENV_FILE="$DESKTOP_STATE_DIR/app.env" \
  VISION_ROLLBACK_ENV_FILE="$DESKTOP_STATE_DIR/app.env" \
  FB_AGENT_CONTROL_APP_ENV_FILE="$APP_ENV" \
  FB_AGENT_CONTROL_APP_COLOR_FILE="$APP_COLOR_FILE" \
  FB_AGENT_STATE_DIR="$STATE_DIR" \
  "$DESKTOP_RELEASE_DIR/scripts/install-vision-webtop.sh" \
  --reconcile-pending-update

# The browser was removed above, so this always creates it against the current
# webtop container ID; the wrapper then verifies HostConfig.NetworkMode.
platform_desktop_compose up
browser_identity_is_exact \
  || die "healed browser-agent identity or Vision namespace binding is not exact"
browser_maintenance_checkpoint \
  || die "browser maintenance fence was lost during desktop healing"

if desktop_runtime_is_exact_and_ready; then
  browser_maintenance_checkpoint \
    || die "browser maintenance fence was lost before transaction completion"
  complete_desktop_transaction
  log "desktop/browser/CDP control plane is ready"
  exit 0
fi

ensure_cdp || die "desktop-aware ensure-cdp failed"
for _ in $(seq 1 30); do
  if desktop_runtime_is_exact_and_ready; then
    browser_maintenance_checkpoint \
      || die "browser maintenance fence was lost before transaction completion"
    complete_desktop_transaction
    log "desktop-aware CDP recovery succeeded"
    exit 0
  fi
  sleep 2
done
die "CDP did not become ready after desktop-aware recovery"
