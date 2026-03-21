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
log_info "Передаю управление dev.sh"
DEV_SKIP_INFRA=1 exec bash "$ROOT/scripts/dev.sh"
