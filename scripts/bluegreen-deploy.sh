#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
# shellcheck source=scripts/browser-control-env.sh
source "$SCRIPT_DIR/browser-control-env.sh"
readonly APP_COMPOSE="$PROJECT_DIR/deploy/compose/docker-compose.app.yml"
COLOR=""
RELEASE_ENV=""
APP_ENV=""
BACKUP_ENV=""
STATE_DIR="${FB_AGENT_STATE_DIR:-/opt/fb-agent/shared}"
CANDIDATE_STATE=""
PUBLIC_URL="${PUBLIC_URL:-https://app.adpulse.su}"
readonly CANONICAL_PUBLIC_URL="https://app.adpulse.su"
ACTIVATE=false
DRY_RUN=false
PREVIOUS_COLOR=""
PREVIOUS_RELEASE_ENV=""
readonly CUTOVER_BUDGET_SECONDS=180
CUTOVER_DEADLINE_EPOCH=""

die() { printf 'ERROR: %s\n' "$*" >&2; return 1; }
log() { printf '[bluegreen-deploy] %s\n' "$*" >&2; }
cleanup() {
  if [[ -n "${TEMP_DIR:-}" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT

dotenv_value() {
  local -r key="$1"
  sed -n "s/^${key}=//p" "$RELEASE_ENV" | tail -n 1
}

ports_for_color() {
  case "$COLOR" in
    blue) APP_API_PORT=18100; APP_WEB_PORT=18080; APP_TMA_PORT=18081 ;;
    green) APP_API_PORT=28100; APP_WEB_PORT=28080; APP_TMA_PORT=28081 ;;
    *) die "--color must be blue or green" ;;
  esac
  export APP_API_PORT APP_WEB_PORT APP_TMA_PORT
}

while (($#)); do
  case "$1" in
    --color) COLOR="${2:?missing value}"; shift 2 ;;
    --release-env) RELEASE_ENV="${2:?missing value}"; shift 2 ;;
    --app-env) APP_ENV="${2:?missing value}"; shift 2 ;;
    --backup-env) BACKUP_ENV="${2:?missing value}"; shift 2 ;;
    --state-dir) STATE_DIR="${2:?missing value}"; shift 2 ;;
    --candidate-state) CANDIDATE_STATE="${2:?missing value}"; shift 2 ;;
    --public-url) PUBLIC_URL="${2:?missing value}"; shift 2 ;;
    --activate) ACTIVATE=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

ports_for_color
[[ "${PUBLIC_URL%/}" == "$CANONICAL_PUBLIC_URL" ]] \
  || die "only the canonical public URL $CANONICAL_PUBLIC_URL is supported"
PUBLIC_URL="$CANONICAL_PUBLIC_URL"
for file in "$RELEASE_ENV" "$APP_ENV" "$BACKUP_ENV"; do
  [[ -f "$file" ]] || die "required file is missing: $file"
done
[[ -d "$CANDIDATE_STATE" ]] || die "--candidate-state is required"
STATE_PARENT="$(cd -- "$STATE_DIR/.." && pwd -P)"
[[ "$STATE_DIR" == "$STATE_PARENT/shared" ]] \
  || die "state directory must be the canonical shared child of FB_AGENT_ROOT"
BROWSER_CONTROL_ENV_FILE="$STATE_DIR/browser-control.env"
BROWSER_MAINTENANCE_ENV_FILE="$STATE_DIR/browser-maintenance.env"
BROWSER_AUTOPAUSE_ENV_FILE="$STATE_DIR/browser-autopause.env"
BROWSER_META_API_ENV_FILE="$STATE_DIR/browser-meta-api.env"
BROWSER_CAMPAIGN_CREATOR_ENV_FILE="$STATE_DIR/browser-campaign-creator.env"
BROWSER_AUTHORITY_ENV_FILE="$STATE_DIR/browser-authority.env"
browser_control_env_require "$BROWSER_CONTROL_ENV_FILE" \
  || die "browser control environment failed the private-file contract"
browser_maintenance_env_require "$BROWSER_MAINTENANCE_ENV_FILE" \
  || die "browser maintenance environment failed the private-file contract"
for operation_env in \
  "$BROWSER_AUTOPAUSE_ENV_FILE" \
  "$BROWSER_META_API_ENV_FILE" \
  "$BROWSER_CAMPAIGN_CREATOR_ENV_FILE"; do
  browser_operation_env_require "$operation_env" \
    || die "browser operation environment failed the private-file contract"
