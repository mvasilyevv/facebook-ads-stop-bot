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
cd services/browser-agent && npm run start                               # Node.js gRPC browser-agent (port 50051)
python run_observer_worker.py                                            # Observer worker (scan + FSM + TG dispatch)
python run_disable_worker.py                                             # Disable worker (poll task_queue)
python run_enable_worker.py                                              # Enable worker
python run_telegram_poller.py                                            # Telegram poller (/spy, /start, /help, inline callbacks)
python run_cleanup_worker.py                                             # Cleanup worker (retention + partitions)
python run_reconciler_worker.py                                          # Reconciler (stuck task_queue → retrying)
python run_meta_api_worker.py                                            # Marketing API mutations worker (Этап 5)
python run_health_watchdog.py                                            # Health watchdog (мониторинг worker:heartbeat:*)
python run_enable_recommendation_worker.py                               # Enable recommendation worker (recovered ads)
python run_digest_scheduler.py                                           # Daily TG digest (09:00 UTC)
python run_meta_api_worker.py                                            # Marketing API mutations worker (skeleton до Этапа 5)
python run_health_watchdog.py                                            # Health watchdog (мониторинг worker:heartbeat:*)

# Через Makefile
make bootstrap        # docker + зависимости + apply-schema (drop+create v2)
make verify           # lint + unit + integration тесты
make test-unit        # только unit-тесты
make test-integration # integration с реальной БД из docker-compose

# Тесты и линтинг
pytest tests/ -x --timeout=30             # полный набор
pytest tests/integration -q               # только интеграционные (нужна БД)
ruff check .                              # линтер
ruff format .                             # форматирование
cd services/browser-agent && npm test     # тесты browser-agent (TypeScript)

