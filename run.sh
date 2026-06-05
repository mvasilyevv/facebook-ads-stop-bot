#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Единый скрипт запуска FB Stop Bot
# Использование:
#   ./run.sh            — запуск всех сервисов (cloudflared туннели по умолчанию ON)
#   ./run.sh --dev      — запуск в dev-режиме (API с --reload)
#   ./run.sh --tunnel   — явно включить cloudflared quick-tunnels (по умолчанию)
#   ./run.sh --no-tunnel — выключить туннели
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
SUPERVISOR_CONF="$SCRIPT_DIR/supervisord.conf"
SUPERVISOR_PID_FILE="$SCRIPT_DIR/supervisord.pid"
SUPERVISOR_SOCK="/tmp/fb_agent_supervisor.sock"

append_pid() {
    local pid="$1"
    local name="$2"

    mkdir -p "$LOG_DIR"
    echo "$pid $name" >> "$PID_FILE"
}

get_supervisorctl_bin() {
    if command -v supervisorctl >/dev/null 2>&1; then
        command -v supervisorctl
        return 0
    fi

    if [ -x "$SCRIPT_DIR/.venv/bin/supervisorctl" ]; then
        echo "$SCRIPT_DIR/.venv/bin/supervisorctl"
        return 0
    fi

    return 1
}

get_supervisord_bin() {
    if command -v supervisord >/dev/null 2>&1; then
        command -v supervisord
        return 0
    fi

    if [ -x "$SCRIPT_DIR/.venv/bin/supervisord" ]; then
        echo "$SCRIPT_DIR/.venv/bin/supervisord"
        return 0
    fi

    return 1
}

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
    cleanup_singleton_pid_file "/tmp/fb_observer.pid" "run_observer_worker.py"
    cleanup_singleton_pid_file "/tmp/fb_disable_worker.pid" "run_disable_worker.py"
    cleanup_singleton_pid_file "/tmp/fb_enable_worker.pid" "run_enable_worker.py"
}

