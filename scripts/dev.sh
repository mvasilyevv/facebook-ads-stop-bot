#!/usr/bin/env bash

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ROOT="$(repo_root)"
PYTHON="$(python_bin "$ROOT")"
BACKEND_LOG="${BACKEND_LOG:-/tmp/fb_agent_backend.log}"
WORKER_LOG="${WORKER_LOG:-/tmp/fb_agent_worker.log}"
BROWSER_HOST_LOG="${BROWSER_HOST_LOG:-/tmp/fb_agent_browser_host.log}"
FRONTEND_LOG="${FRONTEND_LOG:-/tmp/fb_agent_frontend.log}"
PIDS=()
INFRA_STARTED_BY_SCRIPT=0

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done

  if [[ "$INFRA_STARTED_BY_SCRIPT" == "1" && "${DEV_STOP_INFRA_ON_EXIT:-0}" == "1" ]]; then
    log_info "Останавливаю инфраструктуру, которую запустил текущий dev-скрипт"
    (cd "$ROOT" && eval "$(compose_cmd) stop postgres redis") >/dev/null 2>&1 || true
  fi
}

trap cleanup INT TERM EXIT

start_backend() {
  log_info "Запускаю backend API"
  (
    cd "$ROOT"
    exec "$PYTHON" -m uvicorn apps.api.main:app --host "${API_HOST:-127.0.0.1}" --port "${API_PORT:-8000}" --reload
  ) >"$BACKEND_LOG" 2>&1 &
  PIDS+=("$!")
}

start_worker() {
  log_info "Запускаю фонового воркера"
  (
    cd "$ROOT"
    exec "$PYTHON" -m apps.worker.main
  ) >"$WORKER_LOG" 2>&1 &
  PIDS+=("$!")
}

start_browser_host() {
  log_info "Запускаю browser host"
  (
    cd "$ROOT"
    exec "$PYTHON" -m apps.browser_host.main
  ) >"$BROWSER_HOST_LOG" 2>&1 &
  PIDS+=("$!")
}

start_infra_if_possible() {
  if [[ "${DEV_SKIP_INFRA:-0}" == "1" ]]; then
    log_warn "Автозапуск инфраструктуры отключен через DEV_SKIP_INFRA=1"
    return
  fi

  if has_compose; then
    local compose
    compose="$(compose_cmd)"
    log_info "Поднимаю Postgres и Redis через compose"
    (
      cd "$ROOT"
      eval "$compose up -d postgres redis"
    )
    INFRA_STARTED_BY_SCRIPT=1
    return
  fi

  log_warn "Docker Compose не найден. Предполагаю, что Postgres и Redis уже запущены отдельно"
}

run_migrations() {
  local attempt
  for attempt in {1..10}; do
    log_info "Применяю миграции базы данных, попытка $attempt из 10"
    if (
      cd "$ROOT"
      exec "$PYTHON" -m alembic upgrade head
    ); then
      return
    fi
    sleep 2
  done

  die "Не удалось применить миграции после нескольких попыток"
}

start_frontend() {
  local frontend_dir="$ROOT/frontend"

  if [[ ! -d "$frontend_dir" ]]; then
    log_warn "Папка frontend пока отсутствует, запускаю только backend"
    return
  fi

  if [[ ! -f "$frontend_dir/package.json" ]]; then
    die "В папке frontend не найден package.json"
  fi

  log_info "Запускаю React UI"
  (
    cd "$frontend_dir"
    if [[ -f pnpm-lock.yaml ]]; then
      ensure_command pnpm
      if [[ ! -d node_modules ]]; then
        log_info "Устанавливаю frontend-зависимости через pnpm"
        pnpm install
      fi
      exec pnpm run dev -- --host "${FRONTEND_HOST:-127.0.0.1}" --port "${FRONTEND_PORT:-5173}"
    fi
    if [[ -f yarn.lock ]]; then
      ensure_command yarn
      if [[ ! -d node_modules ]]; then
        log_info "Устанавливаю frontend-зависимости через yarn"
        yarn install --frozen-lockfile
      fi
      exec yarn dev --host "${FRONTEND_HOST:-127.0.0.1}" --port "${FRONTEND_PORT:-5173}"
    fi
    ensure_command npm
    if [[ ! -d node_modules ]]; then
      log_info "Устанавливаю frontend-зависимости через npm"
      npm install
    fi
    exec npm run dev -- --host "${FRONTEND_HOST:-127.0.0.1}" --port "${FRONTEND_PORT:-5173}"
  ) >"$FRONTEND_LOG" 2>&1 &
  PIDS+=("$!")
}

cd "$ROOT"
log_info "Старт локальной разработки"
log_info "Backend: ${API_HOST:-127.0.0.1}:${API_PORT:-8000}"
log_info "Frontend: ${FRONTEND_HOST:-127.0.0.1}:${FRONTEND_PORT:-5173}"
log_info "Логи backend: $BACKEND_LOG"
log_info "Логи worker: $WORKER_LOG"
log_info "Логи browser host: $BROWSER_HOST_LOG"
log_info "Логи frontend: $FRONTEND_LOG"

start_infra_if_possible
run_migrations
start_backend
start_worker
start_browser_host
start_frontend

wait
