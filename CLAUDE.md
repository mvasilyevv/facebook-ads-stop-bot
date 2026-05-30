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
python run_cabinet_scheduler.py                                          # Cabinet autostart scheduler (enable по дате + scan)
python run_tracker_aggregator_worker.py                                  # Tracker aggregator (adsetpro_postback_events → tracker_aggregate per ad×country×day)
python run_creator_worker.py                                             # Creator worker (Vision fallback для plan_run)
python run_creator_recorder.py                                           # Creator recorder (запись планов через CDP)
python run_api.py                                                        # FastAPI на 8000 (health + AdSet.pro postback)
python run_meta_api_worker.py                                            # Marketing API mutations worker (skeleton до Этапа 5)
python run_health_watchdog.py                                            # Health watchdog (мониторинг worker:heartbeat:*)

# Через Makefile
make bootstrap        # docker + зависимости + apply-schema (drop+create)
make verify           # lint + unit + integration тесты
make test-unit        # только unit-тесты
make test-integration # integration с реальной БД из docker-compose

# Тесты и линтинг
pytest tests/ -x --timeout=30             # полный набор
pytest tests/integration -q               # только интеграционные (нужна БД)
ruff check .                              # линтер
ruff format .                             # форматирование
cd services/browser-agent && npm test     # тесты browser-agent (TypeScript)

