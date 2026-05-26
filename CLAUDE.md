# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Parallelism
- Максимум 5 фоновых агентов/задач одновременно. Перед запуском нового — убедиться, что лимит не превышен.

## Language rules

- All comments, error messages, log messages, and Telegram notifications must be in Russian.
- Add a short Russian comment above each test explaining the scenario.

## Master integration document

Текущая большая работа — интеграция Meta Marketing API. Master source of truth — `META_INTEGRATION_PLAN.md` в корне. Не дублируй его содержимое здесь, сошлись на него.

## Commands

```bash
# Запуск всего одной командой
./run.sh              # Docker + миграции + API + все воркеры + Frontend
./run.sh --down       # остановка всех сервисов
./run.sh --logs       # просмотр логов

# Ручной запуск сервисов (каждый в своём терминале)
docker compose up -d                                                    # Postgres + Redis
uvicorn apps.api.main:app --host 0.0.0.0 --port 8100 --reload          # API
python run_observer.py                                                   # Observer worker
python -m apps.telegram_poller.main                                      # Telegram poller
python run_disable_worker.py                                             # Disable worker
python run_enable_worker.py                                              # Enable worker
python run_enable_recommendation_worker.py                               # Enable recommendation worker
python run_creator_worker.py                                             # Campaign creator worker (Vision-based)
python run_health_watchdog.py                                            # Health watchdog
cd services/browser-agent && npm run start                               # Node.js gRPC browser-agent (port 50051)
cd frontend && npm run dev                                               # React UI (Vite)
cd frontend-mini && npm run dev                                          # Telegram Mini App

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
cd frontend && npm run test               # Vitest для frontend
cd services/browser-agent && npm test     # тесты browser-agent (TypeScript)

# Миграции БД
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## Architecture

**FB Stop Bot** — мониторит Facebook Ads, оценивает стоп-правила, шлёт алерты в Telegram, автоматически отключает объявления, создаёт новые кампании. Real-time часть работает через anti-detect браузер (Vision + Playwright + Node.js gRPC). Marketing API добавляется для latency-tolerant операций (см. `META_INTEGRATION_PLAN.md`).

### Девять воркеров + API + Node.js gRPC

**Python воркеры:**

1. **observer_worker** (`apps/observer_worker/`) — бесконечный цикл через gRPC к browser-agent: refresh таблицы → scroll → парсинг → оценка 6 стоп-правил → FSM-переход → snapshot upsert → Telegram-алерт. Проверяет `is_scanning_enabled` каждый цикл. Без активных офферов не сканирует. Перечитывает офферы каждые 10 циклов. 5 эскалаторов хрупкости (browser_recovery, self_healing, regression_guard, scan_guard, stale_data_handler). Точка входа: `run_observer.py`.
2. **disable_worker** (`apps/disable_worker/`) — поллит `DisableTask` (SELECT FOR UPDATE SKIP LOCKED), выполняет Playwright-клик через gRPC к browser-agent, retry с exponential backoff (30s → 5min max). Точка входа: `run_disable_worker.py`.
3. **enable_worker** (`apps/enable_worker/`) — аналогично disable, но для включения. После успешного `mark_succeeded` сбрасывает `alert_state` в NORMAL минуя state_machine. Точка входа: `run_enable_worker.py`.
4. **enable_recommendation_worker** (`apps/enable_recommendation_worker/`) — анализирует выключенные объявления, генерирует `EnableRecommendationEvent` через `core/enable_recommendations/service.py`. Точка входа: `run_enable_recommendation_worker.py`.
5. **creator_worker** (`apps/creator_worker/`) — поллит `PlanRun` (QUEUED) каждые 3 сек, открывает gRPC stream `CreatorService.RunPlan()`, аккумулирует `PlanEvent` в step_log, отправляет статус в Telegram при checkpoint. Точка входа: `run_creator_worker.py`.
6. **creator_recorder** (`apps/creator_recorder/`) — управляет жизненным циклом записи действий пользователя в браузере через gRPC. CLI-команды: `start <plan_name>`, `stop`, `status`. Сохраняет в JSON.
7. **telegram_poller** (`apps/telegram_poller/`) — long-polling Telegram Bot API. Команды: `/start`, `/status`, `/ads`, `/offers`, `/rules`, `/disabled`, `/settings`, `/help`, `/set`. Inline-кнопка «Отключить» создаёт `DisableTask`.
8. **health_watchdog** (`apps/health_watchdog/`) — мониторинг здоровья всех воркеров и инфраструктуры. Точка входа: `run_health_watchdog.py`.
9. **api** (`apps/api/`) — FastAPI на :8100, lifespan для async-сессий. 17 роутеров: настройки (GET/PUT + PATCH scanning toggle), CRUD офферов, правила, dashboard-статистика (3052 строки — кандидат на разнесение), snapshots, alerts, disable tasks, history, analytics, AI chat, naming tracker, TMA-роутер (отдельная аутентификация через Telegram initData), campaign recorder.

**Node.js gRPC сервис (`services/browser-agent/`):**

- 8200+ строк TypeScript, gRPC порт 50051, три service'а:
  - `BrowserSessionService` — управление Vision-профилем и CDP (StartBrowser, StopBrowser, Reconnect, Navigate, StreamSessionStatus)
  - `ScannerService` — DOM-парсинг через `data-surface` атрибуты + scroll/refresh/toggle (25+ методов: RunScanCycle stream, ParseVisibleRows, ToggleAd, ValidateColumns, HumanMove, HardReloadPage, ...)
  - `CreatorService` — запись и выполнение планов создания кампаний (RunPlan stream, StartRecording, StopRecording)
- Python gRPC client в `clients/python_grpc/client.py` (751 строка) с circuit-breaker (3 фейла → OPEN 60с) и session-recovery (NOT_FOUND → автоматический restart)

### Core (`core/`)

- **domain.py** — три enum: `AlertStage` (WARNING/STOP), `AlertState` (NORMAL→WARNING_SENT→STOP_SENT→CLAIMED→DISABLED), `DisableTaskStatus`.
- **models/** — 30 SQLAlchemy 2.x async ORM-моделей: ObserverSettings/VisionSettings/TelegramSettings (singleton через `singleton_key='default'`), Offer, OfferRuleConfig, OfferRuleStat, FbCampaign, FbAdset, FbAd (нормализованная иерархия), AdSnapshot (upsert), AlertEvent (append-only), AlertSnooze, ScanRun, AdMetricHistory, DisableTask, EnableTask, EnableRecommendationEvent, AdAutoEnableDisabled, CampaignCreatorTask, Plan, PlanRun, TelegramInvite/Recipient/MessageRef, AICache, WorkerHeartbeat, AdDepositCorrection, CabinetDayArchive. Mixins: UUIDPrimaryKey, Timestamp (UTC).
- **observer/** — `service.py` (`evaluate_row`, `build_rule_context`, `build_metrics_json`), `state_machine.py` (FSM с UUID-токенами и идемпотентностью), `disable_reconciler.py` (создаёт DisableTask на STOP, отменяет «протухшие» auto-tasks), `snapshot_writer.py`, `db_queries.py` (1066 строк — repository + use-case в одном, кандидат на разнесение), 5 эскалаторов хрупкости.
- **scanner/models.py** — frozen dataclass `ScannedAdRow` — главный контракт между сканером и evaluator'ом. Парсер DOM целиком в TypeScript (`services/browser-agent/src/parser.ts`).
- **rules/evaluator.py** — 6 стоп-правил с двухуровневой WARNING (80% от порога) / STOP логикой, спецлогика fast-stop (spend > порог при 0 событиях → немедленный STOP), funnel-лесенка, frequency-anomaly. `RuleContext`, `RuleHit`, `RuleEvaluation`.
- **task_queue/** — `PostgresTaskQueue` (захват через FOR UPDATE SKIP LOCKED) + `BaseTaskWorker` (общий шаблон retry + heartbeat + reconcile-hooks).
- **disable_tasks.py / enable_tasks.py** — outbox-паттерн для отложенных действий.
- **campaign_creator/** — фабрика создания кампаний (Vision-based): `CampaignSpec`, `PlanAction`, `PlanBuilder`, `STEP_REGISTRY` с 24 шагами (create_campaign, set_geo, upload_creatives, set_pixel_event, duplicate_adset, ...), `creo_scanner.py` (сканер папки креативов), `spec_builder.py`, `naming.py`.
- **campaign_recorder/** — запись пользовательских действий в браузере → JSON план.
- **creator_bridge/** — мост между Python и TS-bundle на странице (через `add_init_script` + `window.fbAgentEmit`).
- **creatives/** — `uniquify_creatives` (водяной знак), `folder_opener`.
- **campaign_scripts/planner.py** — декларативный план для ручного создания кампании.
- **enable_recommendations/service.py** — генерация рекомендаций на включение.
- **ai_assistant/** — AI-помощник с tool-use. Anthropic primary (Claude Sonnet 4.6 через `api.claudehub.fun`) + OpenAI fallback (gpt-5.4-mini через `gateway.nekocode.app`). `client.py` управляет fallback, `providers.py` унифицирует формат (`AIResponse`), `tools.py` сейчас содержит 4 операционных tool'а (`supervisor_restart`, `tail_log`, `api_get`, `set_scanning`), `chat.py` — диалоговая сессия, `explain.py` — объяснение алертов с in-memory кэшем.
- **ads/actions.py** — фасад высокоуровневых операций для UI: `get_ad_detail`, `disable_ad`, `snooze_ad`, `claim_ad`.
- **telegram/** — `client.py` (Bot API), `renderer.py` (форматирование алертов с inline-кнопками), `bot_handler.py` (маршрутизация команд, пагинация, создание DisableTask), `digest_scheduler.py` (ежедневный дайджест), `delivery.py`.
- **browser/** — `lock.py` (file-lock эксклюзивности браузер-сессии), `circuit_breaker.py` (AsyncCircuitBreaker для gRPC-вызовов). Реальный Vision-client живёт в Node.js.
- **fake_deposits.py** — ручные корректировки депозитов через `AdDepositCorrection` (трекер партнёрки не подключен, используется руками).
- **pubsub.py** — Redis pubsub для WebSocket в API.
- **alerts/**, **auth/**, **db/** — соответствующие домены.
- **config.py** — pydantic-settings из .env, синглтон `get_settings()`. API-ключ для аутентификации запросов.
- **crypto.py** — Fernet-шифрование секретов (Vision token, Telegram bot token).

### Будущие модули (см. META_INTEGRATION_PLAN.md)

После старта Этапа 1 интеграции Marketing API в `core/` появятся:

- **meta_api/** — async-клиент Marketing API, mutations через outbox, webhooks, audit, rate-limiter. **Изолирован от observer/scanner.** Не пишет в `AdSnapshot` напрямую — отдельные поля `last_api_observed_at` и `meta_ad_status`.
- **ad_library/** — scraper Meta Ad Library API + AI-парсинг чужих креативов в библиотеку паттернов.
- **adset_pro/** (опционально) — интеграция с трекером adset.pro для post-click данных.
- **ai_assistant/tools/** (пакет) — замена монолитного `tools.py`, ToolRegistry с risk_level (READ_ONLY / DRAFT_REQUIRED / CREATIVE).

Новые воркеры: `apps/meta_api_worker/`, `apps/webhook_consumer/`, `apps/ad_library_scanner/`.

### Матчинг офферов

Оффер сопоставляется с объявлением по вхождению кода оффера в название кампании или объявления (case-insensitive). Например, оффер `DRC_CR2` → кампания `CR2 | DRC | MV | Tyver | 25.03`. Приоритет — самый длинный совпадающий код. Бот не фильтрует по кампаниям/адсетам — сканирует всё, что видно на открытой странице Ads Manager.

### DOM-парсинг Ads Manager (в Node.js)

Ячейки таблицы имеют атрибуты `data-surface` вида `/am/table/table_row:{AD_ID}unit/table_cell:{FIELD_KEY}`. Ключи полей обёрнуты в `forObjectType(...)` и `forAttributionWindow(...)`. Маппинг в `services/browser-agent/src/ads-columns.ts` и `parser.ts`.

### Frontend

**Основной (`frontend/`):** React 19.2 + Vite 6.4 + JSX (без TypeScript). Tailwind 3.4 + design tokens (`src/styles/tokens.css`). TanStack Query v5 (только на DashboardPage, остальное на `useAsyncPolling + useEffect`). Recharts 3.8. Vitest 4.1 + Testing Library. Кастомный routing через `useState` в App.jsx (без react-router).

9 страниц: DashboardPage (зрелая, эталон TanStack Query + WS + optimistic updates, 809 строк), AdsPage (1446 строк — god-component, кандидат на разнесение), OffersPage (452), AnalyticsPage (тонкая обёртка над 6 компонентами), HistoryPage, NamingTrackerPage, ScriptsPage (1456 строк — god-component), SettingsPage, HealthMapPage.

**Telegram Mini App (`frontend-mini/`):** React 19.0 + Vite 5.4 + JSX + react-router-dom v7 + vanilla CSS (без Tailwind). Большая часть UI-логики дублирована из основного фронта (форматтеры, STATE_LABELS, fetch — отдельно для каждой страницы). Тесты отсутствуют.

Vite-порт динамический (run.sh читает из лога).

## Key design rules

- **Latency-critical vs latency-tolerant.** Operations с требованием sub-минутной реакции (observer scan, disable, enable) — через Vision. Операции с лагом 5-15 мин приемлемы (создание кампаний, изменения бюджета, аналитика) — через Marketing API. См. `META_INTEGRATION_PLAN.md` § 1.
- **Сканирование, оценка правил и выполнение действий — в отдельных модулях/воркерах.**
- **Весь I/O — async** (httpx, asyncpg, Playwright async, grpcio).
- **SQLAlchemy 2.x async, FastAPI, Pydantic v2.**
- **Outbox-паттерн для всех side-эффектов.** DisableTask/EnableTask/PlanRun/CampaignCreatorTask с идемпотентностью через UNIQUE idempotency_key. Новый код (mutations через Marketing API) — на ту же абстракцию `PostgresTaskQueue` + `BaseTaskWorker`.
- **Draft-first для AI mutations.** AI-tools НЕ вызывают write-API напрямую. Они создают запись в outbox со статусом `DRAFT`, человек подтверждает через Telegram/UI inline-кнопкой, затем worker исполняет. Reconciler чистит протухшие DRAFT через 24 часа.
- **Отключение объявления — два пути:** (1) автоматически — observer/disable_reconciler создаёт DisableTask при STOP-правиле (`requested_by_username="bot_auto_stop"`); (2) вручную — пользователь через TG (`/ads` или inline-кнопка под алертом). В обоих случаях через disable_worker; auto-задача отменяется reconcile-логикой, если объявление успело уйти из STOP до клика.
- **FSM однонаправленная:** повторный WARNING после WARNING_SENT не дублируется, эскалация WARNING_SENT → STOP_SENT возможна. Обратные сбросы из STOP_SENT/CLAIMED/DISABLED разрешены только observer'у (`reopen_reactivated_alert_state`) и enable_worker'у (`mark_succeeded` сбрасывает alert_state в NORMAL).
- **Доменные структуры** (`ScannedAdRow`, `RuleHit`, `AlertCandidate`) — frozen dataclasses.
- **ScannedAdRow — главный контракт.** Не мутировать при добавлении новых источников данных. Marketing API получает собственный `MetaApiAdRow`, конвертация через явный adapter с unit-тестами.
- **Никаких файлов >500 строк в новом коде.** Существующие god-components (AdsPage, ScriptsPage, dashboard.py router) — на разнесение.
- **Ruff:** line-length=100, target py312, rules E/F/I/B/ASYNC (E501, B008 ignored).
- **AdSnapshot** — upsert по fb_ad_id (хранит последнее состояние). **AlertEvent** — append-only.

## Infrastructure

- **Postgres 16** (port 5433, bind 127.0.0.1) через `docker-compose.yml`. Данные в именованном томе `pgdata`.
- **Redis** (port 6380) — очередь алертов и WebSocket pubsub.
- **Vision anti-detect browser** (external, port 3030) — `VISION_X_TOKEN` и `VISION_PROFILE_ID`.
- **Node.js gRPC browser-agent** (port 50051) — отдельный процесс, supervisord.
- **Python 3.12+, Node.js** (для browser-agent и frontend).
- **AI-провайдеры:** Anthropic (proxy через `api.claudehub.fun`) + OpenAI (proxy через `gateway.nekocode.app`).
- Единый скрипт запуска `run.sh` — Docker, venv, миграции, все воркеры, browser-agent, frontend.
- Alembic миграции в `migrations/versions/`. При отсутствии — fallback на `Base.metadata.create_all`.
- **Helm/k8s** артефакты в `helm/` и `k8s/` для production deployment.
