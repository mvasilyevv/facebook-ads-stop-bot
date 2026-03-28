#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Единый скрипт запуска FB Stop Bot
# Использование:
#   ./run.sh          — запуск всех сервисов
#   ./run.sh --down   — остановка всех сервисов
#   ./run.sh --logs   — логи всех процессов

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

# ==========================================
# Остановка сервисов
# ==========================================
stop_all() {
    echo -e "${YELLOW}⏹ Останавливаю сервисы...${NC}"

    if [ -f "$PID_FILE" ]; then
        while read -r pid name; do
            if kill -0 "$pid" 2>/dev/null; then
                echo -e "  Останавливаю $name (PID $pid)"
                kill "$pid" 2>/dev/null || true
            fi
        done < "$PID_FILE"
        rm -f "$PID_FILE"
    fi

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
    --logs)
        show_logs
        exit 0
        ;;
esac

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

# Останавливаем старые процессы если они запущены
if [ -f "$PID_FILE" ] && [ -s "$PID_FILE" ]; then
    echo -e "${YELLOW}🔄 Останавливаю предыдущие процессы...${NC}"
    while read -r pid name; do
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            echo -e "  Остановлен $name (PID $pid)"
        fi
    done < "$PID_FILE"
    sleep 2
fi

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
for i in $(seq 1 30); do
    if docker compose exec -T postgres pg_isready -U fb_stop_bot_v2 -d fb_stop_bot_v2 &>/dev/null; then
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

echo -e "${BLUE}📦 Устанавливаю зависимости...${NC}"
.venv/bin/pip install -q -e '.[dev]' 2>&1 | tail -3

# ==========================================
# 3. Миграции БД
# ==========================================
echo -e "${BLUE}🗄️ Применяю миграции БД...${NC}"
.venv/bin/python -m alembic upgrade head 2>&1 || {
    echo -e "${YELLOW}⚠️  Alembic миграции не найдены, создаю таблицы напрямую${NC}"
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
}

# ==========================================
# 4. Запуск API
# ==========================================
echo -e "${BLUE}🚀 Запускаю API (порт 8100)...${NC}"
.venv/bin/uvicorn apps.api.main:app \
    --host 0.0.0.0 --port 8100 \
    > "$LOG_DIR/api.log" 2>&1 &
API_PID=$!
echo "$API_PID api" >> "$PID_FILE"
echo -e "${GREEN}  API PID: $API_PID${NC}"

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
# 8. Запуск Telegram Poller
# ==========================================
echo -e "${BLUE}🤖 Запускаю Telegram Poller...${NC}"
.venv/bin/python -m apps.telegram_poller.main > "$LOG_DIR/telegram.log" 2>&1 &
TG_PID=$!
echo "$TG_PID telegram" >> "$PID_FILE"
echo -e "${GREEN}  Telegram PID: $TG_PID${NC}"

# ==========================================
# 9. Запуск Frontend (Vite)
# ==========================================
if [ -d frontend ]; then
    echo -e "${BLUE}🎨 Запускаю Frontend (Vite)...${NC}"
    cd frontend
    if [ ! -d node_modules ]; then
        npm install --silent 2>&1 | tail -3
    fi
    npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
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
# 8. Caffeinate — запрет сна ноутбука
# ==========================================
caffeinate -i -d &
CAFFEINATE_PID=$!
echo "$CAFFEINATE_PID caffeinate" >> "$PID_FILE"

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
echo -e "${YELLOW}☕ Сон ноутбука заблокирован (caffeinate)${NC}"
echo ""

# Ждём завершения — Ctrl+C останавливает всё
trap 'stop_all; exit 0' INT TERM
echo -e "${BLUE}Нажмите Ctrl+C для остановки всех сервисов${NC}"
wait
