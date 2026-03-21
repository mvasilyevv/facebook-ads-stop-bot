#!/usr/bin/env bash
#
# bootstrap.sh — единая точка входа для запуска проекта.
#
# Использование:
#   ./run.sh              # запуск
#   ./run.sh --check      # только проверки
#   ./run.sh --down       # остановить всё

set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

ROOT="$(repo_root)"
cd "$ROOT"

# ─────────────────────────────────────────────────────────────
# Цвета
# ─────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_RESET='\033[0m'  C_BOLD='\033[1m'
  C_GREEN='\033[0;32m'  C_YELLOW='\033[0;33m'
  C_RED='\033[0;31m'  C_CYAN='\033[0;36m'  C_DIM='\033[2m'
else
  C_RESET='' C_BOLD='' C_GREEN='' C_YELLOW='' C_RED='' C_CYAN='' C_DIM=''
fi

ok()   { printf "  ${C_GREEN}✔${C_RESET} %s\n" "$*"; }
warn() { printf "  ${C_YELLOW}⚠${C_RESET} %s\n" "$*"; }
fail() { printf "  ${C_RED}✘${C_RESET} %s\n" "$*"; }
step() { printf "\n${C_BOLD}${C_CYAN}▸ %s${C_RESET}\n" "$*"; }

ERRORS=0
WARNINGS=0
PIDS=()

cleanup() {
  local pid
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local attempts="${3:-30}"
  local attempt

  if ! command -v curl >/dev/null 2>&1; then
    warn "curl не найден, пропускаю проверку доступности для: $label"
    return 1
  fi

  for attempt in $(seq 1 "$attempts"); do
    if curl --silent --show-error --fail "$url" >/dev/null 2>&1; then
      ok "$label доступен: $url"
      return 0
    fi
    sleep 1
  done

  warn "$label пока не ответил, но процессы уже запущены. Проверьте логи при необходимости"
  return 1
}

print_access_summary() {
  local backend_host="${API_HOST:-127.0.0.1}"
  local backend_port="${API_PORT:-8000}"
  local frontend_host="${FRONTEND_HOST:-127.0.0.1}"
  local frontend_port="${FRONTEND_PORT:-5173}"
  local backend_log="${BACKEND_LOG:-/tmp/fb_agent_backend.log}"
  local worker_log="${WORKER_LOG:-/tmp/fb_agent_worker.log}"
  local browser_host_log="${BROWSER_HOST_LOG:-/tmp/fb_agent_browser_host.log}"
  local frontend_log="${FRONTEND_LOG:-/tmp/fb_agent_frontend.log}"

  printf "\n${C_BOLD}${C_GREEN}Проект запущен.${C_RESET}\n"
  printf "  UI: http://%s:%s\n" "$frontend_host" "$frontend_port"
  printf "  API: http://%s:%s\n" "$backend_host" "$backend_port"
  printf "  Health: http://%s:%s/health\n" "$backend_host" "$backend_port"
  printf "  Docs: http://%s:%s/docs\n" "$backend_host" "$backend_port"
  printf "  Логи backend: %s\n" "$backend_log"
  printf "  Логи worker: %s\n" "$worker_log"
  printf "  Логи browser host: %s\n" "$browser_host_log"
  printf "  Логи frontend: %s\n" "$frontend_log"
  printf "  Дальше ждать не нужно. Для остановки нажмите Ctrl+C.\n"
}

start_backend() {
  local backend_log="${BACKEND_LOG:-/tmp/fb_agent_backend.log}"
  log_info "Запускаю backend API"
  (
    cd "$ROOT"
    exec "$PYTHON" -m uvicorn apps.api.main:app \
      --host "${API_HOST:-127.0.0.1}" \
      --port "${API_PORT:-8000}" \
      --reload
  ) >"$backend_log" 2>&1 &
  PIDS+=("$!")
}

start_worker() {
  local worker_log="${WORKER_LOG:-/tmp/fb_agent_worker.log}"
  log_info "Запускаю фонового воркера"
  (
    cd "$ROOT"
    exec "$PYTHON" -m apps.worker.main
  ) >"$worker_log" 2>&1 &
  PIDS+=("$!")
}

start_browser_host() {
  local browser_host_log="${BROWSER_HOST_LOG:-/tmp/fb_agent_browser_host.log}"
  log_info "Запускаю browser host"
  (
    cd "$ROOT"
    exec "$PYTHON" -m apps.browser_host.main
  ) >"$browser_host_log" 2>&1 &
  PIDS+=("$!")
}

start_frontend() {
  local frontend_dir="$ROOT/frontend"
  local frontend_log="${FRONTEND_LOG:-/tmp/fb_agent_frontend.log}"

  if [[ ! -d "$frontend_dir" ]]; then
    warn "Папка frontend пока отсутствует, запускаю только backend"
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
  ) >"$frontend_log" 2>&1 &
  PIDS+=("$!")
}

trap cleanup INT TERM EXIT

