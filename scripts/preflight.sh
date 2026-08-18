#!/usr/bin/env bash
# Локальный прогон тех же проверок, что гоняет .github/workflows/verify.yml.
#
# Зачем: 18.08.2026 правка строки интерфейса сломала Playwright, а локально был
# прогнан только `pnpm -r test` — поломка доехала до CI и стоила цикла. Здесь
# собрано всё, что CI проверяет, чтобы это можно было увидеть до пуша.
#
#   scripts/preflight.sh          полный прогон (примерно 12-15 минут)
#   scripts/preflight.sh --fast   без Playwright, Storybook и docker-сборок
#
# База: скрипт поднимает СВОЙ одноразовый Postgres и сносит его в конце. Ни
# боевая база, ни локальный контур не затрагиваются — integration-фикстуры
# сносят offers/offer_rules, поэтому чужую базу им давать нельзя.
#
# Полный прогон требует Docker: одноразовая база, actionlint и сборки фронтов
# идут в контейнерах.
set -euo pipefail

cd "$(dirname "$0")/.."

FAST=0
if [ "${1:-}" = "--fast" ]; then
  FAST=1
fi

PYTHON="${PYTHON:-.venv/bin/python}"
export PYTHONDONTWRITEBYTECODE=1

# Свой контейнер и свой порт: 55432 занят ad-hoc прогонами integration-тестов.
PREFLIGHT_DB_CONTAINER="fb-agent-preflight-db"
PREFLIGHT_DB_PORT="${PREFLIGHT_DB_PORT:-55433}"

step() {
  printf '\n\033[1m==> %s\033[0m\n' "$1"
}

cleanup() {
  docker rm -f "$PREFLIGHT_DB_CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

step "База: одноразовый Postgres на порту $PREFLIGHT_DB_PORT"
cleanup
docker run --rm -d \
  --name "$PREFLIGHT_DB_CONTAINER" \
  -e POSTGRES_USER=preflight \
  -e POSTGRES_PASSWORD=preflight_pw \
  -e POSTGRES_DB=fb_agent_preflight \
  -p "$PREFLIGHT_DB_PORT:5432" \
  postgres:16 >/dev/null
# Ждём с ХОСТА, а не изнутри контейнера: официальный образ postgres поднимает
# временный сервер на unix-сокете для init-скриптов, и `pg_isready` внутри
# отвечает ОК ещё до того, как проброшенный порт начнёт принимать соединения.
for _ in $(seq 1 60); do
  if "$PYTHON" -c "
import socket, sys
sock = socket.socket()
sock.settimeout(1)
sys.exit(0 if sock.connect_ex(('127.0.0.1', $PREFLIGHT_DB_PORT)) == 0 else 1)
" 2>/dev/null; then
    break
  fi
  sleep 1
done
"$PYTHON" -c "
import socket, sys
sock = socket.socket()
sock.settimeout(1)
if sock.connect_ex(('127.0.0.1', $PREFLIGHT_DB_PORT)) != 0:
    sys.exit('одноразовый Postgres не принимает соединения на порту $PREFLIGHT_DB_PORT')
"
echo "готова"

export POSTGRES_USER=preflight
export POSTGRES_PASSWORD=preflight_pw
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT="$PREFLIGHT_DB_PORT"
export POSTGRES_DB=fb_agent_preflight
export DATABASE_URL="postgresql+asyncpg://preflight:preflight_pw@127.0.0.1:$PREFLIGHT_DB_PORT/fb_agent_preflight"
# TEST_DATABASE_URL НЕ задаём намеренно: conftest сам заводит соседнюю
# <POSTGRES_DB>_test на том же кластере, а совпадение с рабочей базой он
# запрещает — destructive-фикстуры не должны сносить схему, на которой
# только что прогнали миграции. Так же устроен и CI.
export REDIS_URL="${REDIS_URL:-redis://localhost:6379/0}"

step "Backend: сгенерированные gRPC-стабы не разошлись с proto"
"$PYTHON" -B scripts/generate_grpc_stubs.py
git diff --exit-code -- clients/python_grpc

step "Backend: ruff"
ruff check .

step "Backend: миграции с чистой базы и дрейф ORM"
"$PYTHON" -m scripts.run-migrations-locked

step "Backend: pytest tests/"
"$PYTHON" -m pytest tests/ --timeout=30 -q

step "Контракт: pnpm run sync:api без дрейфа"
PYTHON="$PYTHON" pnpm run sync:api
git diff --exit-code -- frontend/openapi.json packages/shared/src/api/generated.ts

step "Фронт: pnpm lint"
pnpm lint

step "Фронт: pnpm typecheck"
pnpm typecheck

step "Фронт: pnpm test"
pnpm test

step "Browser-agent: lint и тесты"
npm run lint --prefix services/browser-agent
npm test --prefix services/browser-agent

step "Платформа: права на исполняемые точки входа"
python3 scripts/validate_executable_modes.py scripts/fbctl scripts/validate-platform-configs.sh

step "Платформа: встроенный fbctl и Compose"
./scripts/validate-platform-configs.sh

step "Платформа: shellcheck"
find scripts -maxdepth 1 -type f -name '*.sh' -print0 | sort -z \
  | xargs -0 docker run --rm --volume "$PWD:/mnt:ro" --workdir /mnt koalaman/shellcheck:stable

if [ "$FAST" = "1" ]; then
  printf '\n\033[1mБыстрый прогон закончен. Playwright, Storybook и docker-сборки пропущены.\033[0m\n'
  exit 0
fi

step "Платформа: actionlint"
docker run --rm --volume "$PWD:/repo:ro" --workdir /repo \
  rhysd/actionlint@sha256:887a259a5a534f3c4f36cb02dca341673c6089431057242cdc931e9f133147e9 -color

step "Фронт: docker-сборки"
docker build --target builder --file docker/Dockerfile.frontend .
docker build --target builder --file docker/Dockerfile.mini-app .

step "UI: Storybook и доступность"
pnpm --filter fb-stop-bot-frontend build-storybook
pnpm --filter fb-stop-bot-frontend test:storybook

step "UI: Playwright"
pnpm --filter fb-stop-bot-frontend test:e2e

printf '\n\033[1mВсё, что гоняет CI, прошло локально.\033[0m\n'
