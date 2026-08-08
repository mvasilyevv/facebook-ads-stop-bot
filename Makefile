SHELL := /bin/bash

PYTHON ?= python3
NPM ?= npm
VENV_DIR ?= .venv
VENV_BIN := $(VENV_DIR)/bin
PY := $(VENV_BIN)/python
PIP := $(PY) -m pip
PYTEST := $(VENV_BIN)/pytest
RUFF := $(VENV_BIN)/ruff
FRONTEND_DIR := frontend

.DEFAULT_GOAL := help

PROTO_DIR := proto/v1
GRPC_PY_OUT := clients/python_grpc
GRPC_NODE_DIR := services/browser-agent

.PHONY: help check-env check-local-profile check-tools venv install-backend install-frontend install \
	docker-up docker-down db-wait migrate reset-disposable-db bootstrap \
	frontend frontend-build lint format test test-unit test-telegram test-integration verify \
	start stop logs proto-compile proto-watch \
	browser-agent-build tma-dev tma-build \
	export-openapi gen-api-types docker-build

help: ## Показать доступные команды
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check-env: ## Проверить наличие .env
	@if [ ! -f .env ]; then \
		echo ".env не найден. Скопируйте .env.local.example в .env и заполните."; \
		exit 1; \
	fi

check-local-profile: check-env ## Проверить fail-closed маркер локального контура
	@grep -qx 'FB_AGENT_PROFILE=local' .env || \
		(echo "Локальный runtime требует точную строку FB_AGENT_PROFILE=local в .env"; exit 1)

check-tools: ## Проверить системные зависимости
	@command -v $(PYTHON) >/dev/null || (echo "$(PYTHON) не найден"; exit 1)
	@command -v docker >/dev/null || (echo "docker не найден"; exit 1)
	@command -v $(NPM) >/dev/null || (echo "$(NPM) не найден"; exit 1)

venv: ## Создать виртуальное окружение Python
	@if [ ! -d "$(VENV_DIR)" ]; then \
		$(PYTHON) -m venv "$(VENV_DIR)"; \
	fi

install-backend: venv ## Установить backend-зависимости Python
	$(PIP) install -e '.[dev]'

install-frontend: ## Установить frontend-зависимости
	@if [ ! -d "$(FRONTEND_DIR)/node_modules" ]; then \
		cd $(FRONTEND_DIR) && $(NPM) ci; \
	else \
		echo "Frontend-зависимости уже установлены"; \
	fi

install: install-backend install-frontend ## Установить все зависимости

docker-up: check-tools check-local-profile ## Поднять fail-closed локальный контур
	FB_AGENT_PROFILE=local ./scripts/run-local.sh

docker-down: check-local-profile ## Остановить fail-closed локальный контур
	FB_AGENT_PROFILE=local ./scripts/run-local.sh --down

db-wait: ## Дождаться готовности Postgres
	@for i in $$(seq 1 30); do \
		if docker compose exec -T postgres sh -c 'pg_isready -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' >/dev/null 2>&1; then \
			echo "Postgres готов"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Postgres не стал готов за 30 секунд"; \
	exit 1

bootstrap: check-tools check-local-profile install start ## Подготовить fail-closed local dev-контур

migrate: check-local-profile install-backend ## Применить fresh baseline под advisory lock
	@set -a; . ./.env; set +a; \
	POSTGRES_HOST="$${POSTGRES_HOST:-127.0.0.1}" \
	POSTGRES_PORT="$${POSTGRES_PORT:-5433}" \
	POSTGRES_DB="$$POSTGRES_DB" \
	POSTGRES_USER="$$POSTGRES_USER" \
	POSTGRES_PASSWORD="$$POSTGRES_PASSWORD" \
	$(PY) -m scripts.run-migrations-locked

reset-disposable-db: install-backend ## Явно пересоздать только *_dev/*_test БД
	@test -n "$$FB_AGENT_DISPOSABLE_DATABASE_URL" || \
		(echo "Задайте FB_AGENT_DISPOSABLE_DATABASE_URL"; exit 1)
	@test -n "$$FB_AGENT_ALLOW_DESTRUCTIVE_RESET" || \
		(echo "Задайте FB_AGENT_ALLOW_DESTRUCTIVE_RESET"; exit 1)
	@test -n "$(CONFIRM_DATABASE)" || \
		(echo "Передайте CONFIRM_DATABASE=<точное имя *_dev/*_test БД>"; exit 1)
	$(PY) scripts/apply_schema.py --confirm-drop --confirm-database "$(CONFIRM_DATABASE)"

lint: install-backend ## Проверить Python-код через Ruff
	$(RUFF) check .

format: install-backend ## Отформатировать Python-код через Ruff
	$(RUFF) format .

test: install-backend ## Прогнать все backend-тесты
	$(PYTEST) tests/ -x

test-unit: install-backend ## Прогнать unit-тесты
	$(PYTEST) tests/unit -q

test-telegram: install-backend ## Прогнать Telegram-набор тестов
	$(PYTEST) -q tests/unit/test_telegram_runtime_architecture.py tests/unit/test_telegram_webhook_route.py tests/unit/test_telegram_command_replies.py tests/unit/test_telegram_html_gateway.py tests/integration/test_telegram_webhook_handlers_e2e.py

