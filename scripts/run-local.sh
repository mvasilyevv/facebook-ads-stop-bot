#!/usr/bin/env bash
# ============================================================================
# Локальный DEV-профиль FB Agent — изолирован от боевого сервера.
#
# Поднимает ТОЛЬКО безопасный набор: postgres + api + Telegram outbox workers
# и опциональный Redis для несafety-функций
# (под ТЕСТОВЫМ ботом). Webhook требует отдельный HTTPS tunnel/configurator.
# НЕ запускает observer (сканирование), meta_api (мутации),
# Vision/browser-agent — никаких действий с боевой
# рекламой физически невозможно.
#
# Перед запуском: cp .env.local.example .env → заполни одноразовый migrator input
# TELEGRAM_BOT_TOKEN тестового бота и свой ENCRYPTION_KEY.
# Запуск: ./scripts/run-local.sh   Остановка: ./scripts/run-local.sh --down
# ============================================================================
set -Eeuo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=".env"

# --- Защита: не запускать на боевом конфиге ---
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ОШИБКА: нет $ENV_FILE. Скопируй: cp .env.local.example .env и заполни." >&2
  exit 1
fi
if ! grep -qx 'FB_AGENT_PROFILE=local' "$ENV_FILE"; then
  echo "ОШИБКА: в $ENV_FILE нет 'FB_AGENT_PROFILE=local'." >&2
  echo "Это защита от запуска dev-стека на боевом .env. Используй .env.local.example." >&2
  exit 1
fi
export FB_AGENT_PROFILE=local

# --- Защита: использовать только тестового бота ---
echo "ℹ Локальный профиль: webhook/outbox workers под ТЕСТОВЫМ ботом."
echo "ℹ Для входящих updates настрой отдельный HTTPS tunnel и webhook secret."

COMPOSE=(docker compose --env-file "$ENV_FILE")
LOCAL_SERVICES=(api telegram_delivery_worker telegram_update_worker)

if [[ "${1:-}" == "--down" ]]; then
  "${COMPOSE[@]}" down
  echo "Локальный стек остановлен."
  exit 0
fi
if [[ "${1:-}" == "--logs" ]]; then
  "${COMPOSE[@]}" logs -f "${LOCAL_SERVICES[@]}"
  exit 0
fi

echo "=== [1/3] Обязательная инфраструктура (postgres) ==="
"${COMPOSE[@]}" up -d postgres
if ! "${COMPOSE[@]}" up -d redis; then
  printf '%s\n' \
    "ПРЕДУПРЕЖДЕНИЕ: Redis недоступен; API и Telegram PostgreSQL-контуры продолжат запуск." \
    >&2
fi

echo "=== [2/3] Миграции БД ==="
"${COMPOSE[@]}" run --rm migrate

echo "=== [3/3] Безопасный набор: ${LOCAL_SERVICES[*]} ==="
"${COMPOSE[@]}" up -d "${LOCAL_SERVICES[@]}"

echo ""
echo "✅ Локальный dev-стек поднят."
echo "   API:            http://localhost:8100  (/healthz, /readyz)"
echo "   Запущено:       postgres, api, Telegram delivery/update workers"
echo "   Опционально:    redis (его недоступность не блокирует control/notification planes)"
echo "   НЕ запущено:    observer/сканирование, meta_api/мутации,"
echo "                   Vision/browser-agent — боевые действия с рекламой невозможны."
echo "   Сканирование:   выключено (is_scanning_enabled=false по умолчанию)."
echo ""
echo "   Логи:  ./scripts/run-local.sh --logs      Стоп:  ./scripts/run-local.sh --down"
