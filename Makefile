.PHONY: test lint format migrate precommit precommit-install dev dev-backend dev-frontend dev-worker dev-browser-host compose-up compose-down compose-logs

SHELL := /bin/bash

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

migrate:
	alembic upgrade head

dev:
	bash scripts/dev.sh

dev-backend:
	bash scripts/backend.sh

dev-frontend:
	bash scripts/frontend.sh

dev-worker:
	bash scripts/worker.sh

dev-browser-host:
	bash scripts/browser-host.sh

compose-up:
	bash scripts/compose-up.sh

compose-down:
	bash scripts/compose-down.sh

compose-logs:
	bash scripts/compose-logs.sh

precommit:
	pre-commit run --all-files

precommit-install:
	pre-commit install