# Схема БД (v2)
python scripts/backup_secrets.py          # бэкап Vision/TG токенов (encrypted)
python scripts/apply_v2_schema.py --confirm-drop  # DROP + CREATE с нуля
python scripts/restore_secrets.py          # вернуть токены
```

## Architecture

**FB Stop Bot** — мониторит Facebook Ads, оценивает стоп-правила, шлёт алерты в Telegram, автоматически отключает объявления, создаёт новые кампании. Real-time часть работает через anti-detect браузер (Vision + Playwright + Node.js gRPC). Marketing API добавляется для latency-tolerant операций (см. `META_INTEGRATION_PLAN.md`).

### Десять Python воркеров + Node.js gRPC

После v2-миграции (см. `DB_REDESIGN.md`) кодовая база сокращена. Удалены: legacy ORM, observer god-таблицы, FastAPI роутеры, creator workers. Восстановим инкрементально по запросу.

**Python воркеры (текущие, все на v2 схеме):**

1. **observer_worker** (`apps/observer_worker/`) — бесконечный цикл: gRPC `RunScanCycle` → `ScannedAdRow[]` → process_scan_rows (FSM в `ad_alert_state` + метрики в `ad_metrics` partitioned + outbox в `task_queue`) → dispatch alerts в TG (через `core.telegram.alert_dispatcher`). Heartbeat и runtime status — в Redis (`observer:runtime` с TTL 60s). Pubsub `fb_agent:scan:finished`. Точка входа: `run_observer_worker.py`.
2. **disable_worker** (`apps/disable_worker/`) — поллит `task_queue` где `task_type='disable'` (FOR UPDATE SKIP LOCKED), вызывает gRPC `toggle_ad(target_state=False)`, retry с exponential backoff (30s → 5min cap, max 5 попыток). Точка входа: `run_disable_worker.py`.
3. **enable_worker** (`apps/enable_worker/`) — аналогично disable, но `task_type='enable'` и `target_state=True`. Точка входа: `run_enable_worker.py`.
4. **telegram_poller** (`apps/telegram_poller/`) — long-polling Telegram Bot API. Команды: `/start [code]` (consume invite), `/help`, `/spy <slot> <country>` (Ad Library pipeline). Inline-кнопки `dis:`, `snz:` под алертами → создают `task_queue` запись или ставят `ad_alert_state.snoozed_until`. Точка входа: `run_telegram_poller.py`.
5. **cleanup_worker** (`apps/cleanup_worker/`) — раз в сутки в 04:00 UTC: DROP старых партиций, DELETE по retention из `system_config.retention_policy`, чистка orphan ad_library media файлов, CREATE next-month партиций. Точка входа: `run_cleanup_worker.py`.
6. **reconciler_worker** (`apps/reconciler_worker/`) — каждые 30 сек: переводит `task_queue.status='running'` старше 30 минут → `retrying` (защита от крашнутых воркеров), отменяет `draft` старше 24 часов. Точка входа: `run_reconciler_worker.py`.
7. **meta_api_worker** (`apps/meta_api_worker/`) — поллит `task_queue` где `task_type='meta_api_mutation'`. На Этапе 5 диспетчеризует mutations через `dispatch_mutation` → `core/meta_api/mutations/*` поверх универсального `ExecuteGraphCall`. Eager-init `AuditedMetaApiClient` в `main_loop`. Маршрутизация ошибок: `Permanent/TokenInvalid/NotFound/Permission/NotImplemented/ValueError → mark_failed`; `RateLimited/Temporary/SessionUnavailable → requeue` (exponential backoff). Heartbeat `worker:heartbeat:meta_api` TTL 60s. Точка входа: `run_meta_api_worker.py`.
8. **health_watchdog** (`apps/health_watchdog/`) — раз в 60 сек проверяет `worker:heartbeat:*` в Redis. Если воркер из `EXPECTED_WORKERS` (env CSV) не дышит — алерт в TG через `core.telegram.client`. Дедуп через `health:alerted:{worker}` TTL 3600 (атомарный SET NX EX, не задвоит при параллельном запуске). Дополнительно проверяет `observer:runtime` freshness (>5 мин → отдельный алерт). Если `telegram_config` пуст — работает silent + дедуп всё равно ставится (защита от шквала при появлении токена). Точка входа: `run_health_watchdog.py`.
9. **enable_recommendation_worker** (`apps/enable_recommendation_worker/`) — раз в 5 мин ищет ads в state `stop_sent`/`disabled` старше cooldown (без `ad_auto_enable_disabled`), проверяет метрики после disable через `core/enable_reco/analyzer.should_recommend` (spend, cost_per_lead, cost_per_registration, deposits) → INSERT в `enable_recommendations` + TG-алерт с inline `ereco:<fb_ad_id>` → ручное подтверждение пользователем создаёт `task_queue` enable. Дедуп Redis `enable_reco:last:{ad_id}` TTL 6h (SET NX). Точка входа: `run_enable_recommendation_worker.py`.
10. **digest_scheduler** (`apps/digest_scheduler/`) — ежедневный TG-дайджест в 9:00 UTC через `core/telegram/digest_builder.py` (pure SQL-агрегации поверх `alert_events`, `task_queue`, `ad_metrics`, `offers`) + `digest_renderer.py` (HTML). Цикл `is_in_send_window` сверяет минуты от начала суток. Дедуп `digest:sent:YYYY-MM-DD` TTL 26ч в Redis. При `no_tg_config` флаг не ставится, при `no_recipients` — ставится. Точка входа: `run_digest_scheduler.py`.

**Node.js gRPC сервис (`services/browser-agent/`):**

- 8200+ строк TypeScript, gRPC порт 50051, три service'а:
  - `BrowserSessionService` — управление Vision-профилем и CDP (StartBrowser, StopBrowser, Reconnect, Navigate, StreamSessionStatus)
  - `ScannerService` — DOM-парсинг через `data-surface` атрибуты + scroll/refresh/toggle (25+ методов: RunScanCycle stream, ParseVisibleRows, ToggleAd, ValidateColumns, HumanMove, HardReloadPage, ...)
  - `CreatorService` — запись и выполнение планов создания кампаний (RunPlan stream, StartRecording, StopRecording)
- Python gRPC client в `clients/python_grpc/client.py` (751 строка) с circuit-breaker (3 фейла → OPEN 60с) и session-recovery (NOT_FOUND → автоматический restart)

### Core (`core/`)

- **domain.py** — enum'ы: `AlertStage` (warning/stop), `AlertState` (normal→warning_sent→stop_sent→claimed→disabled).
- **models/** — 35 SQLAlchemy 2.x ORM-моделей, разнесены по доменам (см. `DB_REDESIGN.md`):
  - `settings/` — observer_config, vision_config, telegram_config, system_config (singletons)
  - `catalog/` — offers, offer_rules, offer_rule_stats, fb_campaigns, fb_adsets, fb_ads
  - `observer/` — ad_alert_state (FSM), ad_metrics (partitioned), alert_events (partitioned), scan_runs (partitioned), cabinet_day_archives, ad_deposit_corrections, ad_auto_enable_disabled
  - `tasks/` — task_queue (unified outbox), enable_recommendations
  - `telegram/` — invites, recipients, message_refs
  - `creator/` — creator_plans
  - `ad_library/` — scan, ad, snapshot (partitioned), media, tier, report, winner_archive
  - `meta_api/` — observation, webhook_event (partitioned), audit_log (partitioned)
  - `trackers/` — postback (partitioned), aggregate
  - Все mixins (UUIDPrimaryKey, BigIntPrimaryKey, Timestamp, CreatedAtOnly, SingletonMixin) в `core/models/base.py`.
- **observer/** — `pipeline.py` (process_scan_rows: один scan-цикл), `queries.py` (load_active_offers, match_offer_for_ad с word-boundary regex, load_alert_state), `state_machine.py` (pure FSM: `decide(FsmInput) → FsmTransition`), `writers.py` (upsert catalog + insert_metrics + apply_fsm_transition + maybe_create_disable_task).
- **scanner/models.py** — frozen dataclass `ScannedAdRow` — главный контракт между TS-сканером и Python-pipeline. Парсер DOM целиком в TypeScript (`services/browser-agent/src/parser.ts`) — это не меняется.
- **rules/evaluator.py** — 6 стоп-правил с двухуровневой WARNING (80% от порога) / STOP логикой, спецлогика fast-stop, funnel-лесенка, frequency-anomaly. `evaluate_stop_rules(row, ctx) → RuleEvaluation` (warning_hits + stop_hits).
- **tasks/queue.py** — unified API для `task_queue`: `create_task`, `claim_next_task` (FOR UPDATE SKIP LOCKED), `mark_succeeded`, `requeue_for_retry` (exponential backoff), `reconcile_stuck_running`, `cancel_stale_drafts`. Все 5 типов outbox (`disable`, `enable`, `plan_run`, `meta_api_mutation`, `ad_library_scan`) обслуживаются одной таблицей.
- **tasks/toggle_executor.py** — общий движок для disable/enable воркеров: `execute_one_toggle_task` + `run_toggle_loop` (claim → toggle → mark, error recovery, gate reconnect).
- **telegram/** — `client.py` (TG Bot API через httpx, не зависит от ORM), `service.py` (load_telegram_config, find_recipient, consume_invite), `bot_handler.py` (минимальный: /start /help /spy + callback'и под алертами), `renderer.py` (форматирование алертов с inline-кнопками `dis:`/`snz:`), `alert_dispatcher.py` (отправка алертов из alert_events с дедупом через telegram_message_refs), `messaging.py`.
- **ad_library/** — Ad Library pipeline (см. `DB_REDESIGN.md` §6.7): `scanner.py` (gRPC к browser-agent), `classifier.py` (vertical + relevance к slot), `media.py` (downloader через httpx), `enricher.py` (hook/cta/tone heuristic), `tier_ranker.py` (S/A/B/C), `report.py` (markdown), `pipeline.py` (orchestrator), `spy_handler.py` (parse /spy args).
- **meta_api/** — Python-обвязка над gRPC MetaApiService browser-agent: `client.py` (`MetaApiClient` + `AuditedMetaApiClient`), `schemas.py` (frozen `MetaApiAdRow`/`MetaInsightsRow`/`MetaMutationPayload`), `errors.py` (классификация Graph error codes → `TokenInvalidError`/`RateLimitedError`/`NotFoundError`/...), `adapters.py` (`MetaApiAdRow → ScannedAdRow`), `audit.py` (запись в `meta_api_audit_log`, partitioned), `queue.py` (outbox-обёртка для `task_type='meta_api_mutation'` + `default_idempotency_key`), `reconciler.py` (stuck running / stale drafts), `insights/fetcher.py` (`InsightsFetcher` с пагинацией), **`mutations/`** (Этап 5 — 8 handlers: `pause_ad`/`activate_ad`/`pause_campaign`/`activate_campaign`/`set_adset_budget`/`duplicate_campaign`/`bulk_status_change`/`create_campaign`) + `dispatch_mutation`. Все mutations через универсальный `ExecuteGraphCall`. Marketing API не шлётся через httpx — только через page.evaluate(fetch) изнутри Vision-сессии. См. `META_INTEGRATION_PLAN.md` §3-5.
- **enable_reco/** — pure-функция `should_recommend` (FsmInput-подобный анализ метрик после disable) + `render_recommendation_alert` (HTML + inline `ereco:`). Используется `enable_recommendation_worker`.
- **telegram/digest_builder.py + digest_renderer.py** — pure-агрегации `build_digest(engine, day_start_utc)` поверх partitioned-таблиц (обязательная фильтрация по партиционному ключу) и HTML-рендер для ежедневного дайджеста.
- **campaign_recorder/** — запись пользовательских действий в браузере → JSON план (для creator workers, которые сейчас не активны).
- **creator_bridge/** — мост между Python и TS-bundle на странице (через `add_init_script` + `window.fbAgentEmit`).
- **creatives/** — `uniquify_creatives` (водяной знак), `folder_opener`.
- **campaign_scripts/planner.py** — декларативный план для ручного создания кампании.
- **campaign_creator/** — фабрика создания кампаний (Vision-based, не active в текущем сборке).
- **ai_assistant/** — pure-Python ассистент: `chat.py`, `client.py`, `providers.py`, `prompts/`. Пакет `tools/` (registry + base + ops/meta/drafts/creative — 15 tools) подключён к Telegram через `core/telegram/ai_handlers.py` (`/ask` + draft callbacks `dr_ok`/`dr_cancel`). `ToolHandler.risk_level`: READ_ONLY (исполняется немедленно), DRAFT_REQUIRED (создаёт `task_queue` со `status='draft'` через `core.meta_api.queue.create_draft_task` → юзер подтверждает в TG), CREATIVE. Rate-limit per `client_key` через `tools/_ratelimit.py` (Redis `ai:ratelimit:tools:*` TTL 3600, fail-open). `MetaApiClient` пробрасывается через `ToolContext` — без него meta-tools падают с явной ошибкой.
- **adset_pro/** — минимальный REST-клиент трекера AdSet.pro (Этап 6 подготовка): `AdsetProClient` (async httpx + Bearer + tenacity retry на 5xx/429/transport), schemas (`StatsQueryRequest/Response`, `ConversionRow.from_api_row` парсит `ext_sub6 → fb_ad_id`, `PostbackEvent` на будущее), errors (`AuthError`/`NotFoundError`/`RateLimitedError`/`TemporaryError`/`PermanentError` + `classify_http_error`). Ключ `ADSETPRO_MCP_KEY` в `.env`, `adsetpro_base_url`/`adsetpro_timeout_seconds` в `core/config.py`. Endpoint `POST /api/stats/query` и auth `Bearer` помечены `TODO(stage-6)` — нужно verify на живом API. Без postback FastAPI и БД-таблиц (отдельная волна).
- **alerts/** — Redis-очередь алертов + drain worker (опционально).
- **browser/** — `lock.py` (file-lock эксклюзивности браузер-сессии), `circuit_breaker.py` (AsyncCircuitBreaker для gRPC).
- **auth/** — TMA initData валидация (для будущего Mini App).
- **db/** — get_engine + session_factory (используется опционально).
- **config.py** — pydantic-settings из .env, синглтон `get_settings()`.
- **crypto.py** — Fernet-шифрование (Vision token, Telegram bot token). `rotate_encryption_key` использует raw SQL по telegram_config/vision_config (не зависит от ORM).
- **pubsub.py** — Redis pubsub (`fb_agent:scan:finished`, `fb_agent:alert:created`, `fb_agent:task:changed`).

### Redis (вместо БД-таблиц)

- `worker:heartbeat:<name>` (TTL 60s) — пишут все воркеры
- `observer:runtime` (TTL 60s) — JSON со статусом observer
- `ai:cache:*` (TTL 300-900s) — кэш AI-ответов (когда восстановим AI assistant)

### Что временно отсутствует (восстановим по запросу)

После v2-миграции удалены, но могут быть восстановлены инкрементально:
- **API роутеры** (`apps/api/`) — 17 роутеров FastAPI. Понадобится для фронта.
- **Creator workers** (`apps/creator_worker/`, `apps/creator_recorder/`) — автоматизация создания кампаний через Vision.
- **Meta API расширенные mutations** — Этап 5 дал 8 базовых handlers через `ExecuteGraphCall`. Не реализовано: полный `create_campaign` (campaign + adset + ad + creative в одной Batch-транзакции), `UploadImage`/`UploadVideo` (chunked upload — требует расширения proto потоковым RPC), Custom Audiences, `SetAdCreative` (замена creative у существующего ad), полный rename в `duplicate_campaign`.
- **Backtest** (`scripts/backtest_rules.py`) — пройти историю и оценить false-stop'ы.

### Будущие модули (см. META_INTEGRATION_PLAN.md + DB_REDESIGN.md)

В v2-схеме уже подготовлены таблицы (см. `core/models/`):
- **meta_api/** — `meta_api_observation` (latency-tolerant snapshot, UNIQUE по ad_id), `meta_api_webhook_event` (partitioned, задел — webhooks без Admin BM не работают), `meta_api_audit_log` (partitioned, retention 30 дней). Outbox-канал — `task_queue.task_type='meta_api_mutation'`, обслуживает meta_api_worker.
- **trackers/** — `tracker_postback` (partitioned, AdsetPro schema), `tracker_aggregate` (per ad_id × country × day). Webhook handler + aggregator не написаны.
- **ad_library_winner_archive** — топ S-tier ads hold forever (защита от cleanup).

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
