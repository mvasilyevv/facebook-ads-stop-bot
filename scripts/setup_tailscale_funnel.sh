#!/usr/bin/env bash
# -*- coding: utf-8 -*-
# Разовая настройка стабильного Tailscale Funnel для Telegram Mini App.
#
# Публикует локальный mini-app (vite, :5174) на ПОСТОЯННЫЙ публичный HTTPS-адрес
# https://<host>.<tailnet>.ts.net и регистрирует его в боте (web_app_url + авто
# Menu Button). URL больше не меняется при перезапусках run.sh — BotFather Menu
# Button настраивается ОДИН раз.
#
# Предусловия (ручные, разово):
#   1. tailscale up                          — логин (откроется браузер)
#   2. Admin console (login.tailscale.com/admin):
#        - DNS → включить MagicDNS + HTTPS Certificates
#        - Access Controls → разрешить Funnel для этой машины (nodeAttrs "funnel")
#
# Использование: ./scripts/setup_tailscale_funnel.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

# Подхватываем порты/API_KEY из .env (если есть).
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi
MINI_PORT="${MINI_PORT:-5174}"
API_PORT="${API_PORT:-8100}"

echo -e "${BLUE}🔗 Настройка Tailscale Funnel для Mini App (порт ${MINI_PORT})${NC}"

# 1. tailscale установлен
if ! command -v tailscale >/dev/null 2>&1; then
    echo -e "${RED}❌ tailscale не установлен. brew install tailscale (или приложение Tailscale).${NC}"
    exit 1
fi

# 2. залогинен
if ! tailscale status >/dev/null 2>&1; then
    echo -e "${RED}❌ Tailscale разлогинен.${NC}"
    echo -e "   Выполни: ${YELLOW}tailscale up${NC}  (откроется браузер для логина), затем перезапусти скрипт."
    exit 1
fi

# 3. DNSName машины (стабильный hostname в tailnet)
DNS="$(tailscale status --json 2>/dev/null \
    | python3 -c "import sys,json;print((json.load(sys.stdin).get('Self',{}).get('DNSName','') or '').rstrip('.'))" \
    2>/dev/null || true)"
if [ -z "$DNS" ]; then
    echo -e "${RED}❌ Не удалось получить DNSName. Включи MagicDNS в admin console → DNS.${NC}"
    exit 1
fi
URL="https://${DNS}"
WEB_APP_URL="${URL}/tma/"

# 4. Включаем Funnel на mini-порт (фоновый, переживает рестарты run.sh)
echo -e "${BLUE}⏳ Включаю Funnel :${MINI_PORT} → ${URL} ...${NC}"
ERR_LOG="$(mktemp)"
if ! tailscale funnel --bg "${MINI_PORT}" >"$ERR_LOG" 2>&1; then
    echo -e "${RED}❌ Funnel не включился:${NC}"
    cat "$ERR_LOG"
    echo ""
    echo -e "${YELLOW}Частые причины:${NC}"
    echo "   - Funnel не разрешён в ACL: admin → Access Controls → добавь nodeAttrs с \"funnel\" для этой машины."
    echo "   - HTTPS-сертификаты выключены: admin → DNS → включи HTTPS Certificates."
    echo "   - Подробнее: https://tailscale.com/kb/1223/funnel"
    rm -f "$ERR_LOG"
    exit 1
fi
rm -f "$ERR_LOG"
echo -e "${GREEN}✅ Funnel включён (фоновый, в tailscaled — переживает перезапуски run.sh)${NC}"

# 5. Регистрируем web_app_url в боте → авто-установка Menu Button (см. PUT /web-app-url)
echo -e "${BLUE}⏳ Регистрирую web_app_url в боте ...${NC}"
HEADERS=(-H "Content-Type: application/json")
if [ -n "${API_KEY:-}" ]; then
    HEADERS+=(-H "X-API-Key: ${API_KEY}")
fi
if curl -fsS -X PUT "http://localhost:${API_PORT}/api/settings/telegram/web-app-url" \
    "${HEADERS[@]}" -d "{\"web_app_url\":\"${WEB_APP_URL}\"}" >/dev/null 2>&1; then
    echo -e "${GREEN}✅ web_app_url зарегистрирован, Menu Button бота обновлён автоматически${NC}"
else
    echo -e "${YELLOW}⚠️  Не удалось зарегистрировать через API (запущен ли API на :${API_PORT}?).${NC}"
    echo -e "   Пропиши URL вручную в Settings или повтори после ./run.sh."
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ Стабильный URL Mini App:${NC}"
echo -e "     ${BLUE}${WEB_APP_URL}${NC}"
echo ""
echo -e "  BotFather → твой бот → Bot Settings → Menu Button → вставь URL ${YELLOW}(один раз)${NC}."
echo -e "  URL ПОСТОЯННЫЙ — больше не меняется при перезапусках run.sh."
echo -e "${GREEN}════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "Проверить: ${YELLOW}tailscale funnel status${NC}   ·   Сбросить: ${YELLOW}tailscale funnel reset${NC}"