wait_for_postgres_ready() {
    local timeout_seconds="${1:-45}"
    local service_name="postgres"
    local container_id=""
    local health_status=""
    local elapsed=0

    while [ "$elapsed" -lt "$timeout_seconds" ]; do
        container_id="$(docker compose ps -q "$service_name" 2>/dev/null | tr -d '[:space:]')"

        if [ -n "$container_id" ]; then
            health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$container_id" 2>/dev/null || true)"

            # Если healthcheck уже стал healthy, можно продолжать без дополнительных probe.
            if [ "$health_status" = "healthy" ]; then
                return 0
            fi

            # Проверяем фактическую готовность сервера без привязки к имени БД/роли из текущего .env.
            if docker compose exec -T "$service_name" pg_isready -q &>/dev/null; then
                return 0
            fi

            # На некоторых запусках exec может кратко флапать, хотя порт уже слушается.
            if nc -z 127.0.0.1 "${POSTGRES_PORT:-5433}" >/dev/null 2>&1; then
                return 0
            fi
        fi

        sleep 1
        elapsed=$((elapsed + 1))
    done

    echo -e "${RED}❌ Postgres не запустился за ${timeout_seconds} секунд${NC}"
    echo -e "${YELLOW}Состояние docker compose:${NC}"
    docker compose ps || true

    if [ -n "$container_id" ]; then
        echo -e "${YELLOW}Последние строки логов Postgres:${NC}"
        docker compose logs --tail=40 "$service_name" || true
    fi

    return 1
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

show_worker_logs_tail() {
    local logs=(
        "$LOG_DIR/observer.log"
        "$LOG_DIR/disable_worker.log"
        "$LOG_DIR/enable_worker.log"
        "$LOG_DIR/enable_recommendation_worker.log"
        "$LOG_DIR/telegram.log"
    )
    local log_file=""

    for log_file in "${logs[@]}"; do
        [ -f "$log_file" ] || continue
        echo -e "${YELLOW}Последние строки $(basename "$log_file"):${NC}"
        tail -15 "$log_file" || true
    done
}

wait_for_supervisor_running() {
    local supervisorctl_bin="$1"
    local timeout_seconds="${2:-30}"
    local elapsed=0
    local status_output=""
    local not_running_count=0

    while [ "$elapsed" -lt "$timeout_seconds" ]; do
        status_output="$("$supervisorctl_bin" -c "$SUPERVISOR_CONF" status 2>&1 || true)"

        if printf '%s\n' "$status_output" | grep -Eq '\b(FATAL|BACKOFF|EXITED|STOPPED)\b'; then
            echo -e "${RED}❌ Один из воркеров supervisord не запустился${NC}"
            printf '%s\n' "$status_output"
            show_worker_logs_tail
            return 1
        fi

        not_running_count="$(printf '%s\n' "$status_output" | awk 'NF > 0 && $2 != "RUNNING" { count += 1 } END { print count + 0 }')"
        if [ "$not_running_count" -eq 0 ]; then
            printf '%s\n' "$status_output"
            return 0
        fi

        sleep 1
        elapsed=$((elapsed + 1))
    done

    echo -e "${RED}❌ Воркеры не перешли в RUNNING за ${timeout_seconds}с${NC}"
    printf '%s\n' "$status_output"
    show_worker_logs_tail
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

get_process_cwd() {
    local pid="$1"

    { lsof -a -p "$pid" -d cwd -Fn 2>/dev/null || true; } | sed -n 's/^n//p' | head -1
}

terminate_matching_processes_in_dir() {
    local name="$1"
    local pattern="$2"
    local expected_cwd="$3"
    local pids=""
    local pid=""
    local cwd=""
    local found=0

    pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
    [ -n "$pids" ] || return 0

    for pid in $pids; do
        cwd="$(get_process_cwd "$pid")"
        [ "$cwd" = "$expected_cwd" ] || continue
        if [ "$found" -eq 0 ]; then
            echo -e "${YELLOW}🔄 Найдены старые процессы $name:${NC}"
            found=1
        fi
        stop_process_by_pid "$pid" "$name"
    done
}

stop_supervisord() {
    local timeout_seconds="${1:-20}"
    local spid=""
    local supervisorctl_bin=""
    local output=""

    if [ ! -f "$SUPERVISOR_PID_FILE" ]; then
        rm -f "$SUPERVISOR_SOCK"
        return 0
    fi

    spid="$(cat "$SUPERVISOR_PID_FILE" 2>/dev/null || true)"
    if [ -z "$spid" ] || ! is_process_active "$spid"; then
        rm -f "$SUPERVISOR_PID_FILE" "$SUPERVISOR_SOCK"
        return 0
    fi

    echo -e "  Останавливаю supervisord (PID $spid)"
    if supervisorctl_bin="$(get_supervisorctl_bin 2>/dev/null)"; then
        output="$("$supervisorctl_bin" -c "$SUPERVISOR_CONF" shutdown 2>&1 || true)"
        if printf '%s\n' "$output" | grep -qi "already shutting down"; then
            echo -e "  supervisord уже останавливается"
        elif printf '%s\n' "$output" | grep -qi "shut down"; then
            echo -e "  команда остановки supervisord отправлена"
        elif [ -n "$output" ]; then
            printf '%s\n' "$output"
        fi
    else
        kill "$spid" 2>/dev/null || true
    fi

    if wait_for_process_exit "$spid" "$timeout_seconds"; then
        rm -f "$SUPERVISOR_PID_FILE" "$SUPERVISOR_SOCK"
        echo -e "  supervisord остановлен"
        return 0
    fi

    stop_process_by_pid "$spid" "supervisord" 5 || true
    rm -f "$SUPERVISOR_PID_FILE" "$SUPERVISOR_SOCK"
}

ensure_port_free() {
    local port="$1"
    local name="$2"
    local pids=""

    pids="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null || true)"
    [ -n "$pids" ] || return 0

    echo -e "${YELLOW}🔄 Порт $port занят — завершаю старые процессы перед запуском $name${NC}"
    for pid in $pids; do
        stop_process_by_pid "$pid" "$name (порт $port)"
    done

    # Проверяем, освободился ли порт
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo -e "${RED}❌ Порт $port всё ещё занят, не могу запустить $name${NC}"
        return 1
    fi
}

install_node_dependencies_if_needed() {
    local service_dir="$1"
    local log_file="$2"

    if [ -d "$service_dir/node_modules" ]; then
        return 0
    fi

    if ! command -v npm >/dev/null 2>&1; then
        echo -e "${RED}❌ npm не установлен, не могу установить зависимости для $service_dir${NC}"
        return 1
    fi

    echo -e "${BLUE}📦 Устанавливаю Node.js зависимости: $service_dir${NC}"
    if [ -f "$service_dir/package-lock.json" ]; then
        (cd "$service_dir" && npm ci --silent) > "$log_file" 2>&1
    else
        (cd "$service_dir" && npm install --silent) > "$log_file" 2>&1
    fi
}

# ==========================================
# Остановка сервисов
# ==========================================
stop_all() {
    echo -e "${YELLOW}⏹ Останавливаю сервисы...${NC}"

    stop_supervisord 20

    if [ -f "$PID_FILE" ]; then
        while read -r pid name; do
            [ "$name" != "supervisord" ] || continue
            stop_process_by_pid "$pid" "$name"
        done < "$PID_FILE"
        rm -f "$PID_FILE"
    fi

    terminate_matching_processes "Browser Agent" "$SCRIPT_DIR/services/browser-agent/dist/index.js"
    terminate_matching_processes_in_dir "Browser Agent" "node dist/index.js" "$SCRIPT_DIR/services/browser-agent"
    terminate_matching_processes "Observer Worker" "run_observer_worker.py"
    terminate_matching_processes "Disable Worker" "run_disable_worker.py"
    terminate_matching_processes "Enable Worker" "run_enable_worker.py"
    terminate_matching_processes "Enable Recommendation Worker" "run_enable_recommendation_worker.py"
    terminate_matching_processes "Enable Recommendation Worker" "apps.enable_recommendation_worker.main"
    terminate_matching_processes "Meta API Worker" "run_meta_api_worker.py"
    terminate_matching_processes "Tracker Aggregator Worker" "run_tracker_aggregator_worker.py"
    terminate_matching_processes "Telegram Poller" "run_telegram_poller.py"
    terminate_matching_processes "API" "uvicorn apps.api.main:app"
    terminate_matching_processes "Frontend" "$SCRIPT_DIR/frontend/node_modules/.bin/vite"
    terminate_matching_processes_in_dir "Frontend" "npm run dev|node .*vite" "$SCRIPT_DIR/frontend"
    terminate_matching_processes_in_dir "Mini-app" "npm run dev|node .*vite" "$SCRIPT_DIR/frontend-mini"
    terminate_matching_processes "Cloudflared" "cloudflared tunnel --url"
    # Воркеры, ранее пропущенные в stop_all (их гасил только supervisord shutdown —
    # при его зависании оставались жить, в т.ч. money-критичный cabinet_scheduler).
    terminate_matching_processes "Health Watchdog" "run_health_watchdog.py"
    terminate_matching_processes "Creator Worker" "run_creator_worker.py"
    terminate_matching_processes "Creator Recorder" "run_creator_recorder.py"
    terminate_matching_processes "Digest Scheduler" "run_digest_scheduler.py"
    terminate_matching_processes "Cabinet Scheduler" "run_cabinet_scheduler.py"
    terminate_matching_processes "Reconciler Worker" "run_reconciler_worker.py"
    terminate_matching_processes "Cleanup Worker" "run_cleanup_worker.py"
    cleanup_worker_singleton_pid_files

    echo -e "${YELLOW}⏹ Останавливаю Docker контейнеры...${NC}"
    docker compose stop 2>/dev/null || true

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
ENABLE_TUNNEL=1
case "${1:-}" in
    --down|--stop)
        stop_all
        exit 0
        ;;
    --restart)
        stop_all
        echo ""
        # Прокидываем оставшиеся флаги (напр. --no-tunnel) в свежий запуск.
        exec "$0" "${@:2}"
        ;;
    --dev)
        export DEV_MODE=1
        ;;
    --logs)
        show_logs
        exit 0
        ;;
    --tunnel)
        ENABLE_TUNNEL=1
        ;;
    --no-tunnel)
        ENABLE_TUNNEL=0
        ;;
    "")
        ;;
    *)
        echo -e "${RED}❌ Неизвестный аргумент: $1${NC}"
        echo "Использование: ./run.sh [--dev|--down|--restart|--logs|--tunnel|--no-tunnel]"
        exit 1
        ;;
