.PHONY: up down logs test lint format precommit precommit-install build build-frontend restart

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
	docker compose logs -f --tail=200

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
