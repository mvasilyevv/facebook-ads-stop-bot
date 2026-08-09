#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
# shellcheck source=scripts/browser-control-env.sh
source "$SCRIPT_DIR/browser-control-env.sh"
RUN_CONTAINER_VALIDATORS=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
  if [[ -n "${TEMP_DIR:-}" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT

if [[ "${1:-}" == "--containers" ]]; then
  RUN_CONTAINER_VALIDATORS=true
  shift
fi
(($# == 0)) || die "unknown argument: $1"
for command in bash docker jq python3 rg; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is unavailable"

TEMP_DIR="$(mktemp -d)"
APP_ENV="$TEMP_DIR/app.env"
BROWSER_CONTROL_ENV="$TEMP_DIR/browser-control.env"
BROWSER_MAINTENANCE_ENV="$TEMP_DIR/browser-maintenance.env"
BROWSER_AUTOPAUSE_ENV="$TEMP_DIR/browser-autopause.env"
BROWSER_META_API_ENV="$TEMP_DIR/browser-meta-api.env"
BROWSER_CAMPAIGN_CREATOR_ENV="$TEMP_DIR/browser-campaign-creator.env"
BROWSER_AUTHORITY_ENV="$TEMP_DIR/browser-authority.env"
BACKUP_ENV="$TEMP_DIR/backup.env"
PGBACKREST_CONFIG="$TEMP_DIR/pgbackrest.conf"
MONITORING_ENV="$TEMP_DIR/monitoring.env"
ALERTMANAGER_TOKEN="$TEMP_DIR/alertmanager-webhook-token"
AGENT_ENV="$TEMP_DIR/agent.env"
CADDY_ENV="$TEMP_DIR/caddy.env"
RELEASE_ENV="$PROJECT_DIR/deploy/bluegreen/release-images.env.example"
printf '%s\n' \
  'POSTGRES_DB=fb_stop_bot' \
  'POSTGRES_USER=fb_stop_bot' \
  'POSTGRES_PASSWORD=validation-only-password' \
  'TELEGRAM_WEBHOOK_SECRET=validation-only-webhook-secret' \
  'BROWSER_AUTHORITY_CONSUME_URL=https://app.adpulse.su/api/v1/internal/browser-operations/consume' \
  'BROWSER_MAINTENANCE_CONSUME_URL=https://app.adpulse.su/api/v1/internal/browser-maintenance/consume' \
  >"$APP_ENV"
printf '%s\n' \
  'BROWSER_MAINTENANCE_CAPABILITY_SECRET=validation-only-browser-maintenance-secret-0123456789abcdef' \
  'BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE=validation-only-browser-autopause-secret-0123456789abcdef' \
  'BROWSER_OPERATION_CAPABILITY_SECRET_META_API=validation-only-browser-meta-api-secret-0123456789abcdef' \
  'BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR=validation-only-browser-campaign-creator-secret-0123456789abcdef' \
  'BROWSER_AUTHORITY_CONSUMER_TOKEN=validation-only-browser-authority-token-0123456789abcdef' \
  >"$BROWSER_CONTROL_ENV"
printf '%s\n' \
  'BROWSER_MAINTENANCE_CAPABILITY_SECRET=validation-only-browser-maintenance-secret-0123456789abcdef' \
  >"$BROWSER_MAINTENANCE_ENV"
printf '%s\n' \
  'BROWSER_OPERATION_CAPABILITY_SECRET=validation-only-browser-autopause-secret-0123456789abcdef' \
  >"$BROWSER_AUTOPAUSE_ENV"
printf '%s\n' \
  'BROWSER_OPERATION_CAPABILITY_SECRET=validation-only-browser-meta-api-secret-0123456789abcdef' \
  >"$BROWSER_META_API_ENV"
printf '%s\n' \
  'BROWSER_OPERATION_CAPABILITY_SECRET=validation-only-browser-campaign-creator-secret-0123456789abcdef' \
  >"$BROWSER_CAMPAIGN_CREATOR_ENV"
printf '%s\n' \
  'BROWSER_AUTHORITY_CONSUMER_TOKEN=validation-only-browser-authority-token-0123456789abcdef' \
  >"$BROWSER_AUTHORITY_ENV"
printf '%s\n' \
  'PGBACKREST_REPO1_S3_BUCKET=validation' \
  'PGBACKREST_REPO1_S3_ENDPOINT=s3.example.invalid' \
  'PGBACKREST_REPO1_S3_REGION=eu-central-1' \
  'PGBACKREST_REPO1_S3_KEY=validation' \
  'PGBACKREST_REPO1_S3_KEY_SECRET=validation' \
  'PGBACKREST_REPO1_CIPHER_PASS=validation-only-cipher-pass' >"$BACKUP_ENV"
printf '%s\n' \
  'GF_SECURITY_ADMIN_PASSWORD=validation-only' \
  'GF_SERVER_ROOT_URL=http://localhost:3000' \
  "ALERTMANAGER_WEBHOOK_TOKEN_FILE=$ALERTMANAGER_TOKEN" \
  'NODE_EXPORTER_IMAGE=prom/node-exporter@sha256:4032c6d5bfd752342c3e631c2f1de93ba6b86c41db6b167b9a35372c139e7706' \
  'CADVISOR_IMAGE=gcr.io/cadvisor/cadvisor@sha256:3cde6faf0791ebf7b41d6f8ae7145466fed712ea6f252c935294d2608b1af388' \
  'PROMETHEUS_IMAGE=prom/prometheus@sha256:f6639335d34a77d9d9db382b92eeb7fc00934be8eae81dbc03b31cfe90411a94' \
  'LOKI_IMAGE=grafana/loki@sha256:8b5bd7748d0e4da66cd741ac276e485517514af0bea32167e27c0e1a95bcf8aa' \
  'ALLOY_IMAGE=grafana/alloy@sha256:41c41849989b7e054ccbadc17938ee1e5592fe26bfbc56ef3ffc109c0b0b2739' \
  'TEMPO_IMAGE=grafana/tempo@sha256:d7f4c72e0bad2b42b4e7263b0addaf3f5bbc105cec6b1917eba0aae3b9b70364' \
  'ALERTMANAGER_IMAGE=prom/alertmanager@sha256:9e082985f56f4c8c9f724e18f2288c6708f472e56a5286b8863d080434ea065d' \
  'BLACKBOX_IMAGE=prom/blackbox-exporter@sha256:a50c4c0eda297baa1678cd4dc4712a67fdea713b832d43ce7fcc5f9bea05094d' \
  'GRAFANA_IMAGE=grafana/grafana@sha256:408afb9726de5122b00a2576763a8a57a3c86d5b0eff5305bc994ceb3eb96c3f' \
  >"$MONITORING_ENV"
printf '%s\n' 'validation-only-alertmanager-webhook-token' >"$ALERTMANAGER_TOKEN"
printf '%s\n' \
  'NODE_NAME=validation-agent' \
  'ALLOY_IMAGE=grafana/alloy@sha256:41c41849989b7e054ccbadc17938ee1e5592fe26bfbc56ef3ffc109c0b0b2739' \
  'NODE_EXPORTER_IMAGE=prom/node-exporter@sha256:4032c6d5bfd752342c3e631c2f1de93ba6b86c41db6b167b9a35372c139e7706' \
  'CADVISOR_IMAGE=gcr.io/cadvisor/cadvisor@sha256:3cde6faf0791ebf7b41d6f8ae7145466fed712ea6f252c935294d2608b1af388' \
  'PROMETHEUS_REMOTE_WRITE_URL=https://monitoring.example.invalid/api/v1/write' \
  'LOKI_WRITE_URL=https://monitoring.example.invalid/loki/api/v1/push' \
  'TEMPO_OTLP_HTTP_URL=https://monitoring.example.invalid/otlp' \
  'PROMETHEUS_READY_URL=https://monitoring.example.invalid/-/ready' \
  'LOKI_READY_URL=https://monitoring.example.invalid/loki/ready' \
  'TEMPO_READY_URL=https://monitoring.example.invalid/tempo/ready' >"$AGENT_ENV"
# The bcrypt value is literal; dollar signs must not expand in the validator.
# shellcheck disable=SC2016
printf '%s\n' \
  'API_KEY=validation-only-api-key' \
  'PANEL_BASIC_AUTH_USER=validation' \
  'PANEL_BASIC_AUTH_HASH=$2a$14$DXjnn0C6fVh4VVm1QjBKzuBvSZcNidWvTgKCQlGQw9sm6RYdkVSO2' \
  'DESKTOP_KASM_SERVICE_AUTH_B64=dmFsaWRhdGlvbjp2YWxpZGF0aW9u' >"$CADDY_ENV"
chmod 0600 \
  "$APP_ENV" \
  "$BROWSER_CONTROL_ENV" \
  "$BROWSER_MAINTENANCE_ENV" \
  "$BROWSER_AUTOPAUSE_ENV" \
  "$BROWSER_META_API_ENV" \
  "$BROWSER_CAMPAIGN_CREATOR_ENV" \
  "$BROWSER_AUTHORITY_ENV" \
  "$BACKUP_ENV" \
  "$MONITORING_ENV" \
  "$AGENT_ENV" \
  "$CADDY_ENV"
install -m 0644 "$PROJECT_DIR/deploy/backup/pgbackrest.conf" "$PGBACKREST_CONFIG"
chmod 0644 "$ALERTMANAGER_TOKEN"
browser_control_env_require "$BROWSER_CONTROL_ENV" \
  || die "browser control validation fixture failed the private-file contract"
browser_maintenance_env_require "$BROWSER_MAINTENANCE_ENV" \
  || die "browser maintenance validation fixture failed the private-file contract"
for operation_env in \
  "$BROWSER_AUTOPAUSE_ENV" \
  "$BROWSER_META_API_ENV" \
  "$BROWSER_CAMPAIGN_CREATOR_ENV"; do
  browser_operation_env_require "$operation_env" \
    || die "browser operation validation fixture failed the private-file contract"
done
browser_authority_env_require "$BROWSER_AUTHORITY_ENV" \
  || die "browser authority validation fixture failed the private-file contract"

export APP_ENV_FILE="$APP_ENV"
export BROWSER_CONTROL_ENV_FILE="$BROWSER_CONTROL_ENV"
export BROWSER_MAINTENANCE_ENV_FILE="$BROWSER_MAINTENANCE_ENV"
export BROWSER_AUTOPAUSE_ENV_FILE="$BROWSER_AUTOPAUSE_ENV"
export BROWSER_META_API_ENV_FILE="$BROWSER_META_API_ENV"
export BROWSER_CAMPAIGN_CREATOR_ENV_FILE="$BROWSER_CAMPAIGN_CREATOR_ENV"
export BROWSER_AUTHORITY_ENV_FILE="$BROWSER_AUTHORITY_ENV"
export BROWSER_AUTHORITY_CONSUME_URL="https://app.adpulse.su/api/v1/internal/browser-operations/consume"
export BROWSER_MAINTENANCE_CONSUME_URL="https://app.adpulse.su/api/v1/internal/browser-maintenance/consume"
export BACKUP_ENV_FILE="$BACKUP_ENV"
export PGBACKREST_CONFIG_FILE="$PGBACKREST_CONFIG"
export MONITORING_ENV_FILE="$MONITORING_ENV"
export ALLOY_AGENT_ENV_FILE="$AGENT_ENV"
export RELEASE_ID=validation
export APP_COLOR=blue
export FB_AGENT_BOOTSTRAP_CLUSTER_ID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
export FB_AGENT_TELEMETRY_RELEASE_ID=validation
export APP_API_PORT=18100
export APP_WEB_PORT=18080
export APP_TMA_PORT=18081

infra=(docker compose -p validation_infra --env-file "$RELEASE_ENV" \
  -f "$PROJECT_DIR/deploy/compose/docker-compose.infra.yml")
app=(docker compose -p validation_blue --env-file "$RELEASE_ENV" \
  -f "$PROJECT_DIR/deploy/compose/docker-compose.app.yml")
desktop=(docker compose -p validation_desktop --env-file "$RELEASE_ENV" \
  -f "$PROJECT_DIR/deploy/compose/docker-compose.desktop-agent.yml")
monitoring=(docker compose --env-file "$MONITORING_ENV" \
  -f "$PROJECT_DIR/deploy/monitoring/docker-compose.monitoring.yml")
monitoring_local=(docker compose --env-file "$MONITORING_ENV" \
  -f "$PROJECT_DIR/deploy/monitoring/docker-compose.monitoring.yml" \
  -f "$PROJECT_DIR/deploy/monitoring/docker-compose.local-app.yml")
agent=(docker compose --env-file "$AGENT_ENV" \
  -f "$PROJECT_DIR/deploy/monitoring/docker-compose.agent.yml")

"${infra[@]}" config --quiet
"${app[@]}" --profile migration --profile release --profile workers config --quiet
"${desktop[@]}" config --quiet
"${monitoring[@]}" config --quiet
"${monitoring_local[@]}" config --quiet
"${agent[@]}" config --quiet
"${app[@]}" --profile migration --profile release --profile workers \
  config --format json \
  | jq -e '
      [
        .services
        | to_entries[]
        | select(
            .value.environment.BROWSER_MAINTENANCE_CAPABILITY_SECRET? != null
          )
        | .key
      ] == ["api"]
    ' >/dev/null \
  || die "browser maintenance capability leaked outside the API service"
"${app[@]}" --profile migration --profile release --profile workers \
  config --format json \
  | jq -e '
      [
        .services
        | to_entries[]
        | select(
            .value.environment.BROWSER_AUTHORITY_CONSUMER_TOKEN? != null
          )
        | .key
      ] == ["api"]
    ' >/dev/null \
  || die "browser authority credential leaked outside the API service"
"${app[@]}" --profile migration --profile release --profile workers \
  config --format json \
  | jq -e '
      .services as $services
      | (
        [
        $services
        | to_entries[]
        | select(
            .value.environment.BROWSER_OPERATION_CAPABILITY_SECRET? != null
          )
        | .key
        ]
        | sort
      ) == (
        ["autopause_worker", "campaign_creator", "meta_api"]
        | sort
      )
      and (
        [
          $services.autopause_worker.environment.BROWSER_OPERATION_CAPABILITY_SECRET,
          $services.meta_api.environment.BROWSER_OPERATION_CAPABILITY_SECRET,
          $services.campaign_creator.environment.BROWSER_OPERATION_CAPABILITY_SECRET
        ]
        | unique
        | length
      ) == 3
    ' >/dev/null \
  || die "browser operation capabilities are missing, shared or leaked"
"${app[@]}" --profile migration --profile release --profile workers \
  config --format json \
  | jq -e '
      [
        .services[]
        | (.environment // {})
        | keys[]
        | select(
            . == "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE"
            or . == "BROWSER_OPERATION_CAPABILITY_SECRET_META_API"
            or . == "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR"
          )
      ] == []
    ' >/dev/null \
  || die "browser verifier keyring leaked into the application plane"
"${infra[@]}" config --format json \
  | jq -e '
      [
        .services[]
        | select(
            .environment.BROWSER_MAINTENANCE_CAPABILITY_SECRET? != null
          )
      ] == []
    ' >/dev/null \
  || die "browser maintenance capability leaked into the infrastructure plane"
"${infra[@]}" config --format json \
  | jq -e '
      [
        .services[]
        | select(
            .environment.BROWSER_OPERATION_CAPABILITY_SECRET? != null
          )
      ] == []
    ' >/dev/null \
  || die "browser operation capability leaked into the infrastructure plane"
"${infra[@]}" config --format json \
  | jq -e '
      [
        .services[]
        | select(
            .environment.BROWSER_AUTHORITY_CONSUMER_TOKEN? != null
          )
      ] == []
    ' >/dev/null \
  || die "browser authority credential leaked into the infrastructure plane"
"${desktop[@]}" config --format json \
  | jq -e '
      (
        .services["browser-agent"].environment
        | keys
        | sort
      ) == (
        [
          "BROWSER_AGENT_AM_COLUMNS_QS",
          "BROWSER_AUTHORITY_CONSUME_URL",
          "BROWSER_AUTHORITY_CONSUMER_TOKEN",
          "BROWSER_MAINTENANCE_CONSUME_URL",
          "BROWSER_MAINTENANCE_CAPABILITY_SECRET",
          "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE",
          "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR",
          "BROWSER_OPERATION_CAPABILITY_SECRET_META_API",
          "GRPC_PORT",
          "WORKER_METRICS_PORT"
        ]
        | sort
      )
    ' >/dev/null \
  || die "browser-agent environment exceeds the explicit least-privilege whitelist"
"${desktop[@]}" config --format json \
  | jq -e '
      .services["browser-agent"].environment as $environment
      | (
          $environment.BROWSER_AUTHORITY_CONSUME_URL
          == "https://app.adpulse.su/api/v1/internal/browser-operations/consume"
        )
        and (
          $environment.BROWSER_AUTHORITY_CONSUME_URL
          | contains($environment.BROWSER_AUTHORITY_CONSUMER_TOKEN)
          | not
        )
        and (
          $environment.BROWSER_MAINTENANCE_CONSUME_URL
          == "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume"
        )
        and (
          $environment.BROWSER_MAINTENANCE_CONSUME_URL
          | contains($environment.BROWSER_AUTHORITY_CONSUMER_TOKEN)
          | not
        )
        and (
          $environment.BROWSER_MAINTENANCE_CONSUME_URL
          != $environment.BROWSER_AUTHORITY_CONSUME_URL
        )
        and (
          [
            $environment.BROWSER_MAINTENANCE_CAPABILITY_SECRET,
            $environment.BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE,
            $environment.BROWSER_OPERATION_CAPABILITY_SECRET_META_API,
            $environment.BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR,
            $environment.BROWSER_AUTHORITY_CONSUMER_TOKEN
          ]
          | unique
          | length
        ) == 5
    ' >/dev/null \
  || die "browser-agent authority URL or keyring scoping is invalid"

for compose_name in infra app desktop; do
  case "$compose_name" in
    infra) command=("${infra[@]}") ;;
    app) command=("${app[@]}" --profile migration --profile release --profile workers) ;;
    desktop) command=("${desktop[@]}") ;;
  esac
  while IFS= read -r image; do
    [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] \
      || die "$compose_name contains non-immutable image: $image"
  done < <("${command[@]}" config --images)
  "${command[@]}" config --format json \
    | jq -e '[.services[] | select(has("container_name"))] | length == 0' >/dev/null \
    || die "$compose_name contains fixed container_name entries"
done

for command_name in monitoring monitoring_local agent; do
  case "$command_name" in
    monitoring) command=("${monitoring[@]}") ;;
    monitoring_local) command=("${monitoring_local[@]}") ;;
    agent) command=("${agent[@]}") ;;
  esac
  while IFS= read -r image; do
    [[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] \
      || die "$command_name contains non-immutable image: $image"
  done < <("${command[@]}" config --images)
done

for script in \
  browser-control-env.sh browser-maintenance-lease.sh \
  bluegreen-deploy.sh bluegreen-switch-caddy.sh bluegreen-worker-handoff.sh \
  create-release-manifest.sh deploy-platform-server.sh install-platform-units.sh \
  install-alloy-agent-unit.sh install-host-metrics.sh install-release-reconciler.sh \
  platform-alloy-agent.sh \
  platform-desktop-heal.sh release-backup-gate.sh \
  platform-compose.sh platform-desktop-compose.sh platform-desktop-release.sh \
  platform-desktop-transaction.sh \
  reconcile-platform-release.sh server-platform-release.sh pgbackrest-admin.sh \
  pgbackrest-restore-drill.sh \
  platform-bootstrap.sh run-local.sh validate-platform-configs.sh \
  wait-for-vision-container.sh; do
  bash -n "$PROJECT_DIR/scripts/$script"
done
python3 -m py_compile "$PROJECT_DIR/scripts/backup-adoption-evidence.py"
python3 -m py_compile "$PROJECT_DIR/scripts/host_metrics.py"
python3 -m py_compile "$PROJECT_DIR/scripts/platform-network-inventory.py"
python3 -m py_compile "$PROJECT_DIR/scripts/release-state.py"
python3 -m py_compile "$PROJECT_DIR/scripts/verified-release-exec.py"
python3 -m py_compile "$PROJECT_DIR/migrations/baseline_contract.py"
python3 -m py_compile "$PROJECT_DIR/scripts/run-migrations-locked.py"
python3 -m py_compile "$PROJECT_DIR/scripts/configure-telegram-webhook.py"

# Build stages must resolve every third-party base immutably. api/workers use a
# named, CI-substituted context and postgres validates its digest-pinned ARG.
for dockerfile in \
  docker/Dockerfile.python-base docker/Dockerfile.browser-agent \
  docker/Dockerfile.frontend docker/Dockerfile.mini-app \
  deploy/vision-webtop/Dockerfile; do
  while IFS= read -r from_line; do
    [[ "$from_line" =~ @sha256:[0-9a-f]{64}([[:space:]]|$) ]] \
      || die "$dockerfile contains non-immutable base: $from_line"
  done < <(rg '^FROM[[:space:]]+' "$PROJECT_DIR/$dockerfile")
done
rg -q '^ARG POSTGRES_BASE_IMAGE=postgres:[^[:space:]]+@sha256:[0-9a-f]{64}$' \
  "$PROJECT_DIR/docker/Dockerfile.postgres" \
  || die "Dockerfile.postgres default base is not immutable"
"$PROJECT_DIR/scripts/bluegreen-switch-caddy.sh" \
  --color blue \
  --site-file "$PROJECT_DIR/deploy/caddy/app.adpulse.su.caddy" \
  --desktop-site-file "$PROJECT_DIR/deploy/caddy/desktop.adpulse.su.caddy" \
  --dry-run >/dev/null
if rg -n '\bpromtail\b' "$PROJECT_DIR/deploy/monitoring" \
  --glob '!README.md' >/dev/null; then
  die "Promtail runtime configuration still exists"
fi

rg -q 'verified-release-exec\.py --state app --entrypoint scripts/platform-compose\.sh -- ready' \
  "$PROJECT_DIR/deploy/systemd/fb-agent-healthcheck.service" \
  || die "host healthcheck does not use the supported application runtime"
rg -q 'verified-release-exec\.py --state desktop --entrypoint scripts/platform-desktop-compose\.sh -- up' \
  "$PROJECT_DIR/deploy/systemd/fb-agent-desktop-agent.service" \
  || die "independent desktop-agent systemd runtime is missing"
rg -q 'verified-release-exec\.py --state desktop --entrypoint scripts/platform-desktop-heal\.sh --' \
  "$PROJECT_DIR/deploy/systemd/fb-agent-desktop-heal.service" \
  || die "desktop-aware recovery systemd runtime is missing"
rg -q 'verified-release-exec\.py --state app --entrypoint scripts/platform-alloy-agent\.sh -- up' \
  "$PROJECT_DIR/deploy/systemd/fb-agent-alloy-agent.service" \
  || die "application-host Alloy systemd runtime is missing"
if rg -n '/opt/fb-agent/(current|shared/active-desktop-state/release)/scripts/' \
  "$PROJECT_DIR/deploy/systemd" >/dev/null; then
  die "systemd release entrypoint bypasses the stable verifier"
fi

if [[ "$RUN_CONTAINER_VALIDATORS" == true ]]; then
  docker run --rm --entrypoint /bin/promtool \
    -v "$PROJECT_DIR/deploy/monitoring/prometheus:/etc/prometheus:ro" \
    prom/prometheus:v2.54.1 \
    check config /etc/prometheus/prometheus.yml
  docker run --rm --entrypoint /bin/promtool \
    -v "$PROJECT_DIR/deploy/monitoring/prometheus:/etc/prometheus:ro" \
    prom/prometheus:v2.54.1 \
    test rules /etc/prometheus/tests/host-operations.test.yml
  docker run --rm --entrypoint /bin/amtool \
    -v "$PROJECT_DIR/deploy/monitoring/alertmanager/alertmanager.yml:/etc/alertmanager.yml:ro" \
    -v "$ALERTMANAGER_TOKEN:/run/secrets/alertmanager_webhook_token:ro" \
    prom/alertmanager:v0.33.1 \
    check-config /etc/alertmanager.yml
  docker run --rm \
    -e NODE_NAME=validation \
    -e LOKI_WRITE_URL=http://loki:3100/loki/api/v1/push \
    -e TEMPO_OTLP_HTTP_URL=http://tempo:4318 \
    -v "$PROJECT_DIR/deploy/monitoring/alloy/central.alloy:/etc/alloy/config.alloy:ro" \
    grafana/alloy:v1.17.0 validate /etc/alloy/config.alloy
  docker run --rm \
    -v "$PROJECT_DIR/deploy/monitoring/alloy/browser-agent-local.alloy:/etc/alloy/config.alloy:ro" \
    grafana/alloy:v1.17.0 validate /etc/alloy/config.alloy
  docker run --rm \
    -e NODE_NAME=validation \
    -e PROMETHEUS_REMOTE_WRITE_URL=http://prometheus:9090/api/v1/write \
    -e LOKI_WRITE_URL=http://loki:3100/loki/api/v1/push \
    -e TEMPO_OTLP_HTTP_URL=http://tempo:4318 \
    -v "$PROJECT_DIR/deploy/monitoring/alloy/agent.alloy:/etc/alloy/config.alloy:ro" \
    grafana/alloy:v1.17.0 validate /etc/alloy/config.alloy
  docker run --rm \
    -v "$PROJECT_DIR/deploy/monitoring/tempo/tempo.yml:/etc/tempo.yml:ro" \
    grafana/tempo:2.10.0 \
    -config.file=/etc/tempo.yml -config.verify=true
  docker run --rm \
    -v "$PROJECT_DIR/deploy/monitoring/loki/loki-config.yml:/etc/loki.yml:ro" \
    grafana/loki:2.9.8 \
    -config.file=/etc/loki.yml -verify-config=true
  docker run --rm \
    -v "$PROJECT_DIR/deploy/monitoring/blackbox/blackbox.yml:/etc/blackbox.yml:ro" \
    prom/blackbox-exporter:v0.27.0 \
    --config.file=/etc/blackbox.yml --config.check
  docker run --rm \
    -v "$PROJECT_DIR/deploy/caddy:/etc/caddy/sites-enabled:ro" \
    -v "$TEMP_DIR:/var/log/caddy" \
    caddy:2.10.2 \
    caddy validate --config /etc/caddy/sites-enabled/Caddyfile.validation \
    --adapter caddyfile --envfile /var/log/caddy/caddy.env
fi

printf 'Platform configuration validation: OK\n'
