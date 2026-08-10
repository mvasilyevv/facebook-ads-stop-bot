#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SCRIPT_DIR
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
readonly PROJECT_DIR
readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly AGENT_ENV="$ROOT_DIR/shared/alloy-agent.env"
readonly UNIT_NAME="fb-agent-alloy-agent.service"
VALIDATE_ONLY=false

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
  case "$1" in
    --validate-only) VALIDATE_ONLY=true; shift ;;
    *) die "unknown argument: $1" ;;
  esac
done

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run as root"
for command in install sed stat systemctl; do
  command -v "$command" >/dev/null 2>&1 || die "$command is not installed"
done
[[ -s "$AGENT_ENV" && ! -L "$AGENT_ENV" ]] \
  || die "$AGENT_ENV must be provisioned as a regular file"
[[ "$(stat -Lc '%a' "$AGENT_ENV")" == "600" ]] \
  || die "$AGENT_ENV must have mode 600"

transport="$(sed -n 's/^MONITORING_TRANSPORT=//p' "$AGENT_ENV" | tail -n 1)"
[[ "$transport" == "private_https" || "$transport" == "same_host" ]] \
  || die "MONITORING_TRANSPORT must be private_https or same_host"

for key in PROMETHEUS_REMOTE_WRITE_URL LOKI_WRITE_URL TEMPO_OTLP_HTTP_URL; do
  value="$(sed -n "s/^${key}=//p" "$AGENT_ENV" | tail -n 1)"
  if [[ "$transport" == "private_https" ]]; then
    [[ "$value" == https://* ]] || die "$key must use private HTTPS transport"
  else
    case "$key:$value" in
      PROMETHEUS_REMOTE_WRITE_URL:http://host.docker.internal:9090/api/v1/write|\
      LOKI_WRITE_URL:http://host.docker.internal:3100/loki/api/v1/push|\
      TEMPO_OTLP_HTTP_URL:http://host.docker.internal:4318) ;;
      *) die "$key must use the fixed same-host monitoring endpoint" ;;
    esac
  fi
  [[ "$value" != *"@"* && "$value" != *"?"* && "$value" != *"#"* ]] \
    || die "$key must not carry credentials or query tokens in its URL"
done
for key in PROMETHEUS_READY_URL LOKI_READY_URL TEMPO_READY_URL; do
  value="$(sed -n "s/^${key}=//p" "$AGENT_ENV" | tail -n 1)"
  if [[ "$transport" == "private_https" ]]; then
    [[ "$value" == https://* ]] || die "$key must use private HTTPS transport"
  else
    case "$key:$value" in
      PROMETHEUS_READY_URL:http://172.17.0.1:9090/-/ready|\
      LOKI_READY_URL:http://172.17.0.1:3100/ready|\
      TEMPO_READY_URL:http://172.17.0.1:3200/ready) ;;
      *) die "$key must use the fixed same-host readiness endpoint" ;;
    esac
  fi
  [[ "$value" != *"@"* && "$value" != *"?"* && "$value" != *"#"* ]] \
    || die "$key must not carry credentials or query tokens in its URL"
  case "$key" in
    PROMETHEUS_READY_URL)
      [[ "$value" == *"/-/ready" ]] \
        || die "$key must target the Prometheus /-/ready endpoint"
      ;;
    LOKI_READY_URL|TEMPO_READY_URL)
      [[ "$value" == *"/ready" ]] || die "$key must target a /ready endpoint"
      ;;
  esac
done

for key in ALLOY_IMAGE NODE_EXPORTER_IMAGE CADVISOR_IMAGE; do
  image="$(sed -n "s/^${key}=//p" "$AGENT_ENV" | tail -n 1)"
  [[ "$image" =~ ^[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}$ ]] \
    || die "$key must be an immutable image@sha256 reference"
done
if [[ "$VALIDATE_ONLY" == true ]]; then
  printf 'Application-host Alloy prerequisites validated\n'
  exit 0
fi

install -m 0644 "$PROJECT_DIR/deploy/systemd/$UNIT_NAME" "/etc/systemd/system/$UNIT_NAME"
systemctl daemon-reload
systemctl enable "$UNIT_NAME"
systemctl start "$UNIT_NAME"
systemctl is-enabled --quiet "$UNIT_NAME" \
  || die "application-host Alloy unit is not enabled"
systemctl is-active --quiet "$UNIT_NAME" \
  || die "application-host Alloy unit is not active"
FB_AGENT_ROOT="$ROOT_DIR" "$PROJECT_DIR/scripts/platform-alloy-agent.sh" ready
printf 'Application-host Alloy systemd unit is installed, enabled and ready\n'
