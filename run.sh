#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Единый скрипт запуска FB Stop Bot
# Использование:
#   ./run.sh            — запуск всех сервисов
#   ./run.sh --dev      — запуск в dev-режиме (API с --reload)
#   ./run.sh --down     — остановка всех сервисов
#   ./run.sh --restart  — перезапуск (--down + запуск)
#   ./run.sh --logs     — логи всех процессов

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

LOG_DIR="$SCRIPT_DIR/.logs"
PID_FILE="$LOG_DIR/pids.txt"

wait_for_process_exit() {
    local pid="$1"
    local timeout_seconds="${2:-15}"
    local elapsed=0

    while is_process_active "$pid"; do
        if [ "$elapsed" -ge "$timeout_seconds" ]; then
            return 1
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    reap_process_if_possible "$pid"
    return 0
}

is_process_active() {
    local pid="$1"
    local process_stat=""

    if ! kill -0 "$pid" 2>/dev/null; then
        return 1
    fi

    process_stat="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
    if [ -z "$process_stat" ]; then
        return 1
    fi

    case "$process_stat" in
        Z*|*Z*)
            return 1
            ;;
    esac

    return 0
}

reap_process_if_possible() {
    local pid="$1"

    wait "$pid" 2>/dev/null || true
}

stop_process_by_pid() {
    local pid="$1"
    local name="$2"
    local timeout_seconds="${3:-15}"

    if ! is_process_active "$pid"; then
        reap_process_if_possible "$pid"
        return 0
    fi

    echo -e "  Останавливаю $name (PID $pid)"
    kill "$pid" 2>/dev/null || true

    if wait_for_process_exit "$pid" "$timeout_seconds"; then
        echo -e "  Остановлен $name (PID $pid)"
        return 0
    fi

    echo -e "${YELLOW}  $name (PID $pid) не завершился за ${timeout_seconds}с, отправляю SIGKILL${NC}"
    kill -9 "$pid" 2>/dev/null || true

    if wait_for_process_exit "$pid" 5; then
        echo -e "  Остановлен $name (PID $pid) через SIGKILL"
        return 0
    fi

    echo -e "${RED}  Не удалось дождаться остановки $name (PID $pid)${NC}"
    return 1
}

cleanup_singleton_pid_file() {
    local pid_file="$1"
    local pattern="$2"

    if pgrep -f "$pattern" >/dev/null 2>&1; then
        return 0
    fi

    if [ -f "$pid_file" ]; then
        rm -f "$pid_file"
        echo -e "  Удалён stale PID-файл: $pid_file"
    fi
}

cleanup_worker_singleton_pid_files() {
    cleanup_singleton_pid_file "/tmp/fb_observer.pid" "run_observer.py"
    cleanup_singleton_pid_file "/tmp/fb_disable_worker.pid" "run_disable_worker.py"
    cleanup_singleton_pid_file "/tmp/fb_enable_worker.pid" "run_enable_worker.py"
}

check_process_started() {
    local pid="$1"
    local name="$2"
    local log_file="$3"

    if is_process_active "$pid"; then
        return 0
    fi

    echo -e "${RED}❌ $name завершился сразу после запуска${NC}"
    if [ -f "$log_file" ]; then
        echo -e "${YELLOW}Последние строки $log_file:${NC}"
        tail -20 "$log_file" || true
    fi
    return 1
}

terminate_matching_processes() {
    local name="$1"
    local pattern="$2"
    local pids=""

    pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
    [ -n "$pids" ] || return 0

    echo -e "${YELLOW}🔄 Найдены старые процессы $name:${NC}"
    for pid in $pids; do
        stop_process_by_pid "$pid" "$name"
    done
}

# ==========================================
# Остановка сервисов
# ==========================================
stop_all() {
    echo -e "${YELLOW}⏹ Останавливаю сервисы...${NC}"

    if [ -f "$PID_FILE" ]; then
        while read -r pid name; do
            stop_process_by_pid "$pid" "$name"
        done < "$PID_FILE"
        rm -f "$PID_FILE"
    fi

    terminate_matching_processes "Observer Worker" "run_observer.py"
    terminate_matching_processes "Disable Worker" "run_disable_worker.py"
    terminate_matching_processes "Enable Worker" "run_enable_worker.py"
    terminate_matching_processes "Enable Recommendation Worker" "run_enable_recommendation_worker.py"
    terminate_matching_processes "Enable Recommendation Worker" "apps.enable_recommendation_worker.main"
    terminate_matching_processes "Telegram Poller" "apps.telegram_poller.main"
    terminate_matching_processes "API" "uvicorn apps.api.main:app"
    cleanup_worker_singleton_pid_files

    echo -e "${YELLOW}⏹ Останавливаю Docker контейнеры...${NC}"
    docker compose down 2>/dev/null || true

    echo -e "${GREEN}✅ Все сервисы остановлены${NC}"
}