esac

# Ловим Ctrl+C / SIGTERM / аварийный выход после начала запуска сервисов
CLEANUP_DONE=0
RUN_STARTED=0
do_cleanup() {
    [ "$CLEANUP_DONE" -eq 0 ] || return 0
    [ "$RUN_STARTED" -eq 1 ] || return 0
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

API_HOST="${API_HOST:-0.0.0.0}"
API_PORT="${API_PORT:-8100}"
POSTGRES_PORT="${POSTGRES_PORT:-5433}"
GRPC_PORT="${GRPC_PORT:-50051}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
MINI_PORT="${MINI_PORT:-5174}"
export GRPC_PORT

USE_SUPERVISOR=0
SUPERVISORD_BIN=""
SUPERVISORCTL_BIN=""
if SUPERVISORD_BIN="$(get_supervisord_bin 2>/dev/null)" && SUPERVISORCTL_BIN="$(get_supervisorctl_bin 2>/dev/null)"; then
    USE_SUPERVISOR=1
fi

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
stop_supervisord 20
if [ -f "$PID_FILE" ] && [ -s "$PID_FILE" ]; then
    echo -e "${YELLOW}🔄 Останавливаю предыдущие процессы...${NC}"
    while read -r pid name; do
        [ "$name" != "supervisord" ] || continue
        stop_process_by_pid "$pid" "$name"
    done < "$PID_FILE"
fi

terminate_matching_processes "Browser Agent" "$SCRIPT_DIR/services/browser-agent/dist/index.js"
terminate_matching_processes_in_dir "Browser Agent" "node dist/index.js" "$SCRIPT_DIR/services/browser-agent"
terminate_matching_processes "Observer Worker" "run_observer_worker.py"
terminate_matching_processes "Disable Worker" "run_disable_worker.py"
terminate_matching_processes "Enable Worker" "run_enable_worker.py"
terminate_matching_processes "Enable Recommendation Worker" "run_enable_recommendation_worker.py"
terminate_matching_processes "Enable Recommendation Worker" "apps.enable_recommendation_worker.main"
terminate_matching_processes "Meta API Worker" "run_meta_api_worker.py"
terminate_matching_processes "Tracker Aggregator Worker" "run_tracker_aggregator_worker.py"
terminate_matching_processes "Telegram Poller" "run_telegram_poller.py"
terminate_matching_processes "API" "uvicorn apps.api.main:app"
terminate_matching_processes "Frontend" "$SCRIPT_DIR/frontend/node_modules/.bin/vite"
terminate_matching_processes_in_dir "Frontend" "npm run dev|node .*vite" "$SCRIPT_DIR/frontend"
terminate_matching_processes_in_dir "Mini-app" "npm run dev|node .*vite" "$SCRIPT_DIR/frontend-mini"
terminate_matching_processes "Health Watchdog" "run_health_watchdog.py"
terminate_matching_processes "Creator Worker" "run_creator_worker.py"
terminate_matching_processes "Creator Recorder" "run_creator_recorder.py"
terminate_matching_processes "Digest Scheduler" "run_digest_scheduler.py"
terminate_matching_processes "Cabinet Scheduler" "run_cabinet_scheduler.py"
terminate_matching_processes "Reconciler Worker" "run_reconciler_worker.py"
terminate_matching_processes "Cleanup Worker" "run_cleanup_worker.py"
cleanup_worker_singleton_pid_files

ensure_port_free "$API_PORT" "API"
ensure_port_free "$GRPC_PORT" "Browser Agent"
ensure_port_free "$FRONTEND_PORT" "Frontend"
ensure_port_free "$MINI_PORT" "Mini-app"

RUN_STARTED=1

# Создаём директорию для логов
mkdir -p "$LOG_DIR"
> "$PID_FILE"

# ==========================================
# 1. Docker — Postgres
# ==========================================
echo -e "${BLUE}🐳 Запускаю Docker контейнеры (Postgres)...${NC}"
if ! docker compose up -d; then
    echo -e "${RED}❌ Docker Compose не смог запустить контейнеры${NC}"
    exit 1
fi

# Ждём готовности Postgres
echo -e "${BLUE}⏳ Жду готовности Postgres...${NC}"
if wait_for_postgres_ready 45; then
    echo -e "${GREEN}✅ Postgres готов${NC}"
else
    exit 1
fi

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
    PIP_INSTALL_LOG="$LOG_DIR/pip_install.log"
    if ! .venv/bin/pip install -q -e '.[dev]' > "$PIP_INSTALL_LOG" 2>&1; then
        echo -e "${RED}❌ Ошибка установки Python-зависимостей${NC}"
        tail -20 "$PIP_INSTALL_LOG" || true
        exit 1
    fi
    tail -3 "$PIP_INSTALL_LOG" || true
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
    echo -e "${BLUE}🚀 Запускаю API (порт $API_PORT, dev mode)...${NC}"
else
    echo -e "${BLUE}🚀 Запускаю API (порт $API_PORT)...${NC}"
fi
# shellcheck disable=SC2086
.venv/bin/uvicorn apps.api.main:app \
    --host "$API_HOST" --port "$API_PORT" $UVICORN_EXTRA_ARGS \
    > "$LOG_DIR/api.log" 2>&1 &
API_PID=$!
append_pid "$API_PID" "api"
echo -e "${GREEN}  API PID: $API_PID${NC}"

# Ждём готовности API через /healthz (k8s liveness; /health не существует — был баг)
echo -e "${BLUE}⏳ Жду готовности API...${NC}"
for i in $(seq 1 20); do
    if curl -sf "http://localhost:$API_PORT/healthz" >/dev/null 2>&1; then
        echo -e "${GREEN}✅ API отвечает на /healthz${NC}"
        break
    fi
    if ! is_process_active "$API_PID"; then
        echo -e "${RED}❌ API процесс завершился при запуске${NC}"
        tail -20 "$LOG_DIR/api.log" || true
        exit 1
    fi
    if [ "$i" -eq 20 ]; then
        echo -e "${YELLOW}⚠️  API не ответил на /healthz за 20с, продолжаю запуск${NC}"
    fi
    sleep 1
done

# CDP-порт Vision проверяется ПОЗЖЕ — через verify_vision_cdp после старта
# browser-agent (ensure-cdp ходит к нему по gRPC; до старта agent'а проверка
# была бессмысленна и роняла ложный warning).

# ==========================================
# 5. Сборка и запуск Browser Agent (Node.js gRPC сервис)
# ==========================================
echo -e "${BLUE}🌐 Запускаю Browser Agent (Node.js gRPC)...${NC}"
if [ -d services/browser-agent ]; then
    if ! install_node_dependencies_if_needed "$SCRIPT_DIR/services/browser-agent" "$LOG_DIR/browser_agent_npm_install.log"; then
        tail -20 "$LOG_DIR/browser_agent_npm_install.log" || true
        exit 1
    fi

    echo -e "${BLUE}⏳ Собираю Browser Agent...${NC}"
    if ! (cd "$SCRIPT_DIR/services/browser-agent" && npm run build) > "$LOG_DIR/browser_agent_build.log" 2>&1; then
        echo -e "${RED}❌ Ошибка сборки Browser Agent${NC}"
        tail -20 "$LOG_DIR/browser_agent_build.log" || true
        exit 1
    fi

    echo -e "${GREEN}✅ Browser Agent собран${NC}"
    if [ "$USE_SUPERVISOR" -eq 1 ]; then
        BROWSER_AGENT_PID=""
        echo -e "${BLUE}🛡 Browser Agent будет запущен через supervisord с автоперезапуском${NC}"
    else
        GRPC_PORT="$GRPC_PORT" node "$SCRIPT_DIR/services/browser-agent/dist/index.js" > "$LOG_DIR/browser_agent.log" 2>&1 &
        BROWSER_AGENT_PID=$!
        append_pid "$BROWSER_AGENT_PID" "browser_agent"
        echo -e "${GREEN}  Browser Agent PID: $BROWSER_AGENT_PID${NC}"

        # Ждём готовности gRPC
        echo -e "${BLUE}⏳ Жду готовности Browser Agent...${NC}"
        for i in $(seq 1 10); do
            if nc -z localhost "$GRPC_PORT" 2>/dev/null; then
                echo -e "${GREEN}✅ Browser Agent отвечает на порту $GRPC_PORT${NC}"
                break
            fi
            if ! is_process_active "$BROWSER_AGENT_PID"; then
                echo -e "${RED}❌ Browser Agent процесс завершился при запуске${NC}"
                tail -20 "$LOG_DIR/browser_agent.log" || true
                exit 1
            fi
            if [ "$i" -eq 10 ]; then
                echo -e "${YELLOW}⚠️  Browser Agent не ответил за 10с, продолжаю${NC}"
            fi
            sleep 1
        done
    fi
else
    echo -e "${YELLOW}⚠️  services/browser-agent не найден, пропускаю${NC}"
    BROWSER_AGENT_PID=""
fi

# ==========================================
# 6–9. Запуск воркеров (через supervisord или напрямую)
# ==========================================
if [ "$USE_SUPERVISOR" -eq 1 ]; then
    echo -e "${BLUE}🛡 Запускаю browser-agent и воркеры через supervisord (автоперезапуск включён)...${NC}"

    # Останавливаем предыдущий supervisord этого проекта если запущен
    stop_supervisord 20

    "$SUPERVISORD_BIN" -c "$SUPERVISOR_CONF"
    SUPERVISORD_PID="$(cat "$SUPERVISOR_PID_FILE" 2>/dev/null || true)"
    if [ -n "$SUPERVISORD_PID" ]; then
        append_pid "$SUPERVISORD_PID" "supervisord"
        echo -e "${GREEN}  supervisord PID: $SUPERVISORD_PID${NC}"
    fi

    echo -e "${BLUE}⏳ Жду готовности воркеров supervisord...${NC}"
    if wait_for_supervisor_running "$SUPERVISORCTL_BIN" 35; then
        echo -e "${GREEN}  Browser Agent и воркеры запущены под supervisord${NC}"
    else
        exit 1
    fi

    echo -e "${BLUE}⏳ Жду готовности Browser Agent...${NC}"
    for i in $(seq 1 15); do
        if nc -z localhost "$GRPC_PORT" 2>/dev/null; then
            echo -e "${GREEN}✅ Browser Agent отвечает на порту $GRPC_PORT${NC}"
            break
        fi
        if [ "$i" -eq 15 ]; then
            echo -e "${RED}❌ Browser Agent не ответил за 15с${NC}"
            "$SUPERVISORCTL_BIN" -c "$SUPERVISOR_CONF" status || true
            tail -20 "$LOG_DIR/browser_agent.log" || true
            exit 1
        fi
        sleep 1
    done

    # Заглушки PID для проверки boot ниже — используем supervisorctl
    OBSERVER_PID=""
    DISABLE_PID=""
    ENABLE_PID=""
    ENABLE_RECO_PID=""
    TG_PID=""
else
    echo -e "${YELLOW}⚠️  supervisord не найден — воркеры запускаются без автоперезапуска${NC}"
    echo -e "${YELLOW}   Установи: pip install supervisor  или  brew install supervisor${NC}"

    # ==========================================
    # 6. Запуск Observer Worker
    # ==========================================
    echo -e "${BLUE}🔍 Запускаю Observer Worker...${NC}"
    .venv/bin/python run_observer_worker.py > "$LOG_DIR/observer.log" 2>&1 &
    OBSERVER_PID=$!
    append_pid "$OBSERVER_PID" "observer"
    echo -e "${GREEN}  Observer PID: $OBSERVER_PID${NC}"

    # ==========================================
    # 7. Запуск Disable Worker
    # ==========================================
    echo -e "${BLUE}🔴 Запускаю Disable Worker...${NC}"
    .venv/bin/python run_disable_worker.py > "$LOG_DIR/disable_worker.log" 2>&1 &
    DISABLE_PID=$!
    append_pid "$DISABLE_PID" "disable_worker"
    echo -e "${GREEN}  Disable Worker PID: $DISABLE_PID${NC}"

    # ==========================================
    # 8. Запуск Enable Worker
    # ==========================================
    echo -e "${BLUE}🟢 Запускаю Enable Worker...${NC}"
    .venv/bin/python run_enable_worker.py > "$LOG_DIR/enable_worker.log" 2>&1 &
    ENABLE_PID=$!
    append_pid "$ENABLE_PID" "enable_worker"
    echo -e "${GREEN}  Enable Worker PID: $ENABLE_PID${NC}"

    # ==========================================
    # 9. Запуск Cleanup + Reconciler воркеров
    # ==========================================
    echo -e "${BLUE}🧹 Запускаю Cleanup Worker...${NC}"
    .venv/bin/python run_cleanup_worker.py > "$LOG_DIR/cleanup_worker.log" 2>&1 &
    CLEANUP_PID=$!
    append_pid "$CLEANUP_PID" "cleanup_worker"
    echo -e "${GREEN}  Cleanup Worker PID: $CLEANUP_PID${NC}"

    echo -e "${BLUE}🔁 Запускаю Reconciler Worker...${NC}"
    .venv/bin/python run_reconciler_worker.py > "$LOG_DIR/reconciler_worker.log" 2>&1 &
    RECONCILER_PID=$!
    append_pid "$RECONCILER_PID" "reconciler_worker"
    echo -e "${GREEN}  Reconciler Worker PID: $RECONCILER_PID${NC}"

    echo -e "${BLUE}📊 Запускаю Tracker Aggregator Worker...${NC}"
    .venv/bin/python run_tracker_aggregator_worker.py > "$LOG_DIR/tracker_aggregator_worker.log" 2>&1 &
    TRACKER_AGG_PID=$!
    append_pid "$TRACKER_AGG_PID" "tracker_aggregator_worker"
    echo -e "${GREEN}  Tracker Aggregator Worker PID: $TRACKER_AGG_PID${NC}"

    # ==========================================
    # 10. Запуск Telegram Poller
    # ==========================================
    echo -e "${BLUE}🤖 Запускаю Telegram Poller...${NC}"
    .venv/bin/python run_telegram_poller.py > "$LOG_DIR/telegram.log" 2>&1 &
    TG_PID=$!
    append_pid "$TG_PID" "telegram"
    echo -e "${GREEN}  Telegram PID: $TG_PID${NC}"

    # Meta API Worker — нужен для act_via_api=true (авто-стоп через Marketing API).
    echo -e "${BLUE}🛰️  Запускаю Meta API Worker...${NC}"
    .venv/bin/python run_meta_api_worker.py > "$LOG_DIR/meta_api_worker.log" 2>&1 &
    META_API_PID=$!
    append_pid "$META_API_PID" "meta_api_worker"
    echo -e "${GREEN}  Meta API Worker PID: $META_API_PID${NC}"

    echo -e "${BLUE}♻️  Запускаю Enable Recommendation Worker...${NC}"
    .venv/bin/python run_enable_recommendation_worker.py > "$LOG_DIR/enable_recommendation_worker.log" 2>&1 &
    ENABLE_RECO_PID=$!
    append_pid "$ENABLE_RECO_PID" "enable_recommendation_worker"
    echo -e "${GREEN}  Enable Recommendation Worker PID: $ENABLE_RECO_PID${NC}"

    # Воркеры, ранее запускавшиеся ТОЛЬКО под supervisord. Без них foreground-набор
    # был неполным: cabinet_scheduler (автостарт) и health_watchdog (мониторинг) молча
    # не работали. Запускаем все — но БЕЗ autorestart (его даёт только supervisord).
    echo -e "${YELLOW}⚠️  Foreground без autorestart: при падении воркер не перезапустится.${NC}"
    echo -e "${YELLOW}   Для прода поставь supervisord: pip install supervisor${NC}"

    echo -e "${BLUE}📅 Запускаю Cabinet Scheduler (money-критичный автостарт)...${NC}"
    .venv/bin/python run_cabinet_scheduler.py > "$LOG_DIR/cabinet_scheduler.log" 2>&1 &
    append_pid "$!" "cabinet_scheduler"

    echo -e "${BLUE}🩺 Запускаю Health Watchdog...${NC}"
    .venv/bin/python run_health_watchdog.py > "$LOG_DIR/health_watchdog.log" 2>&1 &
    append_pid "$!" "health_watchdog"

    echo -e "${BLUE}📨 Запускаю Digest Scheduler...${NC}"
    .venv/bin/python run_digest_scheduler.py > "$LOG_DIR/digest_scheduler.log" 2>&1 &
    append_pid "$!" "digest_scheduler"

    echo -e "${BLUE}🎬 Запускаю Creator Worker...${NC}"
    .venv/bin/python run_creator_worker.py > "$LOG_DIR/creator_worker.log" 2>&1 &
    append_pid "$!" "creator_worker"

    echo -e "${BLUE}⏺  Запускаю Creator Recorder...${NC}"
    .venv/bin/python run_creator_recorder.py > "$LOG_DIR/creator_recorder.log" 2>&1 &
    append_pid "$!" "creator_recorder"
fi

# ==========================================
# 10. Запуск Frontend (Vite)
# ==========================================
if [ -d frontend ]; then
    echo -e "${BLUE}🎨 Запускаю Frontend (Vite, порт $FRONTEND_PORT)...${NC}"
    terminate_matching_processes "Frontend" "$SCRIPT_DIR/frontend/node_modules/.bin/vite"
    terminate_matching_processes_in_dir "Frontend" "npm run dev|node .*vite" "$SCRIPT_DIR/frontend"

    if ! install_node_dependencies_if_needed "$SCRIPT_DIR/frontend" "$LOG_DIR/frontend_npm_install.log"; then
        tail -20 "$LOG_DIR/frontend_npm_install.log" || true
        exit 1
    fi

    FRONTEND_URL="http://localhost:$FRONTEND_PORT"
    if [ "$DEV_MODE" -eq 1 ]; then
        # Dev-режим (--dev): Vite с HMR. Медленнее загрузка/отклик (ESM-водопад,
        # React dev + StrictMode double-render), но горячая перезагрузка для правок фронта.
        echo -e "${BLUE}  Frontend: dev-режим (HMR)${NC}"
        (
            cd "$SCRIPT_DIR/frontend"
            VITE_API_KEY="${API_KEY:-}" npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
        ) > "$LOG_DIR/frontend.log" 2>&1 &
        FRONTEND_PID=$!
    else
        # Прод-режим (по умолчанию): минифицированный build + vite preview. В разы быстрее
        # dev (нет ESM-водопада, React production, без double-render). Для правок фронта
        # с HMR — ./run.sh --dev. Сборку делаем синхронно, чтобы поймать ошибки до preview.
        echo -e "${BLUE}  Frontend: собираю prod build...${NC}"
        if ! (cd "$SCRIPT_DIR/frontend" && VITE_API_KEY="${API_KEY:-}" npm run build) \
            > "$LOG_DIR/frontend_build.log" 2>&1; then
            echo -e "${RED}❌ Сборка фронта упала${NC}"
            tail -20 "$LOG_DIR/frontend_build.log" || true
            exit 1
        fi
        echo -e "${BLUE}  Frontend: prod build готов, запускаю vite preview${NC}"
        (
            cd "$SCRIPT_DIR/frontend"
            VITE_API_KEY="${API_KEY:-}" npm run preview -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
        ) > "$LOG_DIR/frontend.log" 2>&1 &
        FRONTEND_PID=$!
    fi
    append_pid "$FRONTEND_PID" "frontend"
    echo -e "${GREEN}  Frontend PID: $FRONTEND_PID${NC}"

    # Ждём готовности Vite на фиксированном порту.
    for i in $(seq 1 15); do
        if nc -z "$FRONTEND_HOST" "$FRONTEND_PORT" 2>/dev/null; then
            echo -e "${GREEN}✅ Frontend отвечает на порту $FRONTEND_PORT${NC}"
            break
        fi
        if ! is_process_active "$FRONTEND_PID"; then
            echo -e "${RED}❌ Frontend процесс завершился при запуске${NC}"
            tail -20 "$LOG_DIR/frontend.log" || true
            exit 1
        fi
        if [ "$i" -eq 15 ]; then
            echo -e "${YELLOW}⚠️  Frontend не ответил за 15с, продолжаю запуск${NC}"
        fi
        sleep 1
    done
else
    FRONTEND_URL="не запущен"
fi

# ==========================================
# 11. Запуск Mini-app (Vite, frontend-mini)
# ==========================================
if [ -d frontend-mini ]; then
    echo -e "${BLUE}📱 Запускаю Mini-app (Vite, порт $MINI_PORT)...${NC}"
    terminate_matching_processes_in_dir "Mini-app" "npm run dev|node .*vite" "$SCRIPT_DIR/frontend-mini"

    if ! install_node_dependencies_if_needed "$SCRIPT_DIR/frontend-mini" "$LOG_DIR/frontend_mini_npm_install.log"; then
        tail -20 "$LOG_DIR/frontend_mini_npm_install.log" || true
        exit 1
    fi

    (
        cd "$SCRIPT_DIR/frontend-mini"
        VITE_API_KEY="${API_KEY:-}" npm run dev -- --host "$FRONTEND_HOST" --port "$MINI_PORT" --strictPort
    ) > "$LOG_DIR/frontend_mini.log" 2>&1 &
    MINI_PID=$!
    append_pid "$MINI_PID" "frontend_mini"
    echo -e "${GREEN}  Mini-app PID: $MINI_PID${NC}"

    # Ждём готовности Vite
    for i in $(seq 1 15); do
        if nc -z "$FRONTEND_HOST" "$MINI_PORT" 2>/dev/null; then
            echo -e "${GREEN}✅ Mini-app отвечает на порту $MINI_PORT${NC}"
            break
        fi
        if ! is_process_active "$MINI_PID"; then
            echo -e "${RED}❌ Mini-app процесс завершился при запуске${NC}"
            tail -20 "$LOG_DIR/frontend_mini.log" || true
            exit 1
        fi
        if [ "$i" -eq 15 ]; then
            echo -e "${YELLOW}⚠️  Mini-app не ответил за 15с, продолжаю запуск${NC}"
        fi
        sleep 1
    done
else
    echo -e "${YELLOW}⚠️  frontend-mini не найден, пропускаю${NC}"
    MINI_PID=""
fi

# ==========================================
# 12. Cloudflared quick-tunnels (если --tunnel)
# ==========================================
# Функция запуска одного туннеля; возвращает URL через stdout переменной
start_tunnel() {
    local name="$1"
    local port="$2"
    local log_file="$LOG_DIR/cloudflared_${name}.log"

    cloudflared tunnel --url "http://localhost:$port" --no-autoupdate \
        > "$log_file" 2>&1 &
    local tpid=$!
    append_pid "$tpid" "cloudflared_${name}"

    # Ждём появления URL в логе до 20 секунд (cloudflared иногда стартует медленно)
    local found=""
    for _ in {1..20}; do
        found=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$log_file" 2>/dev/null | head -1 || true)
        if [[ -n "$found" ]]; then break; fi
        sleep 1
    done
    echo "$found"
}

API_TUNNEL_URL=""
WEB_TUNNEL_URL=""
MINI_TUNNEL_URL=""
MINI_WEB_APP_URL=""

auto_register_web_app_url() {
    local url="$1"
    if [[ -z "$url" ]]; then return; fi
    echo "[run.sh] Жду готовности API на http://localhost:${API_PORT}/healthz ..."
    for i in {1..30}; do
        if curl -s -f -m 1 "http://localhost:${API_PORT}/healthz" >/dev/null 2>&1; then break; fi
        sleep 1
    done
    local headers=(-H "Content-Type: application/json")
    if [[ -n "${API_KEY:-}" ]]; then headers+=(-H "X-API-Key: ${API_KEY}"); fi
    if curl -s -X PUT "http://localhost:${API_PORT}/api/settings/telegram/web-app-url" \
         "${headers[@]}" -d "{\"web_app_url\": \"${url}\"}" -o /dev/null -w "%{http_code}" \
         | grep -q "^200$"; then
        echo -e "${GREEN}✅ web_app_url прописан в БД: ${url}${NC}"
    else
        echo -e "${YELLOW}⚠️  Не удалось прописать web_app_url через API. Введите его вручную в Settings.${NC}"
    fi
}

# Проверка/поднятие CDP-порта Vision через API (ensure-cdp ходит к browser-agent
# по gRPC, поэтому вызывается ПОСЛЕ его старта). Эндпоинт graceful: всегда 200 с
# {ok,status,action,message}, не роняет запуск.
verify_vision_cdp() {
    echo -e "${BLUE}🧭 Проверяю CDP-порт Vision...${NC}"
    local body status
    body="$(mktemp)"
    if [ -n "${API_KEY:-}" ]; then
        status="$(curl -sS -m 90 -w "%{http_code}" -o "$body" -X POST \
            -H "X-API-Key: $API_KEY" \
            "http://localhost:$API_PORT/api/vision/ensure-cdp" || true)"
    else
        status="$(curl -sS -m 90 -w "%{http_code}" -o "$body" -X POST \
            "http://localhost:$API_PORT/api/vision/ensure-cdp" || true)"
    fi
    if [ "$status" = "200" ]; then
        local summary ok state action message
        summary="$(.venv/bin/python - "$body" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
prefix = "ok" if data.get("ok", True) else "warn"
status = data.get("status") or "UNKNOWN"
action = data.get("action") or "none"
message = data.get("message") or "Без сообщения"
print(f"{prefix}|{status}|{action}|{message}")
PY
)"
        IFS='|' read -r ok state action message <<< "$summary"
        if [ "$ok" = "ok" ]; then
            echo -e "${GREEN}✅ Vision CDP: $state ($action) — $message${NC}"
        else
            echo -e "${YELLOW}⚠️  Vision CDP: $state ($action) — $message${NC}"
        fi
    else
        echo -e "${YELLOW}⚠️  Не удалось проверить CDP-порт Vision (HTTP ${status:-нет ответа})${NC}"
        tail -5 "$body" 2>/dev/null || true
    fi
    rm -f "$body"
}

