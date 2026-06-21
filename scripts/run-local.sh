#!/usr/bin/env bash
# ============================================================================
# Локальный DEV-профиль FB Stop Bot — изолирован от боевого сервера.
#
# Поднимает ТОЛЬКО безопасный набор: postgres + redis + api + telegram_poller
# (под ТЕСТОВЫМ ботом). НЕ запускает observer (сканирование), meta_api (мутации),
# cabinet_scheduler (автостарт), Vision/browser-agent — никаких действий с боевой
# рекламой физически невозможно.
#
# Перед запуском: cp .env.local.example .env  → заполни тестовый TELEGRAM_BOT_TOKEN
# и свой ENCRYPTION_KEY. Запуск: ./scripts/run-local.sh   Остановка: ./scripts/run-local.sh --down
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=".env"

# --- Защита: не запускать на боевом конфиге ---
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ОШИБКА: нет $ENV_FILE. Скопируй: cp .env.local.example .env и заполни." >&2
  exit 1
fi
if ! grep -qE '^FB_AGENT_PROFILE=local' "$ENV_FILE"; then
  echo "ОШИБКА: в $ENV_FILE нет 'FB_AGENT_PROFILE=local'." >&2
  echo "Это защита от запуска dev-стека на боевом .env. Используй .env.local.example." >&2
  exit 1
fi

# --- Защита: тестовый токен не должен совпасть с боевым @AdGuard_FB_Bot ---
# (боевой держит getUpdates на сервере; локальный poller с тем же токеном = 409 Conflict).
# Здесь проверить точное совпадение нельзя (боевой токен неизвестен локально), но
# напоминаем явно.
echo "ℹ Локальный профиль: telegram_poller под ТЕСТОВЫМ ботом (НЕ боевой токен!)."

COMPOSE="docker compose"
LOCAL_SERVICES="api telegram_poller"

if [[ "${1:-}" == "--down" ]]; then
  $COMPOSE down
  echo "Локальный стек остановлен."
  exit 0
fi
if [[ "${1:-}" == "--logs" ]]; then
  $COMPOSE logs -f $LOCAL_SERVICES
  exit 0
fi

echo "=== [1/3] Инфраструктура (postgres + redis) ==="
$COMPOSE up -d postgres redis

echo "=== [2/3] Миграции БД ==="
$COMPOSE run --rm migrate

echo "=== [3/3] Безопасный набор: $LOCAL_SERVICES ==="
$COMPOSE up -d $LOCAL_SERVICES

echo ""
echo "✅ Локальный dev-стек поднят."
echo "   API:            http://localhost:8100  (/healthz, /readyz)"
echo "   Запущено:       postgres, redis, api, telegram_poller (тестовый бот)"
echo "   НЕ запущено:    observer/сканирование, meta_api/мутации, cabinet_scheduler,"
echo "                   Vision/browser-agent — боевые действия с рекламой невозможны."
echo "   Сканирование:   выключено (is_scanning_enabled=false по умолчанию)."
echo ""
echo "   Логи:  ./scripts/run-local.sh --logs      Стоп:  ./scripts/run-local.sh --down"