# ==========================================
# Показ логов
# ==========================================
show_logs() {
    echo -e "${BLUE}📋 Логи сервисов:${NC}"
    for f in "$LOG_DIR"/*.log; do
        [ -f "$f" ] || continue
        echo -e "\n${YELLOW}=== $(basename "$f") ===${NC}"
        tail -20 "$f"
    done
}

# ==========================================
# Обработка аргументов
# ==========================================
case "${1:-}" in
    --down|--stop)
        stop_all
        exit 0
        ;;
    --restart)
        stop_all
        echo ""
        exec "$0"
        ;;
    --dev)
        export DEV_MODE=1
        ;;
    --logs)
        show_logs
        exit 0
        ;;
esac

# Ловим Ctrl+C / SIGTERM / аварийный выход на любом этапе запуска
CLEANUP_DONE=0
do_cleanup() {
    [ "$CLEANUP_DONE" -eq 0 ] || return 0
    CLEANUP_DONE=1
    stop_all
}
trap 'do_cleanup; exit 1' INT TERM
trap 'do_cleanup' EXIT

# ==========================================
# Проверки перед запуском
# ==========================================
echo -e "${BLUE}🛑 FB Stop Bot — запуск${NC}"
echo ""

# Проверяем .env
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        echo -e "${YELLOW}⚠️  .env не найден. Копирую из .env.example${NC}"
        cp .env.example .env
        echo -e "${RED}❗ Отредактируй .env перед запуском (VISION_X_TOKEN, TELEGRAM_BOT_TOKEN и т.д.)${NC}"
        exit 1
    else
        echo -e "${RED}❌ .env не найден${NC}"
        exit 1
    fi
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a

# Проверяем Docker
if ! command -v docker &>/dev/null; then
    echo -e "${RED}❌ Docker не установлен${NC}"
    exit 1
fi

# Проверяем Python
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}❌ Python3 не установлен${NC}"
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
    echo -e "${RED}❌ Требуется Python 3.12+${NC}"
    python3 --version || true
    exit 1
fi

# Останавливаем старые процессы если они запущены
if [ -f "$PID_FILE" ] && [ -s "$PID_FILE" ]; then
    echo -e "${YELLOW}🔄 Останавливаю предыдущие процессы...${NC}"
    while read -r pid name; do
        stop_process_by_pid "$pid" "$name"
    done < "$PID_FILE"
fi

terminate_matching_processes "Observer Worker" "run_observer.py"
terminate_matching_processes "Disable Worker" "run_disable_worker.py"
terminate_matching_processes "Enable Worker" "run_enable_worker.py"
terminate_matching_processes "Enable Recommendation Worker" "run_enable_recommendation_worker.py"
terminate_matching_processes "Enable Recommendation Worker" "apps.enable_recommendation_worker.main"
terminate_matching_processes "Telegram Poller" "apps.telegram_poller.main"
terminate_matching_processes "API" "uvicorn apps.api.main:app"
cleanup_worker_singleton_pid_files

# Создаём директорию для логов
mkdir -p "$LOG_DIR"
> "$PID_FILE"

# ==========================================
# 1. Docker — Postgres
# ==========================================
echo -e "${BLUE}🐳 Запускаю Docker контейнеры (Postgres)...${NC}"
docker compose up -d

# Ждём готовности Postgres
echo -e "${BLUE}⏳ Жду готовности Postgres...${NC}"
POSTGRES_USER_VALUE="${POSTGRES_USER:-fb_stop_bot_v2}"
POSTGRES_DB_VALUE="${POSTGRES_DB:-fb_stop_bot_v2}"
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U "$POSTGRES_USER_VALUE" -d "$POSTGRES_DB_VALUE" &>/dev/null; then
        echo -e "${GREEN}✅ Postgres готов${NC}"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo -e "${RED}❌ Postgres не запустился за 30 секунд${NC}"
        exit 1
    fi
    sleep 1
done

# ==========================================
# 2. Python venv + зависимости
# ==========================================
if [ ! -d .venv ]; then
    echo -e "${BLUE}📦 Создаю Python virtual environment...${NC}"
    python3 -m venv .venv
fi

DEPS_HASH_FILE="$LOG_DIR/.pyproject_hash"
CURRENT_HASH="$(md5 -q pyproject.toml 2>/dev/null || md5sum pyproject.toml 2>/dev/null | cut -d' ' -f1)"
CACHED_HASH=""
[ -f "$DEPS_HASH_FILE" ] && CACHED_HASH="$(cat "$DEPS_HASH_FILE")"

if [ "$CURRENT_HASH" != "$CACHED_HASH" ]; then
    echo -e "${BLUE}📦 Устанавливаю зависимости...${NC}"
    .venv/bin/pip install -q -e '.[dev]' 2>&1 | tail -3
    echo "$CURRENT_HASH" > "$DEPS_HASH_FILE"
else
    echo -e "${GREEN}📦 Зависимости актуальны (pyproject.toml не менялся)${NC}"
fi

# ==========================================
# 3. Миграции БД
# ==========================================
echo -e "${BLUE}🗄️ Применяю миграции БД...${NC}"
if .venv/bin/python -m alembic upgrade head 2>&1; then
    :
elif find migrations/versions -maxdepth 1 -name '*.py' ! -name '__init__.py' | grep -q .; then
    echo -e "${RED}❌ Alembic завершился с ошибкой. Автосоздание таблиц отключено, чтобы не рассинхронизировать схему с миграциями.${NC}"
    exit 1
else
    echo -e "${YELLOW}⚠️  Файлы миграций не найдены, пробую аварийное создание таблиц напрямую${NC}"
    .venv/bin/python -c "
import asyncio
from core.db import get_engine
from core.db.base import Base
from core.models import *  # noqa: F401,F403
async def init():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print('Таблицы созданы')
asyncio.run(init())
"
fi

# ==========================================
# 4. Запуск API
# ==========================================
DEV_MODE="${DEV_MODE:-0}"
UVICORN_EXTRA_ARGS=""
if [ "$DEV_MODE" -eq 1 ]; then
    UVICORN_EXTRA_ARGS="--reload"
    echo -e "${BLUE}🚀 Запускаю API (порт 8100, dev mode)...${NC}"
else
    echo -e "${BLUE}🚀 Запускаю API (порт 8100)...${NC}"
fi
# shellcheck disable=SC2086
.venv/bin/uvicorn apps.api.main:app \
    --host 0.0.0.0 --port 8100 $UVICORN_EXTRA_ARGS \
    > "$LOG_DIR/api.log" 2>&1 &
API_PID=$!
echo "$API_PID api" >> "$PID_FILE"
echo -e "${GREEN}  API PID: $API_PID${NC}"

# Ждём готовности API через /health
echo -e "${BLUE}⏳ Жду готовности API...${NC}"
for i in $(seq 1 20); do
    if curl -sf http://localhost:8100/health >/dev/null 2>&1; then
        echo -e "${GREEN}✅ API отвечает на /health${NC}"
        break
    fi
    if ! is_process_active "$API_PID"; then
        echo -e "${RED}❌ API процесс завершился при запуске${NC}"
        tail -20 "$LOG_DIR/api.log" || true
        exit 1
    fi
    if [ "$i" -eq 20 ]; then
        echo -e "${YELLOW}⚠️  API не ответил на /health за 20с, продолжаю запуск${NC}"
    fi
    sleep 1
done

# ==========================================
# 5. Запуск Observer Worker
# ==========================================
echo -e "${BLUE}🔍 Запускаю Observer Worker...${NC}"
.venv/bin/python run_observer.py > "$LOG_DIR/observer.log" 2>&1 &
OBSERVER_PID=$!
echo "$OBSERVER_PID observer" >> "$PID_FILE"
echo -e "${GREEN}  Observer PID: $OBSERVER_PID${NC}"

# ==========================================
# 6. Запуск Disable Worker
# ==========================================
echo -e "${BLUE}🔴 Запускаю Disable Worker...${NC}"
.venv/bin/python run_disable_worker.py > "$LOG_DIR/disable_worker.log" 2>&1 &
DISABLE_PID=$!
echo "$DISABLE_PID disable_worker" >> "$PID_FILE"
echo -e "${GREEN}  Disable Worker PID: $DISABLE_PID${NC}"

# ==========================================
# 7. Запуск Enable Worker
# ==========================================
echo -e "${BLUE}🟢 Запускаю Enable Worker...${NC}"
.venv/bin/python run_enable_worker.py > "$LOG_DIR/enable_worker.log" 2>&1 &
ENABLE_PID=$!
echo "$ENABLE_PID enable_worker" >> "$PID_FILE"
echo -e "${GREEN}  Enable Worker PID: $ENABLE_PID${NC}"

# ==========================================
# 8. Запуск Enable Recommendation Worker
# ==========================================
if [ -f run_enable_recommendation_worker.py ]; then
    echo -e "${BLUE}🟡 Запускаю Enable Recommendation Worker...${NC}"
    .venv/bin/python run_enable_recommendation_worker.py > "$LOG_DIR/enable_recommendation_worker.log" 2>&1 &
    ENABLE_RECO_PID=$!
    echo "$ENABLE_RECO_PID enable_recommendation_worker" >> "$PID_FILE"
    echo -e "${GREEN}  Enable Recommendation Worker PID: $ENABLE_RECO_PID${NC}"
else
    echo -e "${YELLOW}⚠️  run_enable_recommendation_worker.py не найден, пропускаю запуск Enable Recommendation Worker${NC}"
fi

# ==========================================
# 9. Запуск Telegram Poller
# ==========================================
echo -e "${BLUE}🤖 Запускаю Telegram Poller...${NC}"
.venv/bin/python -m apps.telegram_poller.main > "$LOG_DIR/telegram.log" 2>&1 &
TG_PID=$!
echo "$TG_PID telegram" >> "$PID_FILE"
echo -e "${GREEN}  Telegram PID: $TG_PID${NC}"

# ==========================================
# 10. Запуск Frontend (Vite)
# ==========================================
if [ -d frontend ]; then
    echo -e "${BLUE}🎨 Запускаю Frontend (Vite)...${NC}"
    # Убиваем старые Vite-процессы ТОЛЬКО этого проекта
    pgrep -f "node.*vite.*$SCRIPT_DIR/frontend" 2>/dev/null | xargs kill 2>/dev/null || true
    sleep 1
    cd frontend
    if [ ! -d node_modules ]; then
        npm install --silent 2>&1 | tail -3
    fi
    # Прокидываем API_KEY из .env в Vite
    VITE_API_KEY="${API_KEY:-}" npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
    FRONTEND_PID=$!
    echo "$FRONTEND_PID frontend" >> "$PID_FILE"
    echo -e "${GREEN}  Frontend PID: $FRONTEND_PID${NC}"
    cd "$SCRIPT_DIR"

    # Ждём пока Vite напишет реальный порт в лог
    FRONTEND_URL="http://localhost:5173"
    for i in $(seq 1 15); do
        VITE_PORT=$(sed -n 's/.*Local: *http:\/\/localhost:\([0-9]*\).*/\1/p' "$LOG_DIR/frontend.log" 2>/dev/null | head -1)
        if [ -n "$VITE_PORT" ]; then
            FRONTEND_URL="http://localhost:$VITE_PORT"
            break
        fi
        sleep 1
    done
fi

# ==========================================
# 11. Проверка что процессы не завершились сразу
# ==========================================
sleep 2
BOOT_OK=1
check_process_started "$API_PID" "API" "$LOG_DIR/api.log" || BOOT_OK=0
check_process_started "$OBSERVER_PID" "Observer Worker" "$LOG_DIR/observer.log" || BOOT_OK=0
check_process_started "$DISABLE_PID" "Disable Worker" "$LOG_DIR/disable_worker.log" || BOOT_OK=0
check_process_started "$ENABLE_PID" "Enable Worker" "$LOG_DIR/enable_worker.log" || BOOT_OK=0
if [ -n "${ENABLE_RECO_PID:-}" ]; then
    check_process_started "$ENABLE_RECO_PID" "Enable Recommendation Worker" "$LOG_DIR/enable_recommendation_worker.log" || BOOT_OK=0
fi
check_process_started "$TG_PID" "Telegram Poller" "$LOG_DIR/telegram.log" || BOOT_OK=0
if [ -n "${FRONTEND_PID:-}" ]; then
    check_process_started "$FRONTEND_PID" "Frontend" "$LOG_DIR/frontend.log" || BOOT_OK=0
fi

if [ "$BOOT_OK" -eq 0 ]; then
    exit 1
fi

# ==========================================
# 12. Caffeinate — запрет сна ноутбука
# ==========================================
if command -v caffeinate &>/dev/null; then
    # -w $$ — caffeinate завершится автоматически при гибели скрипта
    caffeinate -i -d -w $$ &
    CAFFEINATE_PID=$!
    echo "$CAFFEINATE_PID caffeinate" >> "$PID_FILE"
    CAFFEINATE_ENABLED=1
else
    CAFFEINATE_ENABLED=0
fi

# ==========================================
# Итог
# ==========================================
echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ FB Stop Bot запущен!${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo -e "  🌐 API:       ${BLUE}http://localhost:8100/docs${NC}"
echo -e "  📊 Dashboard: ${BLUE}${FRONTEND_URL}${NC}"
echo -e "  🗄️ Postgres:  localhost:5433"
echo ""
echo -e "  📋 Логи:      ${YELLOW}$LOG_DIR/${NC}"
echo -e "  ⏹  Остановка: ${YELLOW}./run.sh --down${NC}"
echo -e "  📋 Просмотр:  ${YELLOW}./run.sh --logs${NC}"
echo ""
if [ "${CAFFEINATE_ENABLED:-0}" -eq 1 ]; then
    echo -e "${YELLOW}☕ Сон ноутбука заблокирован (caffeinate)${NC}"
else
    echo -e "${YELLOW}⚠️  caffeinate недоступен — запрет сна не включён${NC}"
fi
echo ""

# Ждём завершения — Ctrl+C останавливает всё
echo -e "${BLUE}Нажмите Ctrl+C для остановки всех сервисов${NC}"
wait