if [ "$ENABLE_TUNNEL" -eq 1 ]; then
    if command -v cloudflared >/dev/null 2>&1; then
        echo -e "${BLUE}🚇 Поднимаю cloudflared туннели...${NC}"
        API_TUNNEL_URL="$(start_tunnel "api" "$API_PORT")"
        WEB_TUNNEL_URL="$(start_tunnel "web" "$FRONTEND_PORT")"
        MINI_TUNNEL_URL="$(start_tunnel "mini" "$MINI_PORT")"
        echo -e "${GREEN}✅ Туннели запущены${NC}"
        if [[ -n "$MINI_TUNNEL_URL" ]]; then
            MINI_WEB_APP_URL="${MINI_TUNNEL_URL%/}/tma/"
            auto_register_web_app_url "$MINI_WEB_APP_URL"
        fi
    else
        echo -e "${YELLOW}⚠️  cloudflared не найден, туннели не будут подняты${NC}"
    fi
fi
# CDP Vision проверяем здесь — browser-agent уже поднят (ensure-cdp ходит к нему по gRPC).
verify_vision_cdp

sleep 2
BOOT_OK=1
check_process_started "$API_PID" "API" "$LOG_DIR/api.log" || BOOT_OK=0
if [ -n "${BROWSER_AGENT_PID:-}" ]; then
    check_process_started "$BROWSER_AGENT_PID" "Browser Agent" "$LOG_DIR/browser_agent.log" || BOOT_OK=0