done
browser_authority_env_require "$BROWSER_AUTHORITY_ENV_FILE" \
  || die "browser authority environment failed the private-file contract"
export BROWSER_CONTROL_ENV_FILE BROWSER_MAINTENANCE_ENV_FILE
export BROWSER_AUTOPAUSE_ENV_FILE BROWSER_META_API_ENV_FILE
export BROWSER_CAMPAIGN_CREATOR_ENV_FILE BROWSER_AUTHORITY_ENV_FILE
LOCK_FILE="$STATE_DIR/deploy.lock"
if [[ -n "${FB_AGENT_DEPLOY_LOCK_FD:-}" ]]; then
  [[ "$FB_AGENT_DEPLOY_LOCK_FD" =~ ^[0-9]+$ \
    && -e "/proc/$$/fd/$FB_AGENT_DEPLOY_LOCK_FD" ]] \
    || die "inherited deployment lock fd is invalid"
  [[ "$(readlink -f "/proc/$$/fd/$FB_AGENT_DEPLOY_LOCK_FD")" == "$LOCK_FILE" ]] \
    || die "inherited deployment lock does not guard $LOCK_FILE"
  flock -n "$FB_AGENT_DEPLOY_LOCK_FD" || die "inherited deployment lock is not held"
else
  exec 9>"$LOCK_FILE"
  flock -n 9 || die "another deployment or reconciliation is already running"
  export FB_AGENT_DEPLOY_LOCK_FD=9
fi
export APP_COLOR="$COLOR"
export APP_ENV_FILE="$APP_ENV"
export BACKUP_ENV_FILE="$BACKUP_ENV"
export ADOPTION_BUNDLE_FILE="$STATE_DIR/adoption-bundle-v1.json"
export DESKTOP_READINESS_DIR="${DESKTOP_READINESS_DIR:-$STATE_DIR/desktop-readiness}"
export PGBACKREST_CONFIG_FILE="$STATE_DIR/pgbackrest.conf"
[[ -f "$PGBACKREST_CONFIG_FILE" && ! -L "$PGBACKREST_CONFIG_FILE" ]] \
  || die "stable pgBackRest config is missing: $PGBACKREST_CONFIG_FILE"
