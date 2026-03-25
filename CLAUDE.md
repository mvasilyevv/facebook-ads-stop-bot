# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Language rules

- All comments, error messages, log messages, and Telegram notifications must be in Russian.
- Add a short Russian comment above each test explaining the scenario.

## Commands

```bash
# Запуск всего одной командой
./run.sh              # Docker + миграции + API + Observer + TG + Frontend
./run.sh --down       # остановка всех сервисов
./run.sh --logs       # просмотр логов

# Ручной запуск сервисов (каждый в своём терминале)
docker compose up -d                                                    # Postgres + Redis
uvicorn apps.api.main:app --host 0.0.0.0 --port 8100 --reload          # API
python run_observer.py                                                   # Observer worker
python -m apps.telegram_poller.main                                      # Telegram poller
python run_disable_worker.py                                             # Disable worker (отдельно)
cd frontend && npm run dev                                               # React UI (Vite)

# Тесты и линтинг
pytest tests/ -x                          # полный набор
pytest tests/unit/test_evaluator.py -x    # один файл
ruff check .                              # линтер
ruff format .                             # форматирование

# Миграции БД
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Architecture

**FB Stop Bot v2** — мониторит Facebook Ads через anti-detect браузер, оценивает стоп-правила, шлёт алерты в Telegram и автоматически отключает объявления.

### Четыре воркера + API

1. **observer_worker** (`apps/observer_worker/`) — бесконечный цикл: кнопка «Обновить» (или reload) → скролл таблицы → парсинг через `data-surface` атрибуты → оценка 6 стоп-правил → FSM-переход → сохранение снэпшота в БД → Telegram-алерт. Точка входа: `run_observer.py`.
2. **disable_worker** (`apps/disable_worker/`) — поллит очередь DisableTask из БД (SELECT FOR UPDATE SKIP LOCKED), выполняет Playwright-клик для отключения, retry с exponential backoff. Точка входа: `run_disable_worker.py`.
3. **telegram_poller** (`apps/telegram_poller/`) — long-polling Telegram Bot API, команды (`/start`, `/status`, `/ads`, `/offers`, `/rules`, `/disabled`, `/settings`, `/help`, `/set`), inline-кнопка «Отключить» создаёт DisableTask в БД.
4. **api** (`apps/api/`) — FastAPI на :8100, lifespan создаёт таблицы, `Depends(get_db)` для async-сессий. 14 эндпоинтов: настройки, CRUD офферов, правила, dashboard-статистика, снэпшоты, алерты, задачи на отключение, история расходов.

### Core (`core/`)

- **domain.py** — три enum: `AlertStage` (WARNING/STOP), `AlertState` (NORMAL→WARNING_SENT→STOP_SENT→CLAIMED→DISABLED), `DisableTaskStatus`.
- **models/** — SQLAlchemy 2.x async ORM: ObserverSettings, TelegramSettings, Offer, OfferRuleConfig, AdSnapshot, AlertEvent, DisableTask. Mixins: UUIDPrimaryKey, Timestamp (UTC).
- **observer/service.py** — `evaluate_row()` (оценка одной строки), `build_rule_context()`, `build_metrics_json()`, dataclass'ы `AlertCandidate`, `ObserverCycleResult`.
- **observer/state_machine.py** — FSM: одноходовые переходы, UUID-токены, идемпотентность (повторный алерт той же стадии не отправляется).
- **scanner/parser.py** — парсинг через `data-surface` атрибуты Ads Manager (`campaign_group_name`, `spend`, `clicks`, `cpc`, `actions:lead`, `omni_complete_registration` и т.д.). Функция `refresh_table()` нажимает кнопку «Обновить» без перезагрузки.
- **scanner/models.py** — frozen dataclass `ScannedAdRow`.
- **rules/evaluator.py** — 6 стоп-правил с двухуровневой системой WARNING (80% от порога) / STOP. Спецлогика: spend > порог при 0 событиях → немедленный STOP.
- **rules/types.py** — `RuleContext` (все пороги), `RuleHit` (конкретное срабатывание), `RuleEvaluation` (итог с warning_hits/stop_hits).
- **browser/vision_client.py** — async httpx клиент для Vision API (list/start/stop профилей, CDP URL).
- **browser/manager.py** — `VisionBrowserManager`: подключение к Playwright через CDP, авто-определение folder_id, контекстный менеджер.
- **telegram/client.py** — минимальный async-клиент Bot API (send, edit, answer_callback, get_updates).
- **telegram/renderer.py** — форматирование алертов с inline-кнопками «Отключить».
- **telegram/bot_handler.py** — маршрутизация команд, пагинация объявлений, `_create_disable_task()` создаёт задачу в БД.
- **config.py** — pydantic-settings из .env, синглтон `get_settings()`.
- **db/** — `base.py` (declarative base), `__init__.py` (engine + session factory синглтоны).

### Матчинг офферов

Оффер сопоставляется с объявлением по вхождению кода оффера в название кампании или объявления. Например, оффер `DRC_CR2` → объявление `DRC_CR2_CR002`. Приоритет — самый длинный совпадающий код.

### Frontend (`frontend/`)

React 19 + Vite (JSX, без TypeScript). Страницы: DashboardPage, AdsPage, OffersPage, SettingsPage. API-клиент в `api.js`.

## Key design rules

- Сканирование, оценка правил и выполнение действий — в отдельных модулях/воркерах.
- Весь I/O — async (httpx, asyncpg, Playwright async).
- SQLAlchemy 2.x async, FastAPI, Pydantic v2.
- Отключение объявления требует явного подтверждения через Telegram-кнопку.
- FSM однонаправленная (нельзя вернуться из STOP_SENT/CLAIMED/DISABLED).
- Доменные структуры (`ScannedAdRow`, `RuleHit`, `AlertCandidate`) — frozen dataclasses.
- Ruff: line-length=100, target py312, rules E/F/I/B/ASYNC (E501, B008 ignored).

## Infrastructure

- Postgres 16 (port 5433) + Redis 7 (port 6380) via `docker-compose.yml`.
- Vision anti-detect browser (external, port 3030) — requires `VISION_X_TOKEN` and `VISION_PROFILE_ID`.
- Python 3.12+, Node.js (for frontend).
- Единый скрипт запуска `run.sh` — Docker, venv, миграции, все сервисы.