fi
if [ "$USE_SUPERVISOR" -eq 0 ]; then
    check_process_started "$OBSERVER_PID" "Observer Worker" "$LOG_DIR/observer.log" || BOOT_OK=0
    check_process_started "$DISABLE_PID" "Disable Worker" "$LOG_DIR/disable_worker.log" || BOOT_OK=0
    check_process_started "$ENABLE_PID" "Enable Worker" "$LOG_DIR/enable_worker.log" || BOOT_OK=0
    if [ -n "${ENABLE_RECO_PID:-}" ]; then
        check_process_started "$ENABLE_RECO_PID" "Enable Recommendation Worker" "$LOG_DIR/enable_recommendation_worker.log" || BOOT_OK=0
    fi
    check_process_started "$TG_PID" "Telegram Poller" "$LOG_DIR/telegram.log" || BOOT_OK=0
fi
if [ -n "${FRONTEND_PID:-}" ]; then
    check_process_started "$FRONTEND_PID" "Frontend" "$LOG_DIR/frontend.log" || BOOT_OK=0
fi
if [ -n "${MINI_PID:-}" ]; then
    check_process_started "$MINI_PID" "Mini-app" "$LOG_DIR/frontend_mini.log" || BOOT_OK=0
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
    append_pid "$CAFFEINATE_PID" "caffeinate"
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
echo -e "  🌐 API:       ${BLUE}http://localhost:$API_PORT/docs${NC}"
echo -e "  📊 Dashboard: ${BLUE}${FRONTEND_URL}${NC}"
echo -e "  📱 Mini-app:  ${BLUE}http://localhost:$MINI_PORT/tma/${NC}"
echo -e "  🗄️ Postgres:  localhost:$POSTGRES_PORT"
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
if [ "$ENABLE_TUNNEL" -eq 1 ] && { [ -n "$API_TUNNEL_URL" ] || [ -n "$WEB_TUNNEL_URL" ] || [ -n "$MINI_TUNNEL_URL" ]; }; then
    echo ""
    echo -e "${BLUE}🚇 Туннели:${NC}"
    echo -e "  API:      ${GREEN}${API_TUNNEL_URL:-не определён}${NC}"
    echo -e "  Web UI:   ${GREEN}${WEB_TUNNEL_URL:-не определён}${NC}"
    echo -e "  Mini-app: ${GREEN}${MINI_TUNNEL_URL:-не определён}${NC}  ← для BotFather → Bot Settings → Menu Button"
    if [[ -n "${MINI_WEB_APP_URL:-}" ]]; then
        echo -e "  Mini App URL (для inline-кнопок в алертах): ${GREEN}${MINI_WEB_APP_URL}${NC}"
    fi
fi
echo ""

# Ждём завершения — Ctrl+C останавливает всё
echo -e "${BLUE}Нажмите Ctrl+C для остановки всех сервисов${NC}"
wait
