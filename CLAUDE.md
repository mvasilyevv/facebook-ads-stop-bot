# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language rules

- All comments, error messages, log messages, and Telegram notifications must be in Russian.
- Add a short Russian comment above each test explaining the scenario.

## Commands

```bash
# Infrastructure (Postgres 16 + Redis 7)
docker compose up -d

# Install
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'

# Run services (each in its own terminal)
uvicorn apps.api.main:app --host 0.0.0.0 --port 8100 --reload   # API
python run_observer.py                                             # Observer worker
cd frontend && npm run dev                                         # React UI (Vite)

# Testing & linting
pytest tests/ -x                          # full suite
pytest tests/unit/test_evaluator.py -x    # single file
ruff check .                              # lint
ruff format .                             # format

# Database migration
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Architecture

**FB Stop Bot v2** — monitors Facebook ads via anti-detect browser, evaluates stop-rules, sends Telegram alerts, and auto-disables ads.

### Three workers + API

1. **observer_worker** (`apps/observer_worker/`) — infinite loop: reload Ads Manager → scroll & parse HTML → evaluate 6 stop-rules → FSM state transition → send Telegram alerts. Entry point: `run_observer.py`.
2. **disable_worker** (`apps/disable_worker/`) — polls DisableTask queue, executes Playwright clicks to disable ads, retries with exponential backoff.
3. **telegram_poller** (`apps/telegram_poller/`) — long-polls Telegram Bot API, handles commands (`/start`, `/status`, `/ads`, etc.) and inline "Отключить" callback buttons.
4. **api** (`apps/api/`) — FastAPI on :8100, serves dashboard stats, ad snapshots, offers CRUD, rule config, settings.

### Core (`core/`)

- **domain.py** — three enums: `AlertStage` (WARNING/STOP), `AlertState` (NORMAL→WARNING_SENT→STOP_SENT→CLAIMED→DISABLED), `DisableTaskStatus`.
- **models/** — SQLAlchemy 2.x async ORM: ObserverSettings, TelegramSettings, Offer, OfferRuleConfig, AdSnapshot, AlertEvent, DisableTask.
- **observer/** — `service.py` (evaluation cycle), `state_machine.py` (FSM: one-way transitions, UUID tokens, no duplicate notifications).
- **scanner/** — `parser.py` (regex DOM parser for Ads Manager table), `models.py` (frozen `ScannedAdRow` dataclass).
- **rules/** — `evaluator.py` (6 stop-rules: CPC, CPL, CPR, regs-without-deps, spend-without-deps, spend-with-deps), `types.py` (RuleContext, RuleHit, RuleEvaluation). Each rule has WARNING tier (80% of threshold) and STOP tier.
- **browser/** — `vision_client.py` (async httpx client for Vision anti-detect API), `manager.py` (Playwright CDP connection).
- **telegram/** — `client.py` (Bot API wrapper), `renderer.py` (alert formatting with inline buttons), `bot_handler.py` (command routing).
- **config.py** — pydantic-settings, singleton `get_settings()`.
- **db/base.py** — SQLAlchemy declarative base.

### Frontend (`frontend/`)

React 19 + Vite (JSX, no TypeScript). Pages: DashboardPage, AdsPage, OffersPage, SettingsPage. API client in `api.js`.

## Key design rules

- Scanning, rule evaluation, and action execution must stay in separate modules/workers.
- Prefer async Python for all I/O.
- Use SQLAlchemy 2.x async, FastAPI, Pydantic v2.
- No dangerous actions by default; disable requires explicit Telegram callback confirmation.
- Alert state machine is one-way (no going back from STOP_SENT/CLAIMED/DISABLED).
- Domain data structures (`ScannedAdRow`, `RuleHit`, `AlertCandidate`) are frozen dataclasses.
- Ruff: line-length=100, target py312, rules E/F/I/B/ASYNC (E501 ignored).

## Infrastructure

- Postgres 16 (port 5433) + Redis 7 (port 6380) via `docker-compose.yml`.
- Vision anti-detect browser (external, port 3030) — requires `VISION_X_TOKEN` and `VISION_PROFILE_ID`.
- Python 3.12+.
