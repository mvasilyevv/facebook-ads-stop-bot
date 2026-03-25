# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language rules

- All comments, error messages, log messages, and Telegram notifications must be in Russian.
- Add a short Russian comment above each test explaining the scenario.

## Commands

```bash
# Full stack bootstrap (Postgres + Redis via Docker, then API/worker/browser_host/frontend)
./run.sh
./run.sh --check    # environment checks only
./run.sh --down     # stop everything (local processes + Docker infra)

# Makefile shortcuts
make up             # full bootstrap
make down           # stop all
make logs           # tail api/worker/browser_host/frontend logs
make infra-logs     # tail Postgres/Redis Docker logs

# Testing & linting
make test           # pytest (full suite)
make lint           # ruff check
make format         # ruff format
make precommit      # all pre-commit hooks

# Run a single test
pytest tests/unit/test_foo.py::test_bar -x

# Database migration
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Architecture

**Facebook Ads Stop Bot** — service that monitors and auto-manages Facebook ads via an anti-detect browser.

### Four runtime services (started by `run.sh`)

1. **api** (`apps/api`) — FastAPI on :8000. 13 routers under `apps/api/routers/`. Bootstraps reference data on startup.
2. **worker** (`apps/worker`) — async scheduler: full scan, action queue, targeted recheck, fast-stop pipeline.
3. **browser_host** (`apps/browser_host`) — Playwright-based edge agent connecting to Vision anti-detect browser API. Scans Facebook Ads pages.
4. **frontend** (`frontend/`) — React 18 + TypeScript + Vite on :5173.

### Shared core (`core/`)

- **models/** — SQLAlchemy 2.x async ORM models split by domain: `advertising.py`, `operations.py`, `browser.py`, `offers.py`.
- **repositories/** — data access layer (one repo per aggregate).
- **services/** — business logic: `rule_runtime.py` (rule evaluation), `service_settings.py`, `advertising_history_reset.py`.
- **domain/** — enums, offer resolution logic.
- **scanner/** — Facebook page scanner (models, protocols, service).
- **rules/** — CPA threshold rule evaluation.
- **actions/** — action execution protocols.
- **events/** — domain event definitions.
- **config/settings.py** — pydantic-settings configuration.
- **db/** — SQLAlchemy base and async session factory.
- **locks.py** — Redis distributed locks.

### Notifications (`apps/notifier`)

Telegram notifications via outbox pattern: events → outbox table → formatter → HTTP transport.

## Key design rules

- Do not mix scanning, rule evaluation, and action execution in one module.
- Prefer async Python for all I/O services.
- Use SQLAlchemy 2.x async, FastAPI, Pydantic v2.
- Never enable dangerous actions by default; `auto_resume` requires an explicit feature flag.
- Ruff: line-length=100, target py312, rules E/F/I/B/ASYNC (E501 ignored).

## Testing

- `tests/unit/` and `tests/integration/` with `tests/fixtures/` for shared data.
- pytest with `asyncio_mode="auto"`, aiosqlite for test DB.
- Pre-commit hook runs unit tests automatically on relevant changes.

## Infrastructure

- Postgres 16 + Redis 7 via `docker-compose.yml`.
- Alembic migrations in `migrations/versions/` (naming: `YYYYMMDD_NNNN_description.py`).
- Python 3.12+, venv install: `pip install -e '.[dev]'`.
