.PHONY: test lint format migrate precommit precommit-install

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

migrate:
	alembic upgrade head

precommit:
	pre-commit run --all-files

precommit-install:
	pre-commit install