# Схема БД
python scripts/backup_secrets.py          # бэкап Vision/TG токенов (encrypted)
python scripts/apply_schema.py --confirm-drop  # DROP + CREATE с нуля
python scripts/restore_secrets.py          # вернуть токены
```

## Architecture

**FB Stop Bot** — мониторит Facebook Ads, оценивает стоп-правила, шлёт алерты в Telegram, автоматически отключает объявления, создаёт новые кампании. Real-time часть работает через anti-detect браузер (Vision + Playwright + Node.js gRPC). Marketing API добавляется для latency-tolerant операций (см. `META_INTEGRATION_PLAN.md`).

### 14 Python воркеров + FastAPI + Node.js gRPC

После миграции (см. `DB_REDESIGN.md`) кодовая база сокращена и постепенно восстановлена. Сейчас активные сервисы покрывают почти весь функционал, кроме фронта (отложен).

**Python воркеры (текущие, все на схеме):**

1. **observer_worker** (`apps/observer_worker/`) — бесконечный цикл: gRPC `RunScanCycle` → `ScannedAdRow[]` → process_scan_rows (FSM в `ad_alert_state` + метрики в `ad_metrics` partitioned + outbox в `task_queue`) → dispatch alerts в TG (через `core.telegram.alert_dispatcher`). **Owner-scoping:** если задан `observer_config.owner_campaign_tag` (один или несколько тегов через запятую, напр. `MV` или `MV,ABC`), кампании без любого из тегов (word-boundary, через `campaign_matches_owner` + `parse_owner_tags`) полностью игнорируются — защита от работы с чужими кампаниями в общем кабинете (NULL — фильтр выключен). **Канал авто-стопа** (`observer_config.act_via_api`, #39, дефолт `True`): `True` — `meta_api_mutation pause_ad` (Marketing API через meta_api_worker, точно по ad_id, не промахивается по кнопке) — основной канал; `False` — `task_type='disable'` (DOM-клик через disable_worker) — спящий резерв/фолбэк (disable_worker не выпилен). Detect через DOM в обоих случаях, меняется только act. Heartbeat и runtime status — в Redis (`observer:runtime` с TTL 60s). Pubsub `fb_agent:scan:finished`. **Прод gate-фабрика** (`_default_gate_factory`) собирает `BrowserAgentClient(BrowserAgentConfig)` из `get_settings()` + `start()` (ранее был баг: `BrowserAgentClient()` без config + `.connect()`). Точка входа: `run_observer_worker.py`.
2. **disable_worker** (`apps/disable_worker/`) — поллит `task_queue` где `task_type='disable'` (FOR UPDATE SKIP LOCKED), вызывает gRPC `toggle_ad(target_state=False)`, retry с exponential backoff (30s → 5min cap, max 5 попыток). Точка входа: `run_disable_worker.py`.
3. **enable_worker** (`apps/enable_worker/`) — аналогично disable, но `task_type='enable'` и `target_state=True`. Точка входа: `run_enable_worker.py`.
4. **telegram_poller** (`apps/telegram_poller/`) — long-polling Telegram Bot API. Команды: `/start [code]` (consume invite), `/help`, `/spy <slot> <country>` (Ad Library pipeline). Inline-кнопки `dis:`, `snz:` под алертами → создают `task_queue` запись или ставят `ad_alert_state.snoozed_until`. Точка входа: `run_telegram_poller.py`.
5. **cleanup_worker** (`apps/cleanup_worker/`) — раз в сутки в 04:00 UTC: DROP старых партиций, DELETE по retention из `system_config.retention_policy`, чистка orphan ad_library media файлов, CREATE next-month партиций. Точка входа: `run_cleanup_worker.py`.
6. **reconciler_worker** (`apps/reconciler_worker/`) — каждые 30 сек: переводит `task_queue.status='running'` старше 30 минут → `retrying` (защита от крашнутых воркеров, bump `attempt_count + 1` ровно один раз), отменяет `draft` старше 24 часов. Тонкая env-обёртка над каноническими `core.tasks.queue.reconcile_stuck_running` / `cancel_stale_drafts`. Точка входа: `run_reconciler_worker.py`.
7. **meta_api_worker** (`apps/meta_api_worker/`) — поллит `task_queue` где `task_type='meta_api_mutation'`. На Этапе 5 диспетчеризует mutations через `dispatch_mutation` → `core/meta_api/mutations/*` поверх универсального `ExecuteGraphCall`. Eager-init `AuditedMetaApiClient` в `main_loop`. Маршрутизация ошибок: `Permanent/TokenInvalid/NotFound/Permission/NotImplemented/ValueError → mark_failed`; `RateLimited/Temporary/SessionUnavailable → requeue` (exponential backoff). **FSM-sync** (`core/meta_api/fsm_sync.py::sync_fsm_after_mutation`, #39): после успешного `mark_task_succeeded` приводит `ad_alert_state` к результату mutation — `pause_ad`/`bulk pause`→`disabled`, `activate_ad`/`bulk activate`→`normal` (best-effort, идемпотентно через `reset_alert_state_after_*`). Закрывает money-пробел: без этого FSM застревал в `stop_sent` при auto-stop через API; попутно синхронизирует autostart-bulk activate. `bulk_status_change` трогает только `object_type='ad'`. Heartbeat `worker:heartbeat:meta_api` TTL 60s. Точка входа: `run_meta_api_worker.py`.
8. **health_watchdog** (`apps/health_watchdog/`) — раз в 60 сек проверяет `worker:heartbeat:*` в Redis. Если воркер из `EXPECTED_WORKERS` (env CSV) не дышит — алерт в TG через `core.telegram.client`. Дедуп через `health:alerted:{worker}` TTL 3600 (атомарный SET NX EX, не задвоит при параллельном запуске). Дополнительно проверяет `observer:runtime` freshness (>5 мин → отдельный алерт). Если `telegram_config` пуст — работает silent + дедуп всё равно ставится (защита от шквала при появлении токена). Точка входа: `run_health_watchdog.py`.
9. **enable_recommendation_worker** (`apps/enable_recommendation_worker/`) — раз в 5 мин ищет ads в state `stop_sent`/`disabled` старше cooldown (без `ad_auto_enable_disabled`), проверяет метрики после disable через `core/enable_reco/analyzer.should_recommend` (spend, cost_per_lead, cost_per_registration, deposits) → INSERT в `enable_recommendations` + TG-алерт с inline `ereco:<fb_ad_id>` → ручное подтверждение пользователем создаёт `task_queue` enable. Дедуп Redis `enable_reco:last:{ad_id}` TTL 6h (SET NX). Точка входа: `run_enable_recommendation_worker.py`.
10. **digest_scheduler** (`apps/digest_scheduler/`) — ежедневный TG-дайджест в 9:00 UTC через `core/telegram/digest_builder.py` (pure SQL-агрегации поверх `alert_events`, `task_queue`, `ad_metrics`, `offers`; `_count_active_ads_normal` фильтрует по `last_seen_at >= NOW() - 7d`, иначе счётчик растёт вечно) + `digest_renderer.py` (HTML). `is_in_send_window`: окно от `target` до конца суток UTC (catch-up при downtime воркера в момент 9:00); Redis-ключ `digest:sent:YYYY-MM-DD` TTL 26ч блокирует повтор внутри суток. При `no_tg_config` флаг не ставится, при `no_recipients` — ставится. Точка входа: `run_digest_scheduler.py`.
11. **creator_worker** (`apps/creator_worker/`) — поллит `task_queue` где `task_type='plan_run'`, грузит план из `creator_plans` (только `is_archived=false`), стримит `CreatorService.RunPlan` через `BrowserAgentClient`, агрегирует 6 типов `PlanEvent` (started/finished/failed/skipped/checkpoint/complete) в `task_queue.result`. Маршрутизация: `ValueError/NotImplementedError/KeyError → mark_failed`; `BrowserUnavailable/Timeout/grpc.RpcError → requeue`. Heartbeat `worker:heartbeat:creator` TTL 60s. Vision-fallback для gambling-вертикалей (когда Meta зарезает креативы через API content review). Точка входа: `run_creator_worker.py`.
12. **creator_recorder** (`apps/creator_recorder/`) — подписка на pubsub-каналы `fb_agent:creator:record_start`/`record_stop`. По event'у — `StartRecording`/`StopRecording` через `BrowserAgentClient`, парсит план и INSERT в `creator_plans` (с UTC-suffix retry при конфликте по `uq_creator_plans_name_active`). Heartbeat `worker:heartbeat:creator_recorder`. Точка входа: `run_creator_recorder.py`.
13. **cabinet_scheduler** (`apps/cabinet_scheduler/`) — **money-критичный** автостарт кабинета по расписанию. Раз в минуту проверяет окно (HH:MM UTC из `system_config` key=`cabinet_autostart`, catch-up до конца суток как digest). В окне: дедуп через Redis `cabinet:autostart:YYYY-MM-DD` TTL 26ч (`SET NX`) → owner-scoped резолв активных ad по ДАТЕ в названии кампании (`core/meta_api/bulk.py::resolve_owner_ad_ids_by_dates`, word-boundary `(^|[^0-9])DATE([^0-9]|$)` + `campaign_matches_owner`) → создаёт сразу `pending` `bulk_status_change activate` (idempotency_key=`autostart:{day}:{action}` — двойная защита от дубля) → publish `fb_agent:observer:trigger`. **Безопасность:** пустой список дат → ничего не включаем (НЕ весь кабинет). Конфиг меняется без рестарта через TG `/autostart` (`core/telegram/handlers/autostart.py`). Heartbeat `worker:heartbeat:cabinet_scheduler` TTL 60s. Точка входа: `run_cabinet_scheduler.py`.
14. **tracker_aggregator_worker** (`apps/tracker_aggregator_worker/`, #BL-8 Волна 4) — раз в N минут (env `TRACKER_AGGREGATOR_INTERVAL_SECONDS`, дефолт 300с) пересчитывает `tracker_aggregate` per (ad_id, country, day) из partitioned `adsetpro_postback_events`. Pure-функция `core/adset_pro/aggregator.py::aggregate_postback_events` — **absolute recompute** целых UTC-дней, перекрытых окном `[now - lookback, now]` (lookback дефолт 2ч, переживает полночь): для каждого (ad, country, day) пишет АБСОЛЮТНЫЕ суточные суммы через `ON CONFLICT DO UPDATE SET=пересчёт` (не += инкремент) → повторный/перекрывающийся прогон **не задваивает деньги** (урок Round 10/11). Фильтр по `received_at` (партиционный ключ → pruning). Исключает: `fb_ad_fk IS NULL`, `is_duplicate=TRUE`, строки без валидного ISO-2 `country` (из `raw_json->>'country'/'country_code'/'geo'`). `deposits` переиспользует `DEPOSIT_EVENT_TYPES` из `queries.py` — единый контракт с evaluator'ом. `roi_percent` не считается (спенд без разреза по country). Heartbeat `worker:heartbeat:tracker_aggregator` TTL 60s, аудит в `system_config.tracker_aggregator_runs`. Точка входа: `run_tracker_aggregator_worker.py`.

**FastAPI (`apps/api/`):** через `create_app()` factory + lifespan:
- `GET /healthz` — k8s liveness, всегда 200 без БД.
- `GET /readyz` — readiness с TTL-кэш 5с (`SELECT 1` + Redis `PING`), 200/503.
- `GET /metrics` — Prometheus exposition (`app_requests_total{path,method,status}`, `app_request_duration_seconds{path,method}`, лейбл `path` — route template, не raw URL).
- `POST /api/v1/postback/adsetpro` — приём postback'а от AdSet.pro (Этап 6). Auth через `secrets.compare_digest(x_postback_secret or "", ADSETPRO_POSTBACK_SECRET)` — timing-safe. Если секрет пуст → 503 (явный отказ).
- **`apps/api/routers/v1/`** — пакет с auto-discovery. `register_all(app)` через `pkgutil.iter_modules` находит все модули с атрибутом `router: APIRouter` и подключает их с `prefix="/api"`. Новые роутеры просто кладутся в эту папку — без правок `main.py`. После Round 7.1-7.7 закрыт 61 endpoint в 17 модулях:
  - **Round 7.1 — Settings + Observer + Health:**
    - `settings_observer.py` — `GET/PUT /settings/observer`, `PATCH /settings/observer/scanning`, `PATCH /settings/observer/auto-enable`, `PATCH /settings/observer/act-via-api` (#39 — money-флаг канала toggle; в `PUT` поле `act_via_api` опционально, `None`=не трогать), `POST /settings/observer/scan-now` (Redis publish в `fb_agent:observer:trigger`).
    - `settings_telegram.py` — `GET /settings/telegram` с compute-полями (`is_authorized`, `poller_status`, `bot_username`, `auth_deep_link`) через `core/telegram/settings_compute.py` (Redis-cache TTL 1h для bot_username), `PUT /token`, `DELETE`, `GET/DELETE /recipients`, `POST /recipients/invite`.
    - `settings_vision.py` — `GET/PUT /settings/vision`, `POST /vision/reconnect` (gRPC к BrowserSessionService), `GET /vision/profiles` (501 stub — пока нет в proto).
    - `observer.py` — `GET /observer/status` (Redis `observer:runtime`), `GET /observer/scan-runs` (partitioned WHERE по started_at, фильтры `all/errors/slow/with_alerts`, limit cap 200), `POST /observer/start-new-cabinet-day`, `POST /observer/restart`, `POST /disable-worker/restart` (все publish в Redis-каналы).
    - `health_details.py` — `GET /health/details` (SCAN MATCH `worker:heartbeat:*` → ONLINE/OFFLINE, overall HEALTHY/DEGRADED/CRITICAL).
  - **Round 7.2 — Offers:**
    - `offers.py` — `GET /offers` (`?include_inactive=true`), `GET /offers/compare?days=N` (агрегация Offer+AdMetrics+AlertEvent через FK chain, partitioned WHERE), `POST/PUT/DELETE /offers/{id}` (soft delete `is_active=false`, code immutable), `GET/PUT /offers/{id}/rules`.
  - **Round 7.3 — Ads/FSM core + Tasks helpers:**
    - `dashboard.py` — `GET /dashboard/ads` (composite через `core.dashboard.build_ad_snapshot`, `X-Total-Count` header), `GET /dashboard/alerts` (partitioned AlertEvent default 24h, поля `stage`/`matched_rule_codes`/`ad_name` через JOIN), `GET /dashboard/incidents` (active warning_sent/stop_sent + `incident_duration_seconds`/`transitions_count` через batch-unnest без N+1).
    - `ads_timeline.py` — `GET /ads/{fb_ad_id}/timeline?from_iso&to_iso&include_metrics&include_alerts&include_tasks` (multi-source: AdMetrics + AlertEvent + TaskQueue payload JSONB-фильтр, partitioned WHERE).
    - `fake_deposits.py` — `GET /fake-deposits`, `PUT/DELETE /fake-deposits/{fb_ad_id}` (UPSERT через `AdDepositCorrection`).
    - `auto_enable.py` — `GET/POST/DELETE /dashboard/auto-enable-disabled/{fb_ad_id}` (флаг `AdAutoEnableDisabled` против auto-recommend recovery).
  - **Round 7.4 — Tasks/outbox:**
    - `disable_tasks.py` — `GET /dashboard/disable-tasks?status=PENDING,FAILED&fb_ad_id&limit&offset` (JOIN FbAd для ad_name, `?status=PENDING` разворачивается в `['draft','pending']` — draft скрыт от фронта), `POST` (create через `core.tasks.queue.create_task`), `POST /{id}/retry` (failed/cancelled → retrying, 409 если активная), `DELETE /{id}` (soft cancel).
    - `enable_tasks.py` — `GET /dashboard/enable-tasks` (тот же shape, `task_type='enable'`).
    - `enable_recommendations.py` — `GET /dashboard/enable-recommendations?status=PENDING|PROMOTED` (LEFT JOIN TaskQueue по `promoted_to_task_id`), `POST /{id}/enable` (atomic INSERT task_queue + UPDATE `promoted_to_task_id` в одной транзакции).
  - **Round 7.5 — Dashboard aggregations:**
    - `dashboard_stats.py` — `GET /dashboard/stats` (14 scalar полей: counts FbAd/alert_state + scan stats + observer_status из Redis + pending/failed tasks, fail-all asyncio.gather), `GET /dashboard/batch` (композит stats+incidents+alerts+disable+enable_recommendations в одном fetch'е, **partial-failure** через `_safe_call`-обёртку — упавшая секция возвращает empty default, остальные ОК).
    - `dashboard_timeseries.py` — `GET /dashboard/spend-history?hours&fb_ad_id` (сырые точки `ad_metrics`, default 24h, max 168h, без `fb_ad_id` — limit 10000), `GET /dashboard/chart-data?hours&bucket=hour|day` (SUM по `date_trunc` + COUNT DISTINCT ad_id; пустые бакеты не появляются — Recharts сам обрабатывает разрывы).
    - `dashboard_performance.py` — `GET /dashboard/performance?days&limit_*` (3 параллельных CTE через asyncio.gather: top_campaigns с `cost_per_lead = SUM/NULLIF`, offer_leaderboard через LEFT JOIN alerts_per_offer, top_rule_violations через `jsonb_array_elements_text(matched_rule_codes)` — **matched_rule_codes хранится как JSONB, не TEXT[]!**).
  - **Round 7.6 — History:**
    - `history.py` — `GET /history/summary?from_iso&to_iso` (default last 30d, max range 90d → 422; композитная агрегация spend/impressions/clicks/leads/regs/deposits + alerts по stage + by_rule через `jsonb_array_elements_text` + tasks по terminal-статусам), `GET /history/timeline` (UNION ALL AlertEvent + terminal TaskQueue DESC), `GET /history/campaigns` (GROUP BY FbCampaign + alerts_count через `LEFT JOIN ... al`-subquery), `GET /history/events` (drill-down AlertEvent с фильтрами campaign_id/fb_ad_id/stage), `GET /history/offers` (GROUP BY Offer через FbCampaign.offer_id), `GET /history/ads` (GROUP BY FbAd + **LATERAL** для `last_alert_at`/`last_disable_at`). **Asyncpg quirk:** `::uuid` cast в параметризованных `text()` не поддерживается — UUID конвертируется в Python-объект до передачи в params.
  - **Round 7.7 — Tools + AI (финальный):**
    - `tools.py` — `POST /tools/creative-uniquify` (multipart, list[CreativeInput] bytes → `core.creatives.service.uniquify_creatives(offer_name, copies, creatives, base_dir, now)` → output в `~/Documents/FB_Agent_Creo`), `POST /tools/creative-uniquify/open-folder` (валидация через `default_creatives_root()` — 403 если path вне корня), `GET /tools/campaign-create/folders` (`list_creative_folders()` возвращает `adset_count/creative_count/media_type` — НЕ `files_count/size_bytes`), `POST /tools/campaign-create/plan` (`inspect_creative_folder()` + `build_campaign_script_plan(folder, config)`). **dev-only** (warning-комментарии в docstrings, prod-блокировка через env не добавлена — оставлено как backlog).
    - `ai_analyze.py` — `POST /ai/analyze` с Redis-кэшем `ai:cache:analyze:{block_type}:{scope_key}` TTL 600s + отдельный rate-limiter 20/hour per remote IP (поверх `ChatSession`'овского 30/hour). 503 если AI-провайдеры не настроены. `force_refresh=true` обходит кэш.
- **`apps/api/deps.py`** — `DepEngine`, `DepRedis`, `DepSettings` через `Annotated[..., Depends(...)]` для роутеров v1.
- **`apps/api/utils/status_mapper.py`** — `to_frontend_task_status` / `from_frontend_task_status` (lowercase БД ↔ uppercase frontend, `draft → PENDING`).
- **`apps/api/utils/partition.py`** — `default_window(hours=168)` для partitioned-queries.
- `RequestIdMiddleware` echo'ит `X-Request-Id`. `BodySizeLimitMiddleware` — 64 KB hard cap по `Content-Length` → 413 (GET/HEAD/OPTIONS пропускаются). CORS — только если `frontend_origin` задан; при `"*"` в origin'е (включая комбинации типа `"https://app.com,*"`) `create_app()` падает `RuntimeError` на старте. Exception handlers маппят `AdsetProError`/`MetaApiError` подтипы на 401/403/404/429/503/502.
- **TODO subscriber'ы в worker'ах:** observer не подписан на `fb_agent:observer:trigger`/`cabinet_day`, не подписаны worker'ы на `fb_agent:worker:restart:*`. Endpoints publish'ат сигналы, до реализации subscriber'ов сигналы no-op. Отдельная стич-задача после Round 7.x.
- Точка входа: `run_api.py` или `make api` (uvicorn на 8000).

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
- **observer/** — `pipeline.py` (process_scan_rows: один scan-цикл), `queries.py` (load_active_offers, `match_offer_for_ad` — word-boundary regex с явным alphabetical tie-breaker при равной длине, load_alert_state), `state_machine.py` (pure FSM: `decide(FsmInput) → FsmTransition`; `open_token` сохраняется при `warning_sent→stop_sent`, генерируется заново только при старте incident'а `normal→warning|stop` — старые inline-кнопки `dis:`/`snz:` остаются валидными), `writers.py` (upsert catalog + insert_metrics + `apply_fsm_transition` с `WHERE alert_state NOT IN ('claimed', 'disabled')` в `ON CONFLICT DO UPDATE` против затирания терминальных состояний concurrent observer'ом + maybe_create_disable_task + `_begin_scan_run` через CTE для атомарной записи scan_id и id одной транзакцией).
- **scanner/models.py** — frozen dataclass `ScannedAdRow` — главный контракт между TS-сканером и Python-pipeline. Парсер DOM целиком в TypeScript (`services/browser-agent/src/parser.ts`) — это не меняется.
- **rules/evaluator.py** — 7 стоп-правил с двухуровневой WARNING (80% от порога) / STOP логикой, спецлогика fast-stop, funnel-лесенка, **frequency-anomaly** (правило 7, #37 — opt-in per-offer через `offer.frequency_threshold`; `build_rule_context` прокидывает frequency/impressions/reach в `RuleContext` ТОЛЬКО при заданном пороге, иначе guardrail-поведение CPC/CPL/CPR неизменно; фаза 1 — абсолютный порог, без `frequency_1h_ago`). **Data-driven порог** (`core/rules/frequency_analyzer.py`, #37): `compute_frequency_threshold` ищет порог из истории `ad_metrics` (бакеты частоты → деградация `cost_per_result` относительно baseline низкой частоты, медиана устойчива к выбросам), `apply_recommended_threshold` пишет в `offer_rules.frequency_threshold` с `dry_run`-защитой и ТОЛЬКО в NULL (ручное не затирает); бессилен на пустой `ad_metrics` — нужны накопленные данные. `evaluate_stop_rules(row, ctx) → RuleEvaluation` (warning_hits + stop_hits).
- **tasks/queue.py** — unified API для `task_queue`: `create_task` (поддерживает `created_by_chat_id` для owner ACL), `claim_next_task` (FOR UPDATE SKIP LOCKED), `mark_succeeded`/`mark_failed`/`requeue_for_retry` — все возвращают `bool` (True=update применился, False=race с другим воркером через `WHERE status='running'`-guard, защита от двойного исполнения после reconciler-race), `reconcile_stuck_running` (канонический, инкрементирует `attempt_count`), `cancel_stale_drafts`, `approve_draft_task` (сверяет `created_by_chat_id` или требует `admin_override=True`). Все 5 типов outbox (`disable`, `enable`, `plan_run`, `meta_api_mutation`, `ad_library_scan`) обслуживаются одной таблицей.
- **tasks/toggle_executor.py** — общий движок для disable/enable воркеров: `execute_one_toggle_task` + `run_toggle_loop` (claim → toggle → mark, error recovery, gate reconnect). При `mark_succeeded=False` (race с reconciler-zombie) — warning log + пропуск `reset_alert_state_after_*_succeeded` (победитель уже сделал).
- **telegram/** — `client.py` (TG Bot API через httpx, не зависит от ORM), `service.py` (load_telegram_config, find_recipient, consume_invite), `bot_handler.py` (минимальный: /start /help /spy + callback'и под алертами), `renderer.py` (форматирование алертов с inline-кнопками `dis:`/`snz:`), `alert_dispatcher.py` (pre-claim паттерн: `INSERT ... ON CONFLICT DO NOTHING RETURNING id` с sentinel `message_id=0` ДО `sendMessage` → при NULL skip без send'а, иначе send + UPDATE реальным id, при ошибке DELETE pre-claim для retry; защита от дубля TG-сообщений в SELECT/INSERT race), `messaging.py`.
- **ad_library/** — Ad Library pipeline (см. `DB_REDESIGN.md` §6.7): `scanner.py` (gRPC к browser-agent), `classifier.py` (vertical + relevance к slot), `media.py` (downloader через httpx), `enricher.py` (hook/cta/tone heuristic), `tier_ranker.py` (S/A/B/C), `report.py` (markdown), `pipeline.py` (orchestrator), `spy_handler.py` (parse /spy args).
- **meta_api/** — Python-обвязка над gRPC MetaApiService browser-agent: `client.py` (`MetaApiClient` + `AuditedMetaApiClient`), `schemas.py`, `errors.py` (классификация Graph error codes), `adapters.py` (`MetaApiAdRow → ScannedAdRow`), `audit.py`, `queue.py` (outbox-обёртка для `task_type='meta_api_mutation'` + `create_draft_task(created_by_chat_id=...)` + `approve_draft_task` с ACL по owner или `admin_override=True` для admin-recipient'а), `reconciler.py`, `insights/fetcher.py`, **`mutations/`** — 10 handlers (`pause_ad`/`activate_ad`/`pause_campaign`/`activate_campaign`/`set_adset_budget` с hard cap $100k daily / $1M lifetime/`duplicate_campaign` с atomic rename через Batch API/`bulk_status_change` с warning-логом первого id + caller-side type guard в docstring/`create_campaign` full через Batch API кампания+адсет+креатив+ад с JSONPath refs/`custom_audience` CUSTOM+LOOKALIKE с ratio-валидацией/`set_ad_creative` замена creative у существующего ad) + `_batch_helpers.py` (`build_batch_payload`, `parse_batch_response`, `jsonpath_ref` + custom `_encode_value` form-encoder — кодирует только разделители `&+space%#\r\n`, оставляет `{}:$.=` нетронутыми, чтобы JSONPath refs `{result=campaign:$.id}` доходили до Meta без URL-escape'а). **`upload.py`** — `MediaUploader` поверх client-streaming RPC `UploadVideo` + unary `UploadImage` (chunked resumable 4MB chunks; либо bytes через multipart, либо URL без multipart — Meta скачивает сама; state в `VideoUploadSession` внутри замыкания одного gRPC-вызова). Все mutations через универсальный `ExecuteGraphCall`. Marketing API не шлётся через httpx — только через page.evaluate(fetch) изнутри Vision-сессии. См. `META_INTEGRATION_PLAN.md` §3-5.
- **enable_reco/** — pure-функция `should_recommend` (FsmInput-подобный анализ метрик после disable) + `render_recommendation_alert` (HTML + inline `ereco:`). Используется `enable_recommendation_worker`.
- **telegram/digest_builder.py + digest_renderer.py** — pure-агрегации `build_digest(engine, day_start_utc)` поверх partitioned-таблиц (обязательная фильтрация по партиционному ключу) и HTML-рендер для ежедневного дайджеста.
- **campaign_recorder/** — запись пользовательских действий в браузере → JSON план (для creator workers, которые сейчас не активны).
- **creator_bridge/** — мост между Python и TS-bundle на странице (через `add_init_script` + `window.fbAgentEmit`).
- **creatives/** — `uniquify_creatives` (водяной знак), `folder_opener`.
- **campaign_scripts/planner.py** — декларативный план для ручного создания кампании.
- **campaign_creator/** — фабрика создания кампаний (Vision-based, не active в текущем сборке).
- **ai_assistant/** — pure-Python ассистент: `chat.py`, `client.py`, `providers.py`, `prompts/`. Пакет `tools/` (registry + base + ops/meta/drafts/creative — 15 tools) подключён к Telegram через `core/telegram/ai_handlers.py` (`/ask` + draft callbacks `dr_ok`/`dr_cancel`). `ToolHandler.risk_level`: READ_ONLY (исполняется немедленно), DRAFT_REQUIRED (создаёт `task_queue` со `status='draft'` + `created_by_chat_id` через `core.meta_api.queue.create_draft_task` → юзер подтверждает в TG только если он же owner или recipient с `role='owner'`), CREATIVE. Rate-limit per `client_key` через `tools/_ratelimit.py` (Redis `ai:ratelimit:tools:*` TTL 3600 + in-memory secondary cap 5/60с при сбое Redis вместо fail-open). `MetaApiClient` пробрасывается через `ToolContext` — без него meta-tools падают с явной ошибкой. `drafts/request_bulk_pause` ищет по `offer_code` через Postgres regex `~*` с anchored word-boundary `(^|[^a-z0-9])CODE([^a-z0-9]|$)` — старая ILIKE-substring выборка (`%CR%` матчит `ACRO`) удалена.
- **adset_pro/** — клиент AdSet.pro: оказался **MCP-сервером** (`platform-stats-mcp` v1.0.0), не REST API. Host `adset.pro` (не `api.adset.pro`), endpoint `POST /mcp` JSON-RPC 2.0 с Bearer-токеном из `ADSETPRO_MCP_KEY`. Доступно 10 MCP-tools: `query_stats`, `get_metadata`, `export_csv`, `list_campaigns`/`get_campaign`, `list_sources`/`list_offers`/`list_flows`/`list_cpas`, `resolve_ids`. Публичный контракт сохранён (`StatsQueryRequest/Response`), `call_mcp_tool(name, args)` — низкоуровневый канал под будущие AI-tools. Ingest postback'ов через `core/adset_pro/ingest.py` (двухступенчатый дедуп: pre-INSERT SELECT по 24h окну + `ON CONFLICT DO NOTHING` на UNIQUE). **Волна 4 (#BL-8):** `aggregator.py` (см. tracker_aggregator_worker #14), `outgoing.py` (`OutgoingPostbackSender` — httpx+tenacity retry, `send()` не бросает, `dispatch()` non-blocking fire-and-forget+`drain()`; URL-шаблон с макросами `{click_id}/{goal}/{payout}/...`; конфиг `tracker_outgoing_*`), `credentials.py` (ротация ключей без рестарта: `resolve_adsetpro_api_key`/`resolve_adsetpro_postback_secret` читают `adsetpro_credentials` singleton — Fernet поверх **BYTEA** — с фолбэком на `.env`; `create_adsetpro_client` в `deps.py`, postback endpoint резолвит секрет тем же путём).
- **adset_pro/queries.py + ingest.py** — `load_external_deposits_batch(engine, fb_ad_ids, since)` для evaluator; `ingest_postback` для FastAPI router'а.
- **dashboard/snapshot.py** — `build_ad_snapshot(engine, fb_ad_ids?, alert_states?, limit, offset, include_inactive)` + `build_incidents_snapshot(engine, stage?)`. Композитная view-функция: FbAd LEFT JOIN AdAlertState LEFT JOIN LATERAL (последняя AdMetrics за 7 дней) LEFT JOIN FbAdset/FbCampaign/Offer LEFT JOIN MetaApiObservation. Используется dashboard router'ами вместо устаревшей таблицы `ad_snapshots` (которой в текущей схеме нет). LATERAL `cycle_ts >= NOW() - make_interval(days => :lookback)` для partition pruning. Декомпозиция incidents `transitions_count` через batch `unnest(:ids::uuid[], :starts::timestamptz[])` LEFT JOIN AlertEvent (один запрос вместо N+1).
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

После миграции удалены, но могут быть восстановлены инкрементально:
- **API роутеры** (`apps/api/`) — 17 роутеров FastAPI. Понадобится для фронта.
- **Creator workers** (`apps/creator_worker/`, `apps/creator_recorder/`) — автоматизация создания кампаний через Vision.
- **Frontend** (`frontend/` 9 страниц + `frontend-mini/`) — отложен по решению пользователя. `apps/api/` минимум поднят (health + postback), при возврате к фронту — расширить роутерами под нужные страницы.
- **`scripts/backtest_rules.py`** — для бэктеста по MEMORY 2026-06-08 (через ~2 недели накопления данных).
- **Этап 4 Ad Library** — закрыт через browser-agent gRPC (по-запросу через `/spy <slot> <country>` в TG). Параллельный канал через свой Meta App с App Access Token решено НЕ делать — Meta требует Identity Confirmation (загрузка ID + selfie + 5-7 дней ожидания) даже для коммерческих запросов, при этом use case у пользователя on-demand, не cron. Если в будущем понадобится background-scrape конкурентов — пройти IC на https://www.facebook.com/id и положить `META_AD_LIBRARY_APP_ID`/`_APP_SECRET` в `.env`.
- **Этап 6 AdSet.pro Волна 3+4** — ✅ закрыто. Клиент переписан под MCP-протокол (AdSet.pro оказался MCP-сервером), `adsetpro_postback_events` + `adsetpro_credentials` партиционированные таблицы созданы и применены, ingest с двухступенчатым дедупом, `RuleContext.external_deposits` в evaluator (`load_external_deposits_batch` в pipeline). **Волна 4 (#BL-8):** aggregator per (ad_id, country, day) в `tracker_aggregate` (tracker_aggregator_worker, idempotent absolute-recompute), outgoing postback (`OutgoingPostbackSender`), ротация ключей через `adsetpro_credentials` (БД-first + `.env`-фолбэк, без рестарта). Миграция не понадобилась — таблицы уже были в 0001. Остаточный tech-debt (LOW): outgoing postback не подключён к конкретному flow (нет адресата-URL в проде), durable-outbox через `task_queue` — по запросу.
- **Меta API мелочи** — Custom Audience с CSV upload пользователей (`POST /{audience_id}/users` со streaming), `UploadVideo` из URL.

### Известные технические долги

- ~~`reset_after_disable_succeeded` нигде не вызывается~~ — ✅ исправлено в раунде 5 (`core/observer/writers.py` + `core/tasks/toggle_executor.py` вызывает после `mark_succeeded` в зависимости от `task_type`, идемпотентно через `WHERE alert_state IN (...)`).
- ~~Pre-existing bug в `core/ai_assistant/tools/ops/get_recent_alerts.py`~~ — ✅ исправлено в раунде 6D (`ae.event_type → ae.stage`, `ae.rule_codes → ae.matched_rule_codes`, `a.name → a.ad_name`).
- **Health watchdog: дедуп-ключ ставится даже при отсутствии TG-клиента.** При первом подключении токена «упущенные» алерты не доедут, пока не истечёт TTL 1h. Поведение проверяется `test_no_tg_client_does_not_crash` — нужно зафиксировать в runbook.
- **Backtest** (`scripts/backtest_rules.py`) — пройти историю и оценить false-stop'ы (MEMORY 2026-06-08).
- **Frontend ↔ backend shape расхождения** (зафиксированы при восстановлении API Round 7.2-7.3, возвращаются `null` фронту до миграций):
  - `Offer` нет полей `country_code`, `use_vision_creator`, `notes` (Round 7.2).
  - `OfferRule` — 6 числовых полей (`spend_no_event_threshold`/`cpa_threshold`/`cpm_threshold`/`ctr_threshold`/`frequency_threshold`/`funnel_ratio_threshold`), не JSONB `cpc_thresholds`/`cpl_thresholds` которые ждёт OffersPage (Round 7.2).
  - `AdMetrics` нет `delivery_status` (Round 7.3).
  - `AlertEvent` нет `triggered_by_rule_codes` (Round 7.3; в схеме только `matched_rule_codes`).
  - `AdAlertState` нет отдельных `last_warning_at`/`last_stop_at` — восстанавливаются из `last_transition_at` + `current_stage` CASE (Round 7.3).
  - `AdDepositCorrection.corrected_deposits` ↔ frontend `fake_count` (router маппит).
  - `AdAutoEnableDisabled.created_at` ↔ frontend `disabled_at` (router маппит).
  - `TaskQueue.next_retry_at` ↔ `next_attempt_at`, `last_error` ↔ `last_error_message`, `created_by_chat_id` ↔ `requested_by_chat_id` (Round 7.4 routers маппят).
  - `EnableRecommendation`: только `ad_id` UUID (через JOIN с FbAd для `fb_ad_id`/`ad_name`), `snapshot_metrics` ↔ `metrics_payload`, `recommendation_level` дублируется как `reason` (Round 7.4).
- **TODO subscriber'ы в worker'ах:** observer не подписан на `fb_agent:observer:trigger`/`cabinet_day`, не подписаны worker'ы на `fb_agent:worker:restart:*`. Endpoints publish'ат сигналы, до реализации subscriber'ов сигналы no-op.

### MCP-сервер (apps/mcp_server/)

Наш бэк доступен из Claude Desktop / Cursor / любого MCP-совместимого клиента через stdio-транспорт. См. `docs/MCP_SETUP.md` для настройки `claude_desktop_config.json`.

Адаптирует 15 AI-tools (READ_ONLY ops/meta + DRAFT_REQUIRED drafts + CREATIVE) в формат `mcp.types.Tool`. DRAFT-tools получают префикс `[ТРЕБУЕТ ПОДТВЕРЖДЕНИЯ В TELEGRAM]` в описании — Claude видит и ведёт юзера через `/drafts` callback в TG.

4 MCP Resources (Claude может промптиться на них без явного вызова tool): `fb-stop-bot://offers`, `fb-stop-bot://recent-alerts`, `fb-stop-bot://workers-health`, `fb-stop-bot://schema-overview` (динамический Markdown с описанием tools по risk-категориям).

Транспорт **stdio**: stdout — JSON-RPC канал, любой `print()` ломает протокол. `run_mcp_server.py` жёстко выставляет `logging.basicConfig(stream=sys.stderr)`. Entry: `python run_mcp_server.py` или подключение через `claude_desktop_config.json` (Claude Desktop сам запустит процесс).

HTTP/SSE транспорт для iPhone / удалённого доступа — отдельная история (нужен FastAPI router + OAuth/токен), пока только локальный stdio.

### Round 11 test-hardening + heartbeat code-fix — урок 2 CRIT обобщён

`docs/test_quality_audit.md` (265 строк): почему 1028 тестов пропустили 2 CRIT. Корневая причина — тесты проверяли стороны изолированно на данных, не нагружающих границу бага (money: 1 цикл метрик → naive SUM == latest → невидим; observer: тест ассертил сломанное `unknown` как ожидаемое). **Round 11 закрыл всё**, → 1055 passed.

- **РЕАЛЬНЫЙ баг health (не тестовый):** observer писал только `observer:runtime`, `toggle_executor` (disable/enable) не писал heartbeat вовсе, telegram_poller/cleanup/reconciler — тоже → из 7 `EXPECTED_WORKERS` мониторился только `meta_api`, остальные 6 давали ложные «мёртв». Фикс: фоновый heartbeat-таск во всех 6 воркерах под именами == EXPECTED (короткие: `observer`/`disable`/`enable`/`telegram_poller`/`cleanup`/`reconciler`), `toggle_executor` пишет `worker:heartbeat:{task_type}`. Контрактный тест `test_heartbeat_contract.py` (22 кейса, параметризованный анти-регресс имён writer↔reader).
- **18 test-усилений** (money exact-value мультицикл против naive-SUM `== 300.50` не 676 / cabinet-reset `== 80` не 165; counts через diff-подход против double-count; партиционные проверяют исключение вне-окна по значению; pubsub publisher↔subscriber E2E с реальным `main_loop`). Все cleanup-фикстуры money переведены с глобального DELETE на prefix-scoped (стабильность при random-порядке).

### Аудит code-quality + Round 10 cleanup — 2 money-bug'а + HIGH/MID закрыты

`docs/backend_code_quality_audit.md` (231 строка): независимый review КАЧЕСТВА (не покрытия). Нашёл 2 тихих money-bug'а, прошедших сквозь 974 теста (тесты проверяли shape, не семантику). **Round 10 закрыл всё**, → 1028 passed.

- **CRIT-1** — 8 аналитических endpoint'ов делали наивный `SUM()` по кумулятивным snapshot-метрикам `ad_metrics` → spend завышался 10-100×. Фикс: `core/dashboard/metric_aggregation.py` — `latest_per_ad_window_cte` (DISTINCT ON (bucket, ad_id) для суточных/chart) + `latest_per_ad_per_day_cte` (DISTINCT ON (ad_id, day) для многодневных, т.к. spend сбрасывается посуточно — cabinet day). Применён в history/performance/chart-data/offers. Семантические тесты (75 не 375, 80 не 165).
- **CRIT-2** — `observer:runtime` контракт рассогласован: writer писал `worker_status∈{scanning,idle,paused}`, readers ждали `status∈{running,paused}` → `observer_status` всегда `unknown`. Фикс: `core/observer/runtime.py::read_observer_runtime` (единая точка чтения + нормализация scanning/idle→running), writer пишет оба поля, контрактный тест writer↔reader.
- **HIGH** — `snapshot.py` LATERAL по alert_events → реальные `stop/warning_rule_codes` (были захардкожены `[]`); `validate-columns` проксирует реальный gRPC `ScannerService.ValidateColumns` (503 при недоступности, не фейк-true), save/apply-column-widths → честный 501; `/ai/analyze` rate-limit → Redis sliding-window + X-Forwarded-For (был process-local); `create_campaign` partial-fail → `CreateCampaignPartialError` с created_ids, worker `mark_failed` (не requeue — иначе дубли).
- **MID** — `disable_tasks` retry/cancel проверяют `rowcount` → 409 при гонке; `MutationValidationError(ValueError)` вместо голого ValueError в `_PERMANENT_EXCEPTIONS`.

Костяк (ACL, batch-encode, FSM-guards, partition-pruning, graceful shutdown) — признан качественным. Осталось как tech-debt (LOW): копипаста (JOIN ×7, task_serializer ×4), `history.py` 692 строки, `OfferOut` None-тип.

### Аудит раунда 8 — все CRIT/HIGH/MID закрыты в Round 9

`docs/backend_test_audit_round_8.md` (647 строк): comprehensive аудит 936 тестов после Этапа 7. Найдено 5 CRIT + 6 HIGH + ряд MID/LOW. Verdict: один целевой раунд → prod-ready. **Round 9 закрыл всё**, 936 → 974 passed (+38 тестов, +2 skipped).

- **CRIT #1** — `alert_dispatcher.py` SELECT по `alert_events` без partition-key: Alembic 0004 добавил partial-index `(scan_id, created_at)`, код фильтрует `created_at >= :since`. 3 integration-теста.
- **CRIT #2** — `approve_draft_task(admin_override=True)` без проверки что caller — admin: внутренний `is_admin_recipient` enforcement. 4 теста.
- **CRIT #3** — `handle_draft_callback` E2E с чужого `chat_id`: 3 сценария (owner / foreign / admin override).
- **HIGH #4-7** — snooze boundary edge cases (`snoozed_until == cycle_ts`, expire между scans, NULL, в прошлом): 6 unit + 2 integration теста.
- **HIGH #5** — Hypothesis property-based для evaluator: нашёл реальный баг (`regs_no_dep_stop` срабатывал без spend) — исправлено в коде.
- **HIGH #8** — concurrent `adset_pro` ingest dedup: 3 теста.
- **HIGH #9** — `_calc_next_retry` backoff (exponential, cap): 8 unit-тестов.
- **HIGH #10** — `is_admin_recipient` с `revoked_at`: 4 теста.
- **HIGH #11** — sentinel `message_id=0` dedup в alert_dispatcher: 1 regression-тест.

**Backend production-ready** (по состоянию на Round 9): 974 passed, ruff clean, все security/race/ACL gap'ы из независимого audit'а закрыты.

### Аудит раунда 6 — все CRIT/HIGH/MID закрыты

Все 15 багов из security audit раунда 5 закрыты в раунде 6 (23 коммита, 683/683 теста passed):

- **CRIT #1** — `_batch_helpers.encode_batch_body` через custom `_encode_value` не трогает JSONPath refs (`create_campaign` теперь физически работает в проде).
- **CRIT #2** — `mark_succeeded`/`mark_failed` возвращают `bool` + `WHERE status='running'`, защита от двойного выполнения при reconciler-race.
- **CRIT #3** — `attempt_count + 1` bump только в каноническом `core.tasks.queue.reconcile_stuck_running`, `apps/reconciler_worker/worker.py` теперь тонкая env-обёртка.
- **CRIT #4** — `apps/api/routers/postback.py` через `secrets.compare_digest` (timing-safe).
- **CRIT #5** — `apply_fsm_transition` с `WHERE alert_state NOT IN ('claimed', 'disabled')` в `ON CONFLICT DO UPDATE`.
- **CRIT #6** — `approve_draft_task` сверяет `created_by_chat_id` (Alembic `0002_taskq_chat_id`) или требует `admin_override=True` (только для `role='owner'`).
- **HIGH #8** — `alert_dispatcher` через pre-claim INSERT с sentinel + DELETE-rollback при ошибке.
- **HIGH #9** — `_begin_scan_run` атомарный через CTE.
- **HIGH #10** — `open_token` persistence: новый uuid4 только при старте incident'а (`normal→...`), при эскалации `warning_sent→stop_sent` старый сохраняется.
- **HIGH #11** — `set_adset_budget` hard cap $100k daily / $1M lifetime.
- **HIGH #12** — `create_app()` падает `RuntimeError` при `"*"` в `frontend_origin`.
- **HIGH #13** — `_ratelimit` с in-memory secondary cap при сбое Redis.
- **HIGH #14** — `request_bulk_pause` через Postgres regex `~*` с anchored word-boundary.
- **HIGH #15** — `bulk_status_change` warning-лог + caller-side type guard в docstring.
- **HIGH #16** — `match_offer_for_ad` alphabetical tie-breaker при равной длине.
- **MID #17** — `is_in_send_window` catch-up до конца суток UTC.
- **MID #18** — `BodySizeLimitMiddleware` 64 KB hard cap.
- **MID #19** — `plan_run` callback пишет INFO лог `who_started_plan` + `created_by_chat_id` в `task_queue`.
- **MID #20** — `_count_active_ads_normal` фильтрует `last_seen_at >= NOW() - 7d`.
- **MID #21** — `_is_registration_normal` docstring явный контракт без double-fold cpr.

### Будущие модули (см. META_INTEGRATION_PLAN.md + DB_REDESIGN.md)

В схеме уже подготовлены таблицы (см. `core/models/`):
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

**Новый фронт (`frontend/`):** React 19 + Vite 6 + **TypeScript strict** + Tailwind 4 + TanStack Router (file-based) + TanStack Query 5 + Zustand 5 + Lucide + Radix Primitives. Dark-only, monochrome editorial style (см. `docs/frontend_design.md`). 6 страниц (Dashboard, Ads, Offers, History, Settings, Drafts). Storybook 8 для component isolation. Desktop 1280+ only. Port 5174 (dev), proxy `/api` → `:8100`. Сейчас в репо — foundation (tokens, базовый UI, layout shell, placeholder-страницы, API-клиенты per-domain, WebSocket hook с polling fallback, 5 unit-тестов). Полная имплементация страниц — следующие раунды.

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
