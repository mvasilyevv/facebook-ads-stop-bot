.PHONY: up down logs infra-logs test lint format precommit precommit-install build build-frontend restart

PYTHON := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; elif command -v python3 >/dev/null 2>&1; then command -v python3; else echo python3; fi)

SHELL := /bin/bash

NPM := $(shell if command -v pnpm >/dev/null 2>&1 && [ -f frontend/pnpm-lock.yaml ]; then echo pnpm; elif command -v yarn >/dev/null 2>&1 && [ -f frontend/yarn.lock ]; then echo yarn; else echo npm; fi)

up:
	bash scripts/bootstrap.sh

down:
	bash scripts/bootstrap.sh --down

restart:
	bash scripts/bootstrap.sh --down
	bash scripts/bootstrap.sh

build-frontend:
	cd frontend && $(NPM) install && $(NPM) run build

build: build-frontend
	@echo "Сборка завершена"

logs:
	@sh -c 'files=""; for file in /tmp/fb_agent_backend.log /tmp/fb_agent_worker.log /tmp/fb_agent_browser_host.log /tmp/fb_agent_frontend.log; do if [ -f "$$file" ]; then files="$$files $$file"; fi; done; if [ -z "$$files" ]; then echo "Локальные логи не найдены. Сначала запустите ./run.sh"; exit 1; fi; exec tail -n 200 -f $$files'

infra-logs:
	docker compose logs -f --tail=200 postgres redis

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

precommit:
	$(PYTHON) -m pre_commit run --all-files

precommit-install:
	$(PYTHON) -m pre_commit install
