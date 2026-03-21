.PHONY: up down logs test lint format precommit precommit-install

PYTHON := $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; elif command -v python3 >/dev/null 2>&1; then command -v python3; else echo python3; fi)

SHELL := /bin/bash

up:
	bash scripts/bootstrap.sh

down:
	bash scripts/bootstrap.sh --down

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
