#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ROOT="$(repo_root)"
FRONTEND_DIR="$ROOT/frontend"

if [[ ! -d "$FRONTEND_DIR" ]]; then
  log_warn "Папка frontend пока отсутствует, фронтенд не запускаю"
  exit 0
fi

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
  die "В папке frontend не найден package.json"
fi

cd "$FRONTEND_DIR"

if [[ -f pnpm-lock.yaml ]]; then
  ensure_command pnpm
  log_info "Запускаю React UI через pnpm"
  exec pnpm run dev -- --host "${FRONTEND_HOST:-127.0.0.1}" --port "${FRONTEND_PORT:-5173}"
fi

if [[ -f yarn.lock ]]; then
  ensure_command yarn
  log_info "Запускаю React UI через yarn"
  exec yarn dev --host "${FRONTEND_HOST:-127.0.0.1}" --port "${FRONTEND_PORT:-5173}"
fi

ensure_command npm
log_info "Запускаю React UI через npm"
exec npm run dev -- --host "${FRONTEND_HOST:-127.0.0.1}" --port "${FRONTEND_PORT:-5173}"
