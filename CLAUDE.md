# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Parallelism
- Максимум 5 фоновых агентов/задач одновременно. Перед запуском нового — убедиться, что лимит не превышен.

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
docker compose up -d                                                    # Postgres
uvicorn apps.api.main:app --host 0.0.0.0 --port 8100 --reload          # API
python run_observer.py                                                   # Observer worker
python -m apps.telegram_poller.main                                      # Telegram poller
python run_disable_worker.py                                             # Disable worker
python run_enable_worker.py                                              # Enable worker
python run_enable_recommendation_worker.py                               # Enable recommendation worker
cd frontend && npm run dev                                               # React UI (Vite)

# Через Makefile
make bootstrap        # docker + зависимости + миграции
make verify           # lint + Telegram smoke + frontend build
make test-unit        # только unit-тесты
make test-telegram    # Telegram smoke-тесты

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

**FB Stop Bot** — мониторит Facebook Ads через anti-detect браузер, оценивает стоп-правила, шлёт алерты в Telegram и автоматически отключает объявления.

### Шесть воркеров + API

1. **observer_worker** (`apps/observer_worker/`) — бесконечный цикл: кнопка «Обновить» → скролл таблицы → парсинг через `data-surface` атрибуты → оценка 6 стоп-правил → FSM-переход → сохранение снэпшота в БД → Telegram-алерт. Проверяет флаг `is_scanning_enabled` из БД каждый цикл. Без активных офферов не сканирует. Перечитывает офферы каждые 10 циклов. Точка входа: `run_observer.py`.
2. **disable_worker** (`apps/disable_worker/`) — поллит очередь DisableTask из БД (SELECT FOR UPDATE SKIP LOCKED), выполняет Playwright-клик для отключения, retry с exponential backoff (30s → 5min max). Точка входа: `run_disable_worker.py`.
3. **enable_worker** — выполняет задачи на включение объявлений (аналогично disable_worker, но для обратного действия). Точка входа: `run_enable_worker.py`.
4. **enable_recommendation_worker** (`apps/enable_recommendation_worker/`) — анализирует выключенные объявления, генерирует рекомендации на включение через `core/enable_recommendations/service.py`. Точка входа: `run_enable_recommendation_worker.py`.
5. **telegram_poller** (`apps/telegram_poller/`) — long-polling Telegram Bot API, команды (`/start`, `/status`, `/ads`, `/offers`, `/rules`, `/disabled`, `/settings`, `/help`, `/set`), inline-кнопка «Отключить» создаёт DisableTask в БД.
6. **api** (`apps/api/`) — FastAPI на :8100, lifespan создаёт таблицы, `Depends(get_db)` для async-сессий. Эндпоинты: настройки (GET/PUT + PATCH scanning toggle), CRUD офферов, правила, dashboard-статистика, снэпшоты, алерты, задачи на отключение, история расходов.

### Core (`core/`)

