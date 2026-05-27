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

.PHONY: help check-env check-tools venv install-backend install-frontend install \
	docker-up docker-down db-wait apply-schema backup-secrets restore-secrets bootstrap \
	observer telegram disable-worker enable-worker cleanup-worker reconciler-worker \
	meta-api-worker health-watchdog enable-reco-worker digest-scheduler \
	creator-worker creator-recorder api \
	frontend frontend-build lint format test test-unit test-telegram test-integration verify \
	start stop logs proto-compile proto-watch \
	browser-agent browser-agent-dev browser-agent-build tma-dev tma-build

help: ## Показать доступные команды
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check-env: ## Проверить наличие .env
	@if [ ! -f .env ]; then \
		if [ -f .env.example ]; then \
			cp .env.example .env; \
			echo "Создан .env из .env.example. Заполните переменные и повторите команду."; \
		else \
			echo ".env не найден."; \
		fi; \
		exit 1; \
	fi

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

docker-up: check-tools ## Поднять Docker-сервисы
	docker compose up -d

docker-down: ## Остановить Docker-сервисы
	docker compose down

db-wait: ## Дождаться готовности Postgres
	@for i in $$(seq 1 30); do \
		if docker compose exec -T postgres pg_isready -U fb_stop_bot_v2 -d fb_stop_bot_v2 >/dev/null 2>&1; then \
			echo "Postgres готов"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	echo "Postgres не стал готов за 30 секунд"; \
	exit 1

bootstrap: check-env check-tools docker-up db-wait install apply-schema ## Полная подготовка проекта (drop+apply v2 схемы)

observer: check-env install-backend ## Запустить observer worker
	$(PY) run_observer_worker.py

telegram: check-env install-backend ## Запустить Telegram poller
	$(PY) run_telegram_poller.py

disable-worker: check-env install-backend ## Запустить disable worker
	$(PY) run_disable_worker.py

enable-worker: check-env install-backend ## Запустить enable worker
	$(PY) run_enable_worker.py

cleanup-worker: check-env install-backend ## Запустить cleanup worker (retention + partitions)
	$(PY) run_cleanup_worker.py

reconciler-worker: check-env install-backend ## Запустить reconciler worker (stuck tasks)
	$(PY) run_reconciler_worker.py

meta-api-worker: check-env install-backend ## Запустить meta_api worker (Marketing API mutations)
	$(PY) run_meta_api_worker.py

health-watchdog: check-env install-backend ## Запустить health watchdog (мониторинг heartbeat'ов воркеров)
	$(PY) run_health_watchdog.py

enable-reco-worker: check-env install-backend ## Запустить enable recommendation worker
	$(PY) run_enable_recommendation_worker.py

digest-scheduler: check-env install-backend ## Запустить ежедневный TG digest scheduler (9:00 UTC)
	$(PY) run_digest_scheduler.py

creator-worker: check-env install-backend ## Запустить creator worker (Vision-fallback для plan_run)
	$(PY) run_creator_worker.py

creator-recorder: check-env install-backend ## Запустить creator recorder (запись планов через CDP)
	$(PY) run_creator_recorder.py

api: check-env install-backend ## Запустить FastAPI на порту 8000 (health + AdSet.pro postback)
	$(VENV_BIN)/uvicorn apps.api.main:app --host 0.0.0.0 --port 8000 --reload

apply-schema: check-env install-backend ## Drop + apply v2 схемы БД (ОПАСНО, требует --confirm-drop)
	$(PY) scripts/apply_v2_schema.py --confirm-drop

backup-secrets: check-env install-backend ## Бэкап Vision/TG токенов
	$(PY) scripts/backup_secrets.py

restore-secrets: check-env install-backend ## Восстановить Vision/TG токены из последнего бэкапа
	$(PY) scripts/restore_secrets.py

lint: install-backend ## Проверить Python-код через Ruff
	$(RUFF) check .

format: install-backend ## Отформатировать Python-код через Ruff
	$(RUFF) format .

test: install-backend ## Прогнать все backend-тесты
	$(PYTEST) tests/ -x

test-unit: install-backend ## Прогнать unit-тесты
	$(PYTEST) tests/unit -q

test-telegram: install-backend ## Прогнать Telegram-набор тестов
	$(PYTEST) -q tests/unit/test_telegram_bot_handler.py tests/unit/test_telegram_renderer.py tests/integration/test_telegram_send_via_respx.py tests/integration/test_telegram_alert_dispatcher.py tests/integration/test_telegram_poller_e2e.py

test-integration: install-backend ## Прогнать интеграционные тесты (требуется Postgres из docker-compose)
	$(PYTEST) -q tests/integration --timeout=30

verify: lint test-unit test-integration ## Выполнить основной проверочный прогон

