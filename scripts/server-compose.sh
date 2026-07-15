#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

readonly ROOT_DIR="${FB_AGENT_ROOT:-/opt/fb-agent}"
readonly CURRENT_DIR="$ROOT_DIR/current"
readonly PROJECT_NAME="${COMPOSE_PROJECT_NAME:-fb_agent}"
[[ -d "$CURRENT_DIR" ]] || { printf 'ERROR: current release is missing\n' >&2; exit 1; }
[[ -f "$CURRENT_DIR/.release-id" ]] || { printf 'ERROR: release id is missing\n' >&2; exit 1; }

export IMAGE_TAG="$(<"$CURRENT_DIR/.release-id")"
compose=(docker compose -p "$PROJECT_NAME" --env-file "$CURRENT_DIR/.env" -f "$CURRENT_DIR/docker-compose.yml" --profile web --profile agent)
command="${1:-status}"
shift || true

case "$command" in
  up) exec "${compose[@]}" up -d --remove-orphans --wait --wait-timeout 240 ;;
  stop) exec "${compose[@]}" stop --timeout 90 ;;
  restart) "${compose[@]}" stop --timeout 90; exec "${compose[@]}" up -d --remove-orphans --wait --wait-timeout 240 ;;
  status) exec "${compose[@]}" ps "$@" ;;
  logs) exec "${compose[@]}" logs --tail="${LOG_TAIL:-200}" "$@" ;;
  ready)
    curl --silent --show-error --fail --max-time 10 http://127.0.0.1:8100/healthz >/dev/null
    curl --silent --show-error --fail --max-time 10 http://127.0.0.1:8100/readyz >/dev/null
    printf 'Application health and readiness: OK\n'
    ;;
  compose) exec "${compose[@]}" "$@" ;;
  *) printf 'ERROR: unsupported command: %s\n' "$command" >&2; exit 2 ;;
esac