test-integration: install-backend ## Прогнать интеграционные тесты (требуется Postgres из docker-compose)
	$(PYTEST) -q tests/integration --timeout=30

verify: lint test-unit test-integration ## Выполнить основной проверочный прогон

export-openapi: install-backend ## Экспортировать OpenAPI-схему из FastAPI в frontend/openapi.json
	$(PY) scripts/export_openapi.py

gen-api-types: export-openapi ## Сгенерировать shared TypeScript-типы из OpenAPI
	pnpm run gen:api

start: check-local-profile ## Поднять единственный fail-closed локальный runtime
	FB_AGENT_PROFILE=local ./scripts/run-local.sh

stop: check-local-profile ## Остановить локальный runtime
	FB_AGENT_PROFILE=local ./scripts/run-local.sh --down

logs: check-local-profile ## Показать логи локального runtime
	FB_AGENT_PROFILE=local ./scripts/run-local.sh --logs

proto-compile: ## Скомпилировать proto файлы в Python stubs
	@mkdir -p $(GRPC_PY_OUT)
	# Python stubs
	$(PY) -m grpc_tools.protoc \
		-Iproto \
		--python_out=$(GRPC_PY_OUT) \
		--grpc_python_out=$(GRPC_PY_OUT) \
		--pyi_out=$(GRPC_PY_OUT) \
		$(PROTO_DIR)/browser_session.proto \
		$(PROTO_DIR)/scanner.proto \
		$(PROTO_DIR)/meta_api.proto
	# grpc_tools генерирует absolute import `from v1 import ...`, который ломает пакет clients.python_grpc.
	$(PY) -c "from pathlib import Path; [path.write_text(path.read_text().replace('from v1 import ', 'from . import ')) for path in Path('$(GRPC_PY_OUT)/v1').glob('*_pb2_grpc.py')]"
	@if [ -x "$(RUFF)" ]; then \
		$(RUFF) check $(GRPC_PY_OUT)/v1 --fix && \
		$(RUFF) format $(GRPC_PY_OUT)/v1; \
	fi
	@echo "Python stubs сгенерированы в $(GRPC_PY_OUT)"

proto-watch: ## Следить за proto/ и перекомпилировать
	nodemon --watch proto/ -e proto --exec "make proto-compile"

browser-agent-build: ## Собрать browser-agent
	cd $(GRPC_NODE_DIR) && npm run build

tma-dev: ## Запустить Telegram Mini App в dev-режиме (порт 5175)
	cd frontend-mini && $(NPM) run dev

tma-build: ## Собрать Telegram Mini App
	cd frontend-mini && $(NPM) ci && $(NPM) run build

# ─── Knowledge Base (NotebookLM) ───────────────────────────────────────────────

NOTEBOOKLM ?= $(HOME)/.local/bin/notebooklm

.PHONY: kb-doctor kb-sync kb-sync-dry

kb-doctor: ## Проверить notebooklm CLI (auth/health)
	$(NOTEBOOKLM) doctor

kb-sync-dry: install-backend ## Показать план синка docs → NotebookLM без заливки
	$(PY) scripts/kb_sync.py --dry-run

kb-sync: install-backend ## Синхронизировать docs/ → NotebookLM (идемпотентно, per-geo)
	$(PY) scripts/kb_sync.py --notebook-mode per-geo

# ─── Remotion (видео-постпродакшн) ─────────────────────────────────────────────

REMOTION_DIR := remotion
NODE22_BIN ?= /usr/local/opt/node@22/bin

.PHONY: remotion-install remotion-studio video-batch

remotion-install: ## Установить Remotion-зависимости (Node 22)
	cd $(REMOTION_DIR) && PATH="$(NODE22_BIN):$$PATH" npm ci

remotion-studio: ## Открыть Remotion Studio (превью шаблона)
	cd $(REMOTION_DIR) && PATH="$(NODE22_BIN):$$PATH" npm run studio

video-batch: install-backend ## Рендер видео-батча из реестра + uniquify (нужны GEO=, SLOT=, BG=)
	$(PY) scripts/video_batch.py --geo $(GEO) --slot $(SLOT) --bg $(BG)

# ─── Container images ────────────────────────────────────────────────────────

DOCKER_REGISTRY ?= localhost
IMAGE_TAG ?= latest

.PHONY: docker-build

docker-build: ## Собрать все Docker-образы
	docker build -f docker/Dockerfile.python-base -t $(DOCKER_REGISTRY)/fb-stop-bot/python-base:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.api -t $(DOCKER_REGISTRY)/fb-stop-bot/api:$(IMAGE_TAG) \
		--build-arg BASE_IMAGE=$(DOCKER_REGISTRY)/fb-stop-bot/python-base:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.workers -t $(DOCKER_REGISTRY)/fb-stop-bot/workers:$(IMAGE_TAG) \
		--build-arg BASE_IMAGE=$(DOCKER_REGISTRY)/fb-stop-bot/python-base:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.browser-agent -t $(DOCKER_REGISTRY)/fb-stop-bot/browser-agent:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.frontend -t $(DOCKER_REGISTRY)/fb-stop-bot/frontend:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.mini-app -t $(DOCKER_REGISTRY)/fb-stop-bot/mini-app:$(IMAGE_TAG) .