# ─────────────────────────────────────────────────────────────
# Аргументы
# ─────────────────────────────────────────────────────────────
MODE="up"
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --down)  MODE="down"  ;;
    --help|-h)
      printf "Использование: %s [--check|--down|--help]\n" "$0"
      printf "  (без флагов)  Запуск проекта\n"
      printf "  --check       Только проверки\n"
      printf "  --down        Остановить всё\n"
      exit 0
      ;;
    *) die "Неизвестный аргумент: $arg. Используйте --help" ;;
  esac
done

# ─────────────────────────────────────────────────────────────
# --down
# ─────────────────────────────────────────────────────────────
if [[ "$MODE" == "down" ]]; then
  step "Останавливаю стек"
  COMPOSE="$(compose_cmd)"
  eval "$COMPOSE down --remove-orphans"
  ok "Стек остановлен"
  exit 0
fi

# ═════════════════════════════════════════════════════════════
# ПРОВЕРКИ
# ═════════════════════════════════════════════════════════════
printf "\n${C_BOLD}━━━ Facebook Ads Stop Bot · Bootstrap ━━━${C_RESET}\n"

# Docker
step "Docker"
if command -v docker >/dev/null 2>&1; then
  ok "Docker: $(docker --version 2>/dev/null)"
else
  fail "Docker не установлен"
  ERRORS=$((ERRORS + 1))
fi

if docker info >/dev/null 2>&1; then
  ok "Docker daemon запущен"
else
  fail "Docker daemon не запущен — запустите Docker Desktop"
  ERRORS=$((ERRORS + 1))
fi

if has_compose; then
  ok "Docker Compose: $(compose_cmd)"
else
  fail "Docker Compose не найден"
  ERRORS=$((ERRORS + 1))
fi

# Python
step "Python"
PYTHON=""
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  ok "venv: $("$ROOT/.venv/bin/python" --version 2>&1)"
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  ok "python3: $(python3 --version 2>&1)"
  PYTHON="python3"
else
  fail "Python3 не найден"
  ERRORS=$((ERRORS + 1))
fi

# .env
step "Конфигурация"
if [[ -f "$ROOT/.env" ]]; then
  ok ".env существует"
else
  if [[ -f "$ROOT/.env.example" ]]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    ok "Создан .env из .env.example"
  else
    fail ".env и .env.example отсутствуют"
    ERRORS=$((ERRORS + 1))
  fi
fi

if [[ -f "$ROOT/.env" ]]; then
  _check() {
    local val
    val="$(grep "^$1=" "$ROOT/.env" 2>/dev/null | head -1 | cut -d= -f2-)"
    if [[ -z "$val" ]]; then
      if [[ "$2" == "required" ]]; then
        fail "$1 — не заполнен ($3)"; ERRORS=$((ERRORS + 1))
      else
        warn "$1 — не заполнен ($3)"; WARNINGS=$((WARNINGS + 1))
      fi
    else
      ok "$1 = ${val:0:12}..."
    fi
  }
  _check "POSTGRES_PASSWORD"  "required"    "пароль БД"
  _check "VISION_API_TOKEN"   "recommended" "токен Vision API"
  _check "TELEGRAM_BOT_TOKEN" "recommended" "токен Telegram-бота"
  _check "TELEGRAM_CHAT_ID"   "recommended" "ID чата Telegram"
fi

# Итог
step "Результат"
if [[ $ERRORS -gt 0 ]]; then
  fail "Критичных ошибок: $ERRORS"
  [[ "$MODE" == "check" ]] && exit 1
  printf "\n  ${C_RED}Продолжить? [y/N]${C_RESET} "
  read -r ans
  [[ ! "$ans" =~ ^[Yy] ]] && exit 1
elif [[ $WARNINGS -gt 0 ]]; then
  warn "Предупреждений: $WARNINGS (не критично)"
else
  ok "Все проверки пройдены"
fi

[[ "$MODE" == "check" ]] && { printf "\n${C_DIM}Проверки завершены.${C_RESET}\n\n"; exit 0; }

# ═════════════════════════════════════════════════════════════
# ЗАПУСК
# ═════════════════════════════════════════════════════════════
COMPOSE="$(compose_cmd)"

# Инфраструктура
step "Инфраструктура (Docker)"
eval "$COMPOSE up -d postgres redis"
ok "Контейнеры postgres + redis запущены"

# Ждём Postgres
step "Готовность Postgres"
for i in $(seq 1 30); do
  if eval "$COMPOSE exec -T postgres pg_isready -U \${POSTGRES_USER:-facebook_ads_bot}" >/dev/null 2>&1; then
    ok "Postgres готов"
    break
  fi
  [[ $i -eq 30 ]] && die "Postgres не стал готов за 60 секунд"
  sleep 2
done

# Миграции
step "Миграции"
"$PYTHON" -m alembic upgrade head
ok "Миграции применены"

# Сервисы
step "Сервисы"
start_backend
start_worker
start_browser_host
start_frontend
wait_for_http "http://${API_HOST:-127.0.0.1}:${API_PORT:-8000}/health" "Backend API" 25 || true
wait_for_http "http://${FRONTEND_HOST:-127.0.0.1}:${FRONTEND_PORT:-5173}" "Frontend UI" 60 || true
print_access_summary

wait