export RELEASE_ID
RELEASE_ID="$(dotenv_value RELEASE_ID)"
[[ "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]] || die "invalid RELEASE_ID"
FB_AGENT_BOOTSTRAP_CLUSTER_ID="$(
  sed -n 's/^FB_AGENT_BOOTSTRAP_CLUSTER_ID=//p' "$APP_ENV" | tail -n 1
)"
[[ "$FB_AGENT_BOOTSTRAP_CLUSTER_ID" =~ ^[0-9a-f]{32}$ ]] \
  || die "application environment has an invalid bootstrap cluster id"
export FB_AGENT_BOOTSTRAP_CLUSTER_ID
for key in API_IMAGE WORKERS_IMAGE FRONTEND_IMAGE MINI_APP_IMAGE; do
  value="$(dotenv_value "$key")"
  [[ "$value" =~ ^[^[:space:]@]+@sha256:[0-9a-f]{64}$ ]] \
    || die "$key is not digest-pinned"
done

compose=(docker compose -p "fb_agent_${COLOR}" --env-file "$RELEASE_ENV" -f "$APP_COMPOSE")
"${compose[@]}" config --quiet
while IFS= read -r image; do
  [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || die "non-immutable image: $image"
done < <("${compose[@]}" --profile adoption --profile migration --profile release \
  --profile workers config --images)

if [[ "$DRY_RUN" == true ]]; then
  log "configuration validated; candidate $COLOR was not changed"
  exit 0
fi

TEMP_DIR="$(mktemp -d)"
cutover_remaining_seconds() {
  local now=""
  now="$(date +%s)"
  local -r remaining=$((CUTOVER_DEADLINE_EPOCH - now))
  ((remaining > 0)) || return 1
  printf '%s\n' "$remaining"
}

run_cutover_bounded() {
  local remaining=""
  remaining="$(cutover_remaining_seconds)" || return 124
  timeout --signal=KILL "${remaining}s" "$@"
}

rollback() {
  local -r exit_code=$?
  trap - ERR
  log "release failed before/around commit; reconciling to the atomic active-state pointer"
  if [[ -z "$CUTOVER_DEADLINE_EPOCH" ]]; then
    CUTOVER_DEADLINE_EPOCH=$(( $(date +%s) + CUTOVER_BUDGET_SECONDS ))
  fi
  local remaining=""
  if ! remaining="$(cutover_remaining_seconds)"; then
    remaining=0
  fi
  if ((remaining > 0)) && timeout --signal=KILL "${remaining}s" \
    env FB_AGENT_ROOT="$STATE_PARENT" "$SCRIPT_DIR/reconcile-platform-release.sh" \
      --deadline-epoch "$CUTOVER_DEADLINE_EPOCH"; then
    exit "$exit_code"
  else
    rollback_status=$?
  fi
  python3 "$SCRIPT_DIR/release-state.py" mark-rollback-failed \
    --state-root "$STATE_DIR" --failure "normal_rollback:exit_${rollback_status}"
  logger --id=$$ --priority=daemon.crit --tag=fb-agent-release \
    "CRITICAL release rollback exceeded the single ${CUTOVER_BUDGET_SECONDS}s cutover budget"
  exit 70
}

python3 "$SCRIPT_DIR/release-state.py" begin \
  --state-root "$STATE_DIR" --candidate-state "$CANDIDATE_STATE" >/dev/null
trap rollback ERR
candidate_state_color="$(python3 "$SCRIPT_DIR/release-state.py" get \
  --state-root "$STATE_DIR" --source candidate --field color)"
candidate_state_release_env="$(python3 "$SCRIPT_DIR/release-state.py" get \
  --state-root "$STATE_DIR" --source candidate --field release_env)"
candidate_state_app_env="$(python3 "$SCRIPT_DIR/release-state.py" get \
  --state-root "$STATE_DIR" --source candidate --field app_env)"
candidate_state_release_dir="$(python3 "$SCRIPT_DIR/release-state.py" get \
  --state-root "$STATE_DIR" --source candidate --field release_dir)"
[[ "$candidate_state_color" == "$COLOR" ]]
cmp -s -- "$candidate_state_release_env" "$RELEASE_ENV"
cmp -s -- "$candidate_state_app_env" "$APP_ENV"
[[ "$candidate_state_release_dir" == "$PROJECT_DIR" ]]

if [[ -e "$STATE_DIR/active-state" || -L "$STATE_DIR/active-state" ]]; then
  PREVIOUS_COLOR="$(python3 "$SCRIPT_DIR/release-state.py" get \
    --state-root "$STATE_DIR" --source active --field color)"
  PREVIOUS_RELEASE_ENV="$(python3 "$SCRIPT_DIR/release-state.py" get \
    --state-root "$STATE_DIR" --source active --field release_env)"
  [[ "$PREVIOUS_COLOR" == blue || "$PREVIOUS_COLOR" == green ]] \
    || die "active release must be blue or green"
  [[ "$PREVIOUS_COLOR" != "$COLOR" ]] || die "candidate color is already active"
else
  [[ "$COLOR" == blue ]] || die "the first clean installation must start on blue"
  [[ -f "$ADOPTION_BUNDLE_FILE" && ! -L "$ADOPTION_BUNDLE_FILE" ]] \
    || die "first release requires the reviewed adoption bundle"
  [[ "$(stat -Lc '%a' "$ADOPTION_BUNDLE_FILE")" == "600" ]] \
    || die "adoption bundle must have mode 600"
fi

"${compose[@]}" --profile adoption --profile migration --profile release \
  --profile workers pull
backup_gate_args=(
  --release-env "$RELEASE_ENV"
  --app-env "$APP_ENV"
  --backup-env "$BACKUP_ENV"
  --config-file "$PGBACKREST_CONFIG_FILE"
)
if [[ -z "$PREVIOUS_COLOR" ]]; then
  # There is no historical application data to protect on an explicitly
  # empty first-install target. Install the reviewed baseline first, then
  # prove a full local backup, post-backup WAL replay and isolated restore
  # before the candidate can start or receive public traffic.
  "${compose[@]}" --profile migration run --rm migrator
  "${compose[@]}" --profile adoption run --rm adoption_importer
  "$SCRIPT_DIR/release-backup-gate.sh" \
    "${backup_gate_args[@]}" \
    --evidence-root "$STATE_DIR/backup-evidence" \
    --accepted-dir "$STATE_DIR/backup-evidence/adoption-$RELEASE_ID"
else
  # Existing releases must prove a restorable pre-change backup before any
  # forward-only schema mutation.
  "$SCRIPT_DIR/release-backup-gate.sh" \
    "${backup_gate_args[@]}" \
    --evidence-root "$STATE_DIR/backup-evidence/pre-migration"
  "${compose[@]}" --profile migration run --rm migrator
fi
"${compose[@]}" up -d --no-deps --wait --wait-timeout 240 api frontend mini-app
python3 "$SCRIPT_DIR/release-state.py" stage \
  --state-root "$STATE_DIR" --stage candidate_started

curl --silent --show-error --fail --retry 12 --retry-delay 5 --retry-all-errors \
  --max-time 10 "http://127.0.0.1:${APP_API_PORT}/healthz" >/dev/null
curl --silent --show-error --fail --retry 12 --retry-delay 5 --retry-all-errors \
  --max-time 10 "http://127.0.0.1:${APP_API_PORT}/readyz" >/dev/null
candidate_openapi="$TEMP_DIR/candidate-openapi.json"
curl --silent --show-error --fail --max-time 20 \
  --output "$candidate_openapi" \
  "http://127.0.0.1:${APP_API_PORT}/openapi.json"
python3 - "$candidate_openapi" "$PROJECT_DIR/frontend/openapi.json" <<'PY'
import hashlib
import json
import pathlib
import sys

candidate_path, expected_path = map(pathlib.Path, sys.argv[1:])
candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
expected = json.loads(expected_path.read_text(encoding="utf-8"))

required_operations = {
    ("/healthz", "get"),
    ("/readyz", "get"),
    ("/api/operator/snapshot", "get"),
    ("/api/operator/actions", "get"),
    ("/api/operator/ads", "get"),
    ("/api/operator/ads/{ad_id}/pause", "post"),
    ("/api/operator/ads/{ad_id}/activate", "post"),
    ("/api/operator/incidents/{incident_id}", "get"),
    ("/api/operator/incidents/{incident_id}/ack", "post"),
    ("/api/analytics/performance", "get"),
    ("/api/analytics/live-budget", "get"),
    ("/api/analytics/daypart", "get"),
    ("/api/v1/integrations/telegram/webhook", "post"),
    ("/api/v1/integrations/alertmanager/webhook", "post"),
}
paths = candidate.get("paths", {})
missing = sorted(
    f"{method.upper()} {path}"
    for path, method in required_operations
    if method not in paths.get(path, {})
)
if missing:
    raise SystemExit(f"missing OpenAPI operations: {missing}")

def contract_digest(document: object) -> str:
    canonical = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

candidate_digest = contract_digest(candidate)
expected_digest = contract_digest(expected)
if candidate_digest != expected_digest:
    raise SystemExit(
        "candidate OpenAPI differs from reviewed artifact: "
        f"candidate={candidate_digest} expected={expected_digest}"
    )
PY

if [[ "$ACTIVATE" != true ]]; then
  trap - ERR
  python3 "$SCRIPT_DIR/release-state.py" abort --state-root "$STATE_DIR"
  log "candidate $COLOR is healthy but inactive; workers were not started"
  exit 0
fi

CUTOVER_DEADLINE_EPOCH=$(( $(date +%s) + CUTOVER_BUDGET_SECONDS ))
run_cutover_bounded python3 "$SCRIPT_DIR/release-state.py" arm-cutover \
  --state-root "$STATE_DIR" --deadline-epoch "$CUTOVER_DEADLINE_EPOCH"
# A first clean installation has no runtime to roll back to. Once its candidate
# passed local health and contracts, persist forward recovery before any public
# route or worker can change.
if [[ -z "$PREVIOUS_COLOR" ]]; then
  run_cutover_bounded python3 "$SCRIPT_DIR/release-state.py" select-initial \
    --state-root "$STATE_DIR"
fi
run_cutover_bounded "$SCRIPT_DIR/bluegreen-switch-caddy.sh" \
  --color "$COLOR" \
  --state-dir "$STATE_DIR" \
  --app-env "$APP_ENV"
run_cutover_bounded python3 "$SCRIPT_DIR/release-state.py" stage \
  --state-root "$STATE_DIR" --stage route_switched
handoff_args=(
  --to-color "$COLOR" --to-release-env "$RELEASE_ENV"
  --app-env "$APP_ENV" --backup-env "$BACKUP_ENV"
)
if [[ "$PREVIOUS_COLOR" == blue || "$PREVIOUS_COLOR" == green ]]; then
  handoff_args+=(--from-color "$PREVIOUS_COLOR" --from-release-env "$PREVIOUS_RELEASE_ENV")
fi
handoff_args+=(--deadline-epoch "$CUTOVER_DEADLINE_EPOCH")
run_cutover_bounded "$SCRIPT_DIR/bluegreen-worker-handoff.sh" "${handoff_args[@]}"
run_cutover_bounded python3 "$SCRIPT_DIR/release-state.py" stage \
  --state-root "$STATE_DIR" --stage workers_handed_off
run_cutover_bounded curl --silent --show-error --fail --retry 6 --retry-delay 2 --retry-all-errors \
  --max-time 10 "${PUBLIC_URL%/}/healthz" >/dev/null
webhook_probe_body="$TEMP_DIR/webhook-probe.json"
webhook_probe_status="$(run_cutover_bounded curl --silent --show-error --max-time 10 \
  --output "$webhook_probe_body" --write-out '%{http_code}' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'X-Telegram-Bot-Api-Secret-Token: platform-route-probe-invalid' \
  --data '{"update_id":0}' \
  "${PUBLIC_URL%/}/api/v1/integrations/telegram/webhook")"
[[ "$webhook_probe_status" == "401" ]] \
  || die "public Telegram webhook route probe returned HTTP $webhook_probe_status"
run_cutover_bounded python3 - "$webhook_probe_body" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("detail") != "Invalid Telegram webhook secret":
    raise SystemExit("public webhook did not reach the dedicated Telegram handler")
PY
alertmanager_probe_body="$TEMP_DIR/alertmanager-probe.json"
alertmanager_probe_status="$(run_cutover_bounded curl --silent --show-error --max-time 10 \
  --output "$alertmanager_probe_body" --write-out '%{http_code}' \
  --request POST \
  --header 'Content-Type: application/json' \
  --header 'Authorization: Bearer platform-route-probe-invalid' \
  --data '{"version":"4","status":"firing","alerts":[{"status":"firing","labels":{"alertname":"RouteProbe"},"annotations":{},"startsAt":"2026-01-01T00:00:00Z"}]}' \
  "${PUBLIC_URL%/}/api/v1/integrations/alertmanager/webhook")"
[[ "$alertmanager_probe_status" == "401" ]] \
  || die "public Alertmanager webhook route probe returned HTTP $alertmanager_probe_status"
run_cutover_bounded python3 - "$alertmanager_probe_body" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("detail") != "Invalid Alertmanager webhook secret":
    raise SystemExit("public webhook did not reach the dedicated Alertmanager handler")
PY
run_cutover_bounded "${compose[@]}" --profile release run --rm \
  telegram_webhook_configurator

run_cutover_bounded python3 "$SCRIPT_DIR/release-state.py" stage \
  --state-root "$STATE_DIR" --stage accepted
commit_args=(commit --state-root "$STATE_DIR")
if [[ -n "${FB_AGENT_RELEASE_COMMIT_FAILPOINT:-}" ]]; then
  commit_args+=(--failpoint "$FB_AGENT_RELEASE_COMMIT_FAILPOINT")
fi
run_cutover_bounded python3 "$SCRIPT_DIR/release-state.py" "${commit_args[@]}"
run_cutover_bounded python3 "$SCRIPT_DIR/release-state.py" ensure-links \
  --state-root "$STATE_DIR" --root-dir "$STATE_PARENT"

# The application pointer is committed, but the durable transaction remains
# open until the parent adopts Alloy, timers, systemd and desktop state. An
# interruption in that post-commit window is resumed by boot reconciliation.
# Failure to stop an idle N-1 web container must not roll the committed app
# state back after Telegram has switched to the webhook.
trap - ERR

if [[ "$PREVIOUS_COLOR" == blue || "$PREVIOUS_COLOR" == green ]]; then
  previous_release_id="$(sed -n 's/^RELEASE_ID=//p' "$PREVIOUS_RELEASE_ENV" | tail -n 1)"
  case "$PREVIOUS_COLOR" in
    blue) previous_api=18100; previous_web=18080; previous_tma=18081 ;;
    green) previous_api=28100; previous_web=28080; previous_tma=28081 ;;
  esac
  APP_COLOR="$PREVIOUS_COLOR" RELEASE_ID="$previous_release_id" \
    APP_API_PORT="$previous_api" APP_WEB_PORT="$previous_web" APP_TMA_PORT="$previous_tma" \
    docker compose -p "fb_agent_${PREVIOUS_COLOR}" --env-file "$PREVIOUS_RELEASE_ENV" \
      -f "$APP_COMPOSE" stop --timeout 90 api frontend mini-app \
      || log "warning: N-1 web containers remain running and require cleanup"
fi

log "release $RELEASE_ID is active on $COLOR"