start: ## Поднять весь проект через run.sh
	./run.sh

stop: ## Остановить весь проект через run.sh
	./run.sh --down

logs: ## Показать логи run.sh
	./run.sh --logs

proto-compile: ## Скомпилировать proto файлы в Python и Node.js stubs
	@mkdir -p $(GRPC_PY_OUT)
	# Python stubs
	$(PY) -m grpc_tools.protoc \
		-Iproto \
		--python_out=$(GRPC_PY_OUT) \
		--grpc_python_out=$(GRPC_PY_OUT) \
		--pyi_out=$(GRPC_PY_OUT) \
		$(PROTO_DIR)/browser_session.proto \
		$(PROTO_DIR)/scanner.proto \
		$(PROTO_DIR)/creator.proto \
		$(PROTO_DIR)/meta_api.proto \
		$(PROTO_DIR)/ad_library.proto
	# grpc_tools генерирует absolute import `from v1 import ...`, который ломает пакет clients.python_grpc.
	$(PY) -c "from pathlib import Path; [path.write_text(path.read_text().replace('from v1 import ', 'from . import ')) for path in Path('$(GRPC_PY_OUT)/v1').glob('*_pb2_grpc.py')]"
	@if [ -x "$(RUFF)" ]; then $(RUFF) check $(GRPC_PY_OUT)/v1 --fix; fi
	@echo "Python stubs сгенерированы в $(GRPC_PY_OUT)"
	# Node.js stubs
	cd $(GRPC_NODE_DIR) && npm run proto 2>/dev/null || echo "Node.js stubs: запустите 'cd services/browser-agent && npm install && npm run proto'"

proto-watch: ## Следить за proto/ и перекомпилировать
	nodemon --watch proto/ -e proto --exec "make proto-compile"

browser-agent-dev: ## Запустить browser-agent в dev-режиме
	cd $(GRPC_NODE_DIR) && npm run dev

browser-agent-build: ## Собрать browser-agent
	cd $(GRPC_NODE_DIR) && npm run build

browser-agent: ## Запустить собранный browser-agent
	cd $(GRPC_NODE_DIR) && npm start

tma-dev: ## Запустить Telegram Mini App в dev-режиме (порт 5174)
	cd frontend-mini && $(NPM) run dev

tma-build: ## Собрать Telegram Mini App
	cd frontend-mini && $(NPM) ci && $(NPM) run build

# ─── Kubernetes ──────────────────────────────────────────────────────────────

DOCKER_REGISTRY ?= localhost
IMAGE_TAG ?= latest

.PHONY: docker-build k3s-import helm-install helm-uninstall k8s-logs

docker-build: ## Собрать все Docker-образы
	docker build -f docker/Dockerfile.python-base -t $(DOCKER_REGISTRY)/fb-stop-bot/python-base:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.api -t $(DOCKER_REGISTRY)/fb-stop-bot/api:$(IMAGE_TAG) \
		--build-arg BASE_IMAGE=$(DOCKER_REGISTRY)/fb-stop-bot/python-base:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.workers -t $(DOCKER_REGISTRY)/fb-stop-bot/workers:$(IMAGE_TAG) \
		--build-arg BASE_IMAGE=$(DOCKER_REGISTRY)/fb-stop-bot/python-base:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.browser-agent -t $(DOCKER_REGISTRY)/fb-stop-bot/browser-agent:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.frontend -t $(DOCKER_REGISTRY)/fb-stop-bot/frontend:$(IMAGE_TAG) .
	docker build -f docker/Dockerfile.mini-app -t $(DOCKER_REGISTRY)/fb-stop-bot/mini-app:$(IMAGE_TAG) .

k3s-import: ## Импортировать Docker-образы в k3s (требует sudo)
	@for img in api workers browser-agent frontend mini-app; do \
		echo "Импортируем $(DOCKER_REGISTRY)/fb-stop-bot/$$img:$(IMAGE_TAG) в k3s..."; \
		docker save $(DOCKER_REGISTRY)/fb-stop-bot/$$img:$(IMAGE_TAG) | sudo k3s ctr images import -; \
	done
	@echo "Все образы импортированы в k3s"

helm-install: ## Установить/обновить Helm chart
	helm upgrade --install fb-stop-bot helm/fb-stop-bot \
		-f helm/fb-stop-bot/values.yaml \
		-f helm/fb-stop-bot/values-mini-pc.yaml \
		-f helm/fb-stop-bot/secrets.yaml \
		--namespace fb-stop-bot --create-namespace

helm-uninstall: ## Удалить Helm release
	helm uninstall fb-stop-bot -n fb-stop-bot

k8s-logs: ## Показать логи всех подов fb-stop-bot
	kubectl logs -n fb-stop-bot -l app.kubernetes.io/name=fb-stop-bot --tail=50 -f