- **domain.py** — три enum: `AlertStage` (WARNING/STOP), `AlertState` (NORMAL→WARNING_SENT→STOP_SENT→CLAIMED→DISABLED), `DisableTaskStatus`.
- **models/** — SQLAlchemy 2.x async ORM: ObserverSettings (singleton, включая `is_scanning_enabled`), TelegramSettings, Offer, OfferRuleConfig, AdSnapshot, AlertEvent, DisableTask. Mixins: UUIDPrimaryKey, Timestamp (UTC).
- **observer/service.py** — `evaluate_row()`, `build_rule_context()`, `build_metrics_json()`, dataclass'ы `AlertCandidate`, `ObserverCycleResult`.
- **observer/state_machine.py** — FSM: одноходовые переходы, UUID-токены, идемпотентность (повторный алерт той же стадии не отправляется).
- **scanner/parser.py** — парсинг DOM Ads Manager. Три функции извлечения текста: `_get_ad_name` (фильтрует кнопки), `_get_metric_text` (числовые поля, пропускает тултипы >20 символов), `_get_first_text` (текстовые поля). Row ID из `data-surface` = FB Ad ID. Функция `refresh_table()` нажимает кнопку «Обновить» без перезагрузки.
- **scanner/models.py** — frozen dataclass `ScannedAdRow`.
- **rules/evaluator.py** — 6 стоп-правил с двухуровневой системой WARNING (80% от порога) / STOP. Спецлогика: spend > порог при 0 событиях → немедленный STOP.
- **rules/types.py** — `RuleContext` (все пороги), `RuleHit` (конкретное срабатывание), `RuleEvaluation` (итог с warning_hits/stop_hits).
- **browser/vision_client.py** — async httpx клиент для Vision API (list/start/stop профилей, CDP URL). При `port: None` от `/start` пробует fallback на `/list`.
- **browser/manager.py** — `VisionBrowserManager`: подключение к Playwright через CDP. Если профиль запущен без CDP-порта — автоматически перезапускает (stop → sleep 2s → start).
- **telegram/client.py** — минимальный async-клиент Bot API (send, edit, answer_callback, get_updates).
- **telegram/renderer.py** — форматирование алертов с inline-кнопками «Отключить».
- **telegram/bot_handler.py** — маршрутизация команд, пагинация объявлений, `_create_disable_task()` создаёт задачу в БД.
- **config.py** — pydantic-settings из .env, синглтон `get_settings()`. API-ключ (`API_KEY`) для аутентификации запросов.
- **db/** — `base.py` (declarative base), `__init__.py` (engine + session factory синглтоны).

### Матчинг офферов

Оффер сопоставляется с объявлением по вхождению кода оффера в название кампании или объявления (case-insensitive). Например, оффер `DRC_CR2` → кампания `CR2 | DRC | MV | Tyver | 25.03`. Приоритет — самый длинный совпадающий код. Бот не фильтрует по кампаниям/адсетам — сканирует всё, что видно на открытой странице Ads Manager.

### DOM-парсинг Ads Manager

Ячейки таблицы имеют атрибуты `data-surface` вида `/am/table/table_row:{AD_ID}unit/table_cell:{FIELD_KEY}`. Ключи полей обёрнуты в `forObjectType(...)` и `forAttributionWindow(...)`. Маппинг в `_FIELD_KEYS` словаре `scanner/parser.py`.

### Frontend (`frontend/`)

React 19 + Vite (JSX, без TypeScript). Страницы: DashboardPage (чеклист запуска, таймер скана, тогл сканирования, KPI-стрипы, графики), AdsPage, OffersPage, SettingsPage. API-клиент в `api.js`. Компоненты в `components/` (CampaignScorecard, AlertTray, TaskQueuePanel, графики). Хуки: `useAsyncPolling`, `useRefreshOnResume`. Vite-порт динамический (run.sh читает из лога).

## Key design rules

- Сканирование, оценка правил и выполнение действий — в отдельных модулях/воркерах.
- Весь I/O — async (httpx, asyncpg, Playwright async).
- SQLAlchemy 2.x async, FastAPI, Pydantic v2.
- Отключение объявления требует явного подтверждения через Telegram-кнопку.
- FSM однонаправленная (нельзя вернуться из STOP_SENT/CLAIMED/DISABLED).
- Доменные структуры (`ScannedAdRow`, `RuleHit`, `AlertCandidate`) — frozen dataclasses.
- Ruff: line-length=100, target py312, rules E/F/I/B/ASYNC (E501, B008 ignored).
- AdSnapshot — upsert по fb_ad_id (хранит последнее состояние). AlertEvent — append-only история алертов.

## Infrastructure

- Postgres 16 (port 5433, bind 127.0.0.1) via `docker-compose.yml`. Данные Postgres в именованном томе `pgdata`.
- Vision anti-detect browser (external, port 3030) — requires `VISION_X_TOKEN` and `VISION_PROFILE_ID`.
- Python 3.12+, Node.js (for frontend).
- Единый скрипт запуска `run.sh` — Docker, venv, миграции, все сервисы.
- Alembic миграции в `migrations/versions/`. При отсутствии — fallback на `Base.metadata.create_all`.
