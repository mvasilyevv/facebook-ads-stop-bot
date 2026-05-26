# Agent 4 — Project mapping: текущая архитектура FB Stop Bot → Marketing API / Meta MCP

> Автор: subagent (на основании прямого чтения кода), дата: 2026-05-25.
> Источники: `CLAUDE.md`, `META_MCP_RESEARCH.md`, плюс прямой просмотр кодовой базы (`apps/`, `core/`, `frontend/`, `services/browser-agent/`, `clients/`, `proto/`, `migrations/`, `run.sh`, `docker-compose.yml`).

Категории в таблицах:
- **R (Replace)** — функция 100% уходит в Marketing API/MCP.
- **A (Augment)** — функция остаётся, но получает новые данные/возможности.
- **K (Keep as-is)** — функция не зависит от источника данных, не трогаем.
- **N (New)** — новая возможность, которую API/MCP открывает.

---

## 1. Текущая структура проекта (что реально лежит в репо)

Проект — это монорепо с несколькими языковыми контурами:

- **Python-orchestrator** (`apps/`, `core/`) — все доменные процессы, БД, FastAPI, Telegram. Семь воркеров, а не шесть, как в `CLAUDE.md`: добавлены `creator_worker` и `health_watchdog`. Точки входа: `run_observer.py`, `run_disable_worker.py`, `run_enable_worker.py`, `run_enable_recommendation_worker.py`, `run_creator_worker.py`, `run_health_watchdog.py`, `python -m apps.telegram_poller.main`, `uvicorn apps.api.main:app`.
- **Node.js browser-agent** (`services/browser-agent/`) — отдельный сервис на TS, который держит Playwright-сессию Vision-браузера и отдаёт всё через gRPC (порт 50051). Реализованные сервисы: `ScannerService` (RunScanCycle, RefreshTable, ScrollAndParse, FindToggleCell, ToggleAd, HardReloadPage, ValidateColumns…), `CreatorService` (RunPlan stream, StartRecording, StopRecording), `BrowserSession` (см. `proto/v1/`). DOM-парсинг по `data-surface` атрибутам теперь живёт в TS, а не в Python — в `core/scanner/` остался только `models.py` (`ScannedAdRow`).
- **gRPC stubs** (`clients/python_grpc/`, `proto/v1/`) — `BrowserAgentClient` оборачивает все вызовы и пропускает их через `AsyncCircuitBreaker` (`core/browser/circuit_breaker.py`).
- **БД** (Postgres 16 на порту 5433) и **Redis** (порт 6380) — оба в `docker-compose.yml`. Redis нужен для очереди алертов (`core/alerts/`) и pub-sub каналов (`core/pubsub`) для WebSocket-реалтайма.
- **Frontend** (`frontend/`, React 19 + Vite) и **Mini-app** (`frontend-mini/`, Telegram Web App) — две независимые SPA. Страницы фронта: Dashboard, Ads, Offers, Analytics, History, Naming, Scripts, Settings (`PAGES` в `App.jsx`).
- **AI assistant** (`core/ai_assistant/`) — Anthropic SDK + OpenAI fallback, tool-use (`supervisor_restart`, `tail_log`, и др.), используется для объяснения причин алертов (`ai_explain_alerts_enabled`) и для AI-карточек на дашборде (`AIBriefingCard`).
- **Доп. модули, которых нет в CLAUDE.md:**
  - `core/campaign_creator/` — runner деклоративных «планов» создания кампаний, шаги (`create_campaign`, `create_adset`, `upload_creatives`, `set_geo`, `set_budget`, `set_cta`, `set_pixel_event`, `set_attribution`, `duplicate_adset`, и т.д.) — это уже браузерная автоматизация Ads Manager UI поверх browser-agent.
  - `core/campaign_recorder/` — recorder-«самописец» действий пользователя в Ads Manager → JSON-план для последующего воспроизведения.
  - `core/campaign_scripts/` — высокоуровневое планирование скриптов из папки креативов.
  - `core/creatives/` — uniquifier (модификация креативов, чтобы избегать одинаковых хешей), folder_opener.
  - `core/enable_recommendations/` — анализ выключенных объявлений + генерация рекомендаций на reenable.
  - `core/ads/actions.py` — высокоуровневые операции (`get_ad_detail`, `disable_ad`, `snooze_ad`, `claim_ad`) — фасад для Telegram-бота и mini-app.
  - `core/fake_deposits.py` — таблица корректировок «ложных депозитов» (вручную проставленных).
  - `core/cabinet_day.py` — отслеживание момента сброса дневной статистики в Ads Manager (zero-scan детект).
  - `core/alerts/` — очередь и drain-worker для отправки Telegram-сообщений через Redis.
  - `core/pubsub.py` + `apps/api/routers/ws.py` — WebSocket pub/sub для realtime-обновлений дашборда.
- **Helm/k8s** — `helm/`, `k8s/` (артефакты для деплоя в кластере).
- **TMA-роутер** (`apps/api/routers/tma.py`) — отдельная аутентификация через Telegram `initData`.

Итого: то, что в `CLAUDE.md` описано как «шесть воркеров + API», на практике — семь воркеров, два UI, gRPC-микросервис на Node, Redis, AI-ассистент, фабрика создания кампаний и инструменты записи действий. Большая часть «легаси-сложности» — это именно браузерная автоматизация, которая в той или иной мере вытесняется Marketing API.

---

## 2. Полный mapping (таблица)

### 2.1. Воркеры (`apps/`)

| Модуль/файл | Что делает сейчас | Категория | Что станет с интеграцией | Конкретный API/MCP вызов |
|---|---|---|---|---|
| `apps/observer_worker/main.py` (2220 строк, точка входа `run_observer.py`) | Бесконечный цикл: lock браузер → gRPC `RunScanCycle` → парсинг DOM-таблицы → matching офферов → `evaluate_row` (6 правил) → snapshot upsert → FSM-переход → Telegram-алерт через Redis-очередь; reconcile disable/enable tasks; cabinet_day watch; самовосстановление (`browser_recovery`, `self_healing`); STALE-data handler. | A | Источник данных меняется: вместо `BrowserAgentClient.RunScanCycle` — async-клиент Marketing API (`core/meta_api/insights.py`). Вся остальная оркестрация (FSM, matching, snapshot, retries, reconcile, AI-explain) остаётся. Уходят `BrowserRecoveryEscalator`, `StaleDataEscalator`, `ZeroScanGuard`, `RegressionGuard` — все они придуманы для проблем DOM-парсинга. Цикл становится короче и стабильнее. | `GET /act_{ACCOUNT_ID}/insights?level=ad&fields=ad_id,ad_name,adset_name,campaign_name,spend,impressions,clicks,cpc,ctr,actions,cost_per_action_type,outbound_clicks,cpm,frequency,reach,cost_per_result&date_preset=today&limit=500` + `GET /{ad_id}?fields=effective_status` |
| `apps/disable_worker/main.py` (точка входа `run_disable_worker.py`) | Поллит `DisableTask` (FOR UPDATE SKIP LOCKED) → захват browser-lock → gRPC `FindToggleCell` → `ReadToggleState` → `ToggleAd` → `WaitForToggleConfirmation`; retry с exponential backoff; таймауты 60/120s; heartbeat. | R | Полная замена: вместо четырёх RPC и борьбы с UI — один POST в Graph API. Сохраняем тот же outbox-паттерн (таблица `disable_tasks`), retry-логику, idempotency_key, completion-callback в Telegram. Удаляются browser-lock, batch-таймауты, ToggleConfirmation-проверка — статус из ответа API. | `POST /{fb_ad_id}` с телом `{"status": "PAUSED"}` (или `"DELETED"` для архивации). Ошибки кодируются `error_subcode`. |
| `apps/enable_worker/main.py` (точка входа `run_enable_worker.py`) | Аналог disable_worker, но переключает toggle в ON. Особенность: после успеха сбрасывает `alert_state` в NORMAL минуя state_machine (`mark_succeeded` в reconciler). | R | То же самое, что disable_worker. Сохраняется специальный сброс alert_state. Замечание: на сущности, **созданные через официальный mcp.facebook.com/ads**, активация запрещена (Meta forces PAUSED), но у нас все объявления — легаси, созданные через UI, ограничение не действует. | `POST /{fb_ad_id}` с телом `{"status": "ACTIVE"}` |
| `apps/enable_recommendation_worker/main.py` | Поллит снапшоты OFF-объявлений, вызывает `core.enable_recommendations.service.collect_enable_recommendation_candidates`, создаёт `EnableRecommendationEvent`, шлёт TG, при `auto_enable_recommendations=True` промоутит до `EnableTask`. | A | Логика рекомендаций (`determine_enable_recommendation_level` в `core/rules/evaluator.py`) не меняется. Источник входных данных — те же insights API. Дополнительно можно подтянуть `ads_get_opportunity_score` и `ads_insights_performance_trend` (MCP-tools) для усиления рекомендаций. | `GET /{ad_id}/insights` + опционально `ads_get_opportunity_score`, `ads_insights_performance_trend` |
| `apps/telegram_poller/main.py` | Long-polling Telegram, набор команд (`/start`, `/app`, `/digest`, `/help`, `/init_topics`, `/bind_thread`), inline-кнопки «Отключить»/«Снуз»/«Заявить», запуск digest scheduler, alerts drain loop (Redis → TG). | K | Источник данных и команд UI не зависит от Meta API. Только косвенный эффект: новые команды/коллбеки (`/duplicate_ad`, `/clone_campaign`, `/pause_offer`, `/set_budget`) появятся **после** написания Marketing-API-обёрток в `core/meta_api/actions.py`. | — |
| `apps/api/main.py` (FastAPI :8100) | Lifespan, CORS, 17 роутеров, dependency `verify_api_key`/`require_api_key_or_tma`, Prometheus `/metrics`, scan_runs housekeeping background-task. | K | Сам FastAPI-фреймворк и общая обвязка не зависят от Meta API. Новые роутеры (`core/meta_api/`-эндпоинты для UI) подключаются `app.include_router`. | — |
| `apps/health_watchdog/main.py` | Каждые 30s читает `worker_heartbeats`, `/api/health/details`, размер `.logs/observer.log`, supervisor-status через XML-RPC, дёргает рестарты, шлёт инциденты в Telegram. | A | Дольше остаётся актуальным для disable/enable воркеров и creator_worker; для observer становится проще (нет browser-зависимости, отпадают логи `browser_agent.log` и `WAKE_JUMP_THRESHOLD`). Добавляются проверки `meta_api_health` (last successful insights fetch, rate-limit headroom). | Расширение `HealthDetails`: новое поле `meta_api` (last_call_at, rate_limit_remaining, last_error). |
| `apps/creator_worker/main.py` (точка входа `run_creator_worker.py`) | Поллит `PlanRun` (creator v2), исполняет план через `CreatorService.RunPlan` gRPC-стрим, накапливает step_log, переводит в `REQUIRES_ATTENTION` на checkpoint. | R | Радикально упрощается: вместо browser-стрима с десятками шагов («click_next», «set_pixel_event», «upload_creatives», «duplicate_adset») — несколько вызовов Marketing API: `POST /act_X/campaigns` → `POST /act_X/adsets` → `POST /act_X/adcreatives` → `POST /act_X/ads`. Recorder-кейс умирает, plan_runner ужимается до простого orchestrator над batch-API. | Marketing API: `POST /act_{id}/campaigns`, `POST /act_{id}/adsets`, `POST /act_{id}/adcreatives`, `POST /act_{id}/ads`. MCP-tools: `ads_create_campaign`, `ads_create_ad_set`, `ads_create_ad`. |
| `apps/creator_recorder/main.py` | Worker для записи действий пользователя через `CreatorService.StartRecording`/`StopRecording` (генерирует JSON-план). | R | Скорее всего, утратит смысл: записывать клики в UI нужно для воспроизведения. С Marketing API «план» вырождается в декларативный spec (CampaignSpec уже есть в `core/campaign_creator/plan_types.py`), который пишется вручную или генерируется AI-ассистентом. | — (удаление или превращение в no-op) |

### 2.2. Core (`core/`)

| Модуль/файл | Что делает сейчас | Категория | Что станет с интеграцией | Конкретный API/MCP вызов |
|---|---|---|---|---|
| `core/domain.py` | Enum-ы `AlertStage`, `AlertState`, `DisableTaskStatus`, `EnableTaskStatus`, `EnableRecommendationLevel`, `TelegramUserRole`, `TelegramNotificationStream`, `CampaignCreatorTaskStatus`, `PlanRunStatus`. | K | Доменная модель не зависит от источника данных. Возможно появление `PlanRunStatus` уберём за ненадобностью (creator переходит на API). | — |
| `core/models/__init__.py` (990 строк, ~25 ORM-классов) | SQLAlchemy-модели: `ObserverSettings`, `TelegramSettings`, `Offer`, `OfferRuleConfig`, `FbCampaign`, `FbAdset`, `FbAd`, `AdSnapshot`, `AdMetricHistory`, `AlertEvent`, `DisableTask`, `EnableTask`, `EnableRecommendationEvent`, `AdDepositCorrection`, `AdAutoEnableDisabled`, `VisionSettings`, `TelegramInvite`, `TelegramRecipient`, `TelegramMessageRef`, `WorkerHeartbeat`, `AlertSnooze`, `CampaignCreatorTask`, `Plan`, `PlanRun`, `AICache`, `OfferRuleStat`, `CabinetDayArchive`, `ScanRun`. | A | Основная модель данных переживает миграцию. Расширения: добавляется `MetaApiCredentials` (token, ad_account_id, scopes, expires_at), `MetaApiCallLog` (для аудита/rate-limit) и поле `meta_ad_status` в `AdSnapshot` (effective_status из API: `ACTIVE`, `PAUSED`, `DELETED`, `WITH_ISSUES`). `VisionSettings` остаётся, но переходит в режим fallback. `CampaignCreatorTask`/`Plan`/`PlanRun`/`ScanRun` теряют большую часть смысла. | — (миграции БД) |
| `core/observer/service.py` (`evaluate_row`, `build_rule_context`, `build_metrics_json`, `resolve_offer_code`, `AlertCandidate`, `ObserverCycleResult`) | Чистая доменная логика поверх `ScannedAdRow`. | K | Не трогаем. `ScannedAdRow` будет строиться из API-ответа вместо DOM-парсера, контракт неизменен. | — |
| `core/observer/state_machine.py` | FSM-переходы между `AlertState` с UUID-токенами, идемпотентностью, `reopen_reactivated_alert_state` (для случая, когда DISABLED-объявление снова показывается). | K | FSM не зависит от источника данных. | — |
| `core/observer/disable_reconciler.py` | `auto_create_disable_tasks` создаёт DisableTask при STOP-стадии; `reconcile_disable_tasks_in_db` отменяет «протухшие» auto-tasks. Аналогично для enable. | A | Логика остаётся. Появляется быстрый sync-вариант: «сразу вызвать API» вместо outbox-задачи, если очередь пуста и API доступен. Также reconcile может сверяться с реальным `effective_status` из API. | — (косвенно использует `core/meta_api/actions.py`) |
| `core/observer/browser_recovery.py`, `self_healing.py`, `regression_guard.py`, `scan_guard.py`, `stale_data_handler.py` | Эскалаторы и страховки против хрупкости DOM-парсера: STALE_DATA (>90% пустых ячеек), zero-scan, регрессии метрик, отказы CDP. | R | Все эти модули рождены проблемой DOM. С API → удаляются почти полностью. Остаются простые проверки rate-limit и API-errors. | — (удаляются) |
| `core/scanner/parser.py` | Уже не существует как Python-файл — парсинг переехал в `services/browser-agent/src/parser.ts` (TS). Был исторически. | R | Файл удаляется (если ещё есть legacy-следы в импортах). Парсинг DOM как способ получения данных уходит полностью. | — (удаление) |
| `core/scanner/models.py` (`ScannedAdRow` frozen dataclass) | DTO для одной строки метрик. | K | Сохраняется как единый внутренний контракт. Меняется только factory: `ScannedAdRow.from_insights_dict(...)` рядом с уже существующими конструкторами. | — |
| `core/rules/evaluator.py` (607 строк, 6 правил + frequency-anomaly + funnel-лесенка + `determine_enable_recommendation_level`) | Чистая бизнес-логика стоп-правил. | K | Не зависит от источника. Возможные расширения (см. раздел N): `ads_insights_industry_benchmark` для динамической калибровки порогов. | — |
| `core/rules/types.py`, `core/rules/labels.py` | `RuleContext`, `RuleHit`, `RuleEvaluation`, ярлыки правил. | K | Не трогаем. | — |
| `core/browser/circuit_breaker.py` | `AsyncCircuitBreaker` для gRPC-вызовов к browser-agent. | A | Переиспользуется для async-клиента Marketing API (httpx). Та же идея: при серии 429/5xx — открываем circuit, не давим API. | — |
| `core/browser/lock.py` | `acquire_browser_lock` (per-process файловый lock для exclusivity scan vs disable vs enable). | R | Не нужен, если убрать browser-agent. С API доступом параллельных вызовов нет конкуренции за «единственный CDP-порт». Lock переезжает только на fallback-сценарии Vision. | — (упраздняется) |
| `services/browser-agent/` (TS, gRPC :50051) — `ScannerService`, `CreatorService`, parser.ts, ads-table.ts, ads-columns.ts, session-manager.ts, hard-reload.ts, modal-dismisser.ts, humanizer.ts, stealth.ts, toggle-utils.ts, vision-client.ts | Playwright-сессия Vision-браузера + DOM-парсер + tool-вызовы для disable/enable/scan/creator. | R/K | Большая часть (Scanner, Creator) — заменяется. Остаётся как **fallback**: ручной OAuth-флоу, скриншоты «как видит юзер», отладка спорных кейсов. Один HumanClick для OAuth + один ScanCycle на случай, если API не отвечает. Снижение: ~80% TS-кода уходит, остаётся минимальный browser_session.proto + stealth.ts + ручной recorder. | — |
| `core/browser/` (Python wrappers `vision_client.py`, `manager.py`) | По CLAUDE.md существовали; на практике файлов нет — функционал ушёл в TS. В Python остался только circuit_breaker + lock. | R | Уже частично удалено. | — |
| `core/telegram/client.py` (низкоуровневый httpx-клиент Bot API: send/edit/answer_callback/get_updates/set_my_commands) | Минимальная обёртка Bot API. | K | Не трогаем. | — |
| `core/telegram/renderer.py` | Форматирование алертов с inline-кнопками, render_alert_message, normalize_enable_recommendation_reason. | A | Сохраняется. Добавляются новые шаблоны: «бюджет повышен», «кампания склонирована», «budget adjustment applied», и т.д. для новых N-фич. | — |
| `core/telegram/bot_handler.py` | Маршрутизация команд, callback-кнопок, пагинация, `_create_disable_task`. | A | Появляются новые команды: `/clone <fb_ad_id>`, `/budget <campaign> <amount>`, `/duplicate_winners <offer>`, `/pause_offer <code>`, и т.д. Все — поверх `core/meta_api/actions.py`. | Marketing API mutations |
| `core/telegram/digest_scheduler.py`, `digest_queries.py`, `digest.py` | Ежедневный дайджест в 9:00 (timezone из настроек) — статистика за вчера. | A | Получает доступ к свежим API-данным с минимальной задержкой. Можно добавлять trend-сравнения через `ads_insights_performance_trend`. | `ads_insights_performance_trend` (MCP) |
| `core/alerts/queue.py`, `drain_worker.py`, `send.py` | Redis-очередь алертов: producer (observer/enable_recommendation) → drain_worker → Telegram. | K | Не зависит от Meta. | — |
| `core/pubsub.py` + WebSocket-роутер | Realtime-каналы `CHANNEL_ALERT_CREATED`, `CHANNEL_SCAN_FINISHED` для фронта. | A | Появляются новые каналы: `CHANNEL_META_RATE_LIMIT_WARNING`, `CHANNEL_AD_STATUS_CHANGED` (webhook). | — |
| `core/ai_assistant/client.py`, `chat.py`, `providers.py`, `tools.py`, `explain.py`, `diagnostics.py`, `prompts.py` | Anthropic + OpenAI клиенты, tool-use (`supervisor_restart`, `tail_log`, ...), AI-объяснения алертов, AI-карточки на дашборде. | A | Резкое расширение: tools.py получает новые tool-обёртки (`pause_ad`, `clone_campaign`, `set_budget`, `get_insights`, ...) — AI-ассистент становится оператором, а не только наблюдателем. Это и есть «диалоговая аналитика» из идеи Варианта Г. | tool-обёртки = mutate-эндпоинты Marketing API |
| `core/enable_recommendations/service.py` | Логика подбора кандидатов на reenable. | A | Может опираться на `ads_get_opportunity_score`. | MCP `ads_get_opportunity_score` |
| `core/campaign_creator/*` (runner, plan_runner, spec_builder, plan_builder, plan_types, naming, humanizer, tree_nav, step_executor, creo_scanner, context_codec, steps/{create_campaign,create_adset,set_geo,set_budget,upload_creatives,set_cta,fill_texts,set_age,set_pixel_event,set_conversion_location,set_schedule_start,set_attribution,set_tracking_url,switch_to_adset,reattach_creative,duplicate_adset,duplicate_ad,rename_ad,rename_adset,click_next,save_draft}) | Браузерная фабрика создания кампании из креативной папки + наименование + humanizer для имитации человека. | R | Замена на серию Marketing-API вызовов: campaigns.create → adsets.create → adcreatives.create → ads.create (см. строку про `creator_worker`). Папка-сканер и нэйминг остаются (это про файлы и шаблоны имени, не про браузер). Humanizer, tree_nav, step_executor, все «steps/», runner, plan_runner — удаляются. | `POST /act_X/campaigns` + `adsets`, `adcreatives`, `ads`. MCP: `ads_create_campaign`, `ads_create_ad_set`, `ads_create_ad`. |
| `core/campaign_creator/creo_scanner.py`, `spec_builder.py`, `plan_builder.py`, `plan_types.py`, `naming.py` | Сканирование папки креативов, парсинг имён, билд `CampaignSpec`. | K | Чистый Python без браузера — сохраняется. Spec становится директивой для Marketing API. | — |
| `core/campaign_recorder/*` (analyzer, cdp_session, event_injector, markdown_report, session_writer) | Recorder действий пользователя в Ads Manager → JSON-план. | R | Утрачивает смысл (см. `creator_recorder`). Может быть сохранён как «recorder для документации», но в воркер не интегрирован. | — |
| `core/creatives/uniquifier.py`, `service.py`, `folder_opener.py` | Уникализация креативов (изменение хеша файла), открытие папки. | A | Уникализация продолжает быть нужна (FB пессимизирует одинаковые креативы). Открытие папки — локальное действие, не связано с API. С API можно дополнительно загружать креативы напрямую: `POST /act_X/adimages` / `advideos`. | `POST /act_X/adimages`, `POST /act_X/advideos` |
| `core/campaign_scripts/planner.py`, `creative_folder.py` | Высокоуровневое планирование скриптов из папки. | K | Чистый Python, остаётся. | — |
| `core/creator_bridge/` | Bridge между fb-бот-логикой и creator-пайплайном. | A | Тонкий слой остаётся, теперь поверх API-actions. | — |
| `core/fake_deposits.py` + роутер | Ручная корректировка ложных депозитов (`AdDepositCorrection`). | K | Не зависит от Meta — это наша внутренняя кухня (мы не доверяем FB-данным о покупках). | — |
| `core/cabinet_day.py` | Детект момента сброса дневной статистики Ads Manager (zero-scan). | A | С API момент сброса виден явно по полю `date_start` insights или через `breakdowns=hourly_stats_aggregated_by_advertiser_time_zone`. Не нужен сложный zero-scan-эвристик. | `breakdowns=hourly_stats_aggregated_by_advertiser_time_zone` |
| `core/diagnostics.py` (build_ad_quality_diagnostics, compute_cpm_baselines_by_offer) | Доп.диагностика для приклеивания к алертам. | A | Богаче за счёт `ads_insights_auction_ranking_benchmarks` и `ads_insights_industry_benchmark`. | MCP: `ads_insights_auction_ranking_benchmarks`, `ads_insights_industry_benchmark` |
| `core/observer/thresholds.py`, `outcome_classifier.py`, `runtime_status.py`, `snapshot_writer.py`, `scan_run_writer.py`, `db_queries.py` | Утилиты observer'а: пороги, классификация исходов цикла, runtime-статус, upsert снапшотов, scan_runs, БД-запросы. | A | `outcome_classifier` упрощается (исчезают STALE_DATA, ERROR_BROWSER_*). `snapshot_writer` остаётся, `db_queries` тоже. | — |
| `core/settings_queries.py`, `core/crypto.py`, `core/math_utils.py`, `core/worker_utils.py`, `core/logging.py`, `core/sentry.py`, `core/metrics.py` | Утилиты, шифрование токенов (Fernet), retry math, JSON-logger, Sentry, Prometheus. | K | Не зависят. Только `crypto` начнёт шифровать `META_API_TOKEN` (новый тип секрета). | — |
| `core/config.py` (pydantic-settings, `get_settings()`) | Один синглтон конфигурации. Уже есть `vision_x_token`, `anthropic_api_key`, `openai_api_key`, и т.д. | A | Добавляются: `meta_app_id`, `meta_app_secret`, `meta_system_user_token`, `meta_ad_account_id`, `meta_business_id`, `meta_api_version` (по умолчанию `v22.0`), `meta_api_proxy_url` (опционально), `meta_api_rate_limit_buffer_percent`. | — |
| `core/db/base.py`, `core/db/__init__.py` | Declarative base, async engine + session_factory синглтоны. | K | Не зависят. | — |
| `core/task_queue/postgres_queue.py`, `base_worker.py` | Базовый класс для DB-выкачивающих воркеров. | K | Переиспользуется для нового `meta_api_executor_worker` (если решим выделить отдельно). | — |
| `clients/python_grpc/client.py`, `v1/` | gRPC stubs к browser-agent (`BrowserAgentClient`, `ScanResult`, `ScanProgress`, `ScanDataUnavailableError`). | R | После миграции большая часть импортов уходит. Остаётся минимальный клиент для fallback. | — (постепенное удаление) |
| `proto/v1/scanner.proto`, `creator.proto`, `browser_session.proto` | Контракт gRPC. | R | Schema sunsets. Сохраняем только `browser_session.proto` для fallback. | — |

### 2.3. API роутеры (`apps/api/routers/`)

| Роутер | Размер | Что делает сейчас | Категория | Что станет |
|---|---|---|---|---|
| `dashboard.py` | 3052 строки | Главные данные для Dashboard: KPI-стрипы, графики, scorecards, alerts, task-queue. Большое количество SQL-агрегаций по нашим snapshots. | A | Источник данных — БД, не зависит от Meta. После миграции дашборд начнёт показывать **больше**: real-time effective_status, opportunity_score, industry-benchmark рядом с метрикой. |
| `history.py` | 1602 | История по объявлениям/кампаниям/офферам, фильтры, KPI-стрип, trend-чарт. | A | Возможность сравнивать с industry benchmarks; impression breakdown по `age,gender,country,placement` без дорогого скрейпа. |
| `settings.py` | 759 | CRUD observer/Telegram-настроек, scanning toggle, web_app_url, frequency_thresholds. | A | Новые секции: «Meta API connection» (OAuth, токен, ad_account_id), «Multi-account» (список подключённых аккаунтов). |
| `vision_telegram.py` | 754 | Vision settings + Telegram invites/recipients + ensure-cdp. | A | Часть «Vision» уходит в fallback-секцию. Telegram-часть остаётся. Появляется аналогичный `meta_api.py` для onboarding. |
| `ai.py` | 570 | AI-карточки на дашборде, brief анализ, chat. | A | Новый mode «ассистент-оператор»: AI может выполнять мутации (pause, clone, set_budget) через tools, см. секцию N. |
| `health.py` | 364 | `/health`, `/health/details` (статусы воркеров, supervisor, browser_agent ping). | A | Расширяется `meta_api` секцией. |
| `campaign_creator.py` | 320 | Запуск автосоздания кампаний. | R | Полностью переписывается под Marketing API (без browser). |
| `tma.py` | 254 | Telegram Mini App-аутентификация через initData. | K | Не зависит. |
| `offers.py` | 247 | CRUD офферов, правил, фронт-форма. | A | Поле `meta_ad_account_id` на оффере — если разные офферы льются с разных аккаунтов. |
| `observer.py` | 217 | Статус observer-а, force-scan, pause/resume. | A | Кнопка force-scan теперь дешёвая (один API-вызов). Pause-логика остаётся. |
| `campaign_recorder.py` | 200 | Старт/стоп записи действий, статус. | R | Утрачивает смысл, удаляется или зачехлён. |
| `naming_tracker.py` | 160 | Группировка объявлений по паттерну имени (числовой суффикс). | K | Не зависит. |
| `fake_deposits.py` | 109 | CRUD корректировок депозитов. | K | Не зависит. |
| `campaign_scripts.py` | 72 | Ручной планировщик скриптов из папки. | K | Не зависит. |
| `ws.py` | 72 | WebSocket pub/sub. | K | Не зависит. |
| `creative_tools.py` | 69 | Уникализация креативов и т.п. | K | Не зависит. |

### 2.4. Frontend (`frontend/src/`)

| Страница / компонент | Что делает сейчас | Категория | Что станет |
|---|---|---|---|
| `pages/DashboardPage.jsx` | Чеклист, тогл сканирования, KPI-стрипы, графики, AlertTray, TaskQueuePanel, CampaignScorecard. | A | Появляется блок «Meta API health» (вместо «Vision CDP»), opportunity_score per ad, benchmark-сравнения. |
| `pages/AdsPage.jsx` | Список объявлений с фильтрами, статусы, kebab-меню (отключить/снузить/посмотреть). | A | Действия из меню становятся sync-API: «Отключить» возвращает результат за секунду. Появляются «Дублировать», «Изменить бюджет». |
| `pages/OffersPage.jsx` | CRUD офферов, конфиг 6 правил, geo/cabinet/pixel. | A | Поле `meta_ad_account_id` per offer, поле `meta_pixel_id` обогащается списком из API (`GET /act_X/adspixels`). |
| `pages/AnalyticsPage.jsx` | Сравнение офферов, donut причин стопа, heatmap алертов, CPL-timeline. | A | Добавляются industry-benchmarks, attribution windows comparison. |
| `pages/HistoryPage.jsx` | История объявлений/кампаний/офферов. | A | Лента включает действия «через API», audit-log API-вызовов. |
| `pages/HealthMapPage.jsx` | Карта здоровья воркеров и компонентов. | A | Добавляется тайл `meta_api`. |
| `pages/ScriptsPage.jsx` | Запуск скриптов создания кампаний из папок. | R | Перерисовывается под API-driven creation. |
| `pages/NamingTrackerPage.jsx` | Трекер нумерации объявлений по паттерну. | K | Не зависит. |
| `pages/SettingsPage.jsx` | Настройки Observer, Telegram, Vision. | A | Секция Vision → «Fallback browser». Новая секция «Meta API». |
| `api.js` | Fetch-обёртка для FastAPI. | A | Новые методы: `getMetaAccounts`, `connectMetaAccount`, `cloneCampaign`, `setAdBudget`, `bulkPause`, `getOpportunityScore`. |
| `components/CampaignScorecard.jsx`, `AlertTray.jsx`, `TaskQueuePanel.jsx`, `OfferLeaderboard.jsx`, `TopAdsQualityTable.jsx`, `RuleViolationRanking.jsx`, `SpendAlertsChart.jsx`, `BudgetOverrunChart.jsx`, `CampaignComparativeBars.jsx`, `CampaignBreakdownTable.jsx`, `CampaignCreatorTimeline.jsx`, `CommandPalette.jsx`, `MiniSparkline.jsx`, `HealthBar.jsx`, `KPIPlate.jsx`, `PacingCardiogram.jsx`, `StateIcon.jsx`, `OfferDetailPanel.jsx`, `OfferRulesTab.jsx`, `OfferThresholdsTab.jsx`, `HistoryAdsTable.jsx`, `HistoryCampaignTable.jsx`, `HistoryOffersTable.jsx`, `MetricsTrendChart.jsx`, `SpendTrendChart.jsx`, `EventTimeline.jsx`, `DateRangePicker.jsx`, `HistoryFilters.jsx`, `ObserverStatusTile.jsx`, `ScanRunsHistoryModal.jsx`, `AIBriefingCard.jsx`, `AIPanelButton.jsx`, `AIInlineButton.jsx`, `DashboardCommandBar.jsx`, `DashboardOperations.jsx`, `StopReasonsDonut.jsx`, `AlertsHeatmap.jsx`, `CPLTimeline.jsx`, `DecisionsHistoryFeed.jsx`, `OfferComparisonTable.jsx`, `AnalyticsKPIStrip.jsx` | UI-компоненты, общий стиль. | K/A | Большинство компонентов потребляют те же API/snapshots — не меняются. Часть (CampaignCreatorTimeline, ScanRunsHistoryModal, ObserverStatusTile) теряет смысл при удалении соответствующих процессов. |
| `hooks/useAsyncPolling.js`, `useRefreshOnResume.js`, `useWebSocket.js`, `useTableSort.js`, `useIsMobile.js`, `useDebouncedValue.js`, `useSettingsData.js` | UI-хуки. | K | Не зависят. |

### 2.5. Инфраструктура

| Артефакт | Что делает сейчас | Категория | Что станет |
|---|---|---|---|
| `docker-compose.yml` | Postgres 16 + Redis 7-alpine. | K | Без изменений (Postgres и Redis всё ещё нужны для outbox-очередей и pubsub). |
| `run.sh` (1108 строк) | Поднимает: Docker → venv → миграции → API → Browser Agent (Node, gRPC) → 5 воркеров (через supervisord или напрямую) → Frontend → Mini-app → Cloudflared. Включает Vision-CDP ensure-проверку. | A | После миграции упрощается: «Browser Agent» становится опциональным сервисом за флагом, Vision-CDP-чек становится опциональным. Добавляется проверка валидности `META_API_TOKEN` при старте. |
| `supervisord.conf` | Управление процессами с автоперезапуском. | A | Убираем `browser_agent` из обязательных, держим как опцию. |
| `migrations/versions/` (33 миграции) | Текущая схема. | A | Добавится 2-3 миграции: `meta_api_credentials`, `meta_api_call_log`, `add_meta_ad_status_to_ad_snapshots`. |
| `services/browser-agent/` (Node.js, ~3-5K строк TS) | gRPC-сервер для Playwright-сессии. | R | Сильно сокращается, остаётся минимальный recorder + OAuth-helper. ~80% TS-кода удаляется. |
| `proto/v1/*.proto` | gRPC контракты. | R | Удаляются scanner.proto и creator.proto. Остаётся browser_session.proto. |
| `helm/`, `k8s/` | Helm chart и K8s manifests. | A | Убираем deployment browser_agent (или делаем optional). Добавляем secret `META_API_TOKEN`. |

---

## 3. Новые возможности (категория N) — детально

Под каждой фичей: что она делает, какие tools/API, T-shirt сложность (S/M/L), связь с существующими модулями.

### N1. Conversational analytics через AI-ассистента

**Что делает.** Пользователь в Telegram или в командной палитре фронта пишет: «покажи топ-10 объявлений по spend за неделю», «по офферу DRC_CR2 — где CPL хуже всего», «какие гео отключить». AI-ассистент дёргает `ads_insights_advertiser_context`, `ads_insights_performance_trend`, `ads_insights_industry_benchmark` и наши БД-запросы, формулирует ответ. Это та самая «диалоговая аналитика» из видео @leadgenerals и пункта Варианта Г в `META_MCP_RESEARCH.md`.

**API/MCP:** `ads_insights_advertiser_context`, `ads_insights_performance_trend`, `ads_insights_industry_benchmark` (MCP-tools) + `core/meta_api/insights.py` (наш wrapper) + tool-обёртки в `core/ai_assistant/tools.py`.

**Сложность:** M (3-5 дней).

**Связь:** расширяет `core/ai_assistant/tools.py` (whitelist) и `apps/api/routers/ai.py`. Использует `CommandPalette.jsx` на фронте, поверх `core/telegram/bot_handler.py` (новые команды `/ask`, `/insight`).

### N2. Создание кампаний из Telegram-бота / фронта

**Что делает.** В Telegram: `/create_campaign DRC_CR2 from_folder /Users/.../creo` — бот собирает CampaignSpec из папки, валидирует, создаёт через Marketing API, отвечает ссылкой в Ads Manager. Или через фронт: страница ScriptsPage → форма → «Создать».

**API/MCP:** `POST /act_X/campaigns`, `POST /act_X/adsets`, `POST /act_X/adcreatives`, `POST /act_X/adimages`, `POST /act_X/adlabels`, `POST /act_X/ads`. MCP: `ads_create_campaign`, `ads_create_ad_set`, `ads_create_ad`. Все кампании пока создаются в PAUSED.

**Сложность:** L (1-2 недели на полный пайплайн, S-M если ограничиться duplicate-from-template).

**Связь:** заменяет `core/campaign_creator/runner.py`, `step_executor.py`, все `steps/*.py`. `creo_scanner.py`, `spec_builder.py`, `plan_builder.py`, `naming.py` остаются и подают spec.

### N3. Auto-clone успешных кампаний

**Что делает.** Если кампания за 24 часа показала ROI > X (наша же метрика, из БД), бот предлагает: «Дублировать с новым креативом?» Кнопка «Клонировать» в Telegram/UI → копия кампании создаётся через API с новыми креативами из соседней папки.

**API/MCP:** `POST /act_X/campaigns?source_campaign_id={ID}` (Meta поддерживает копирование), `POST /{adset_id}` (deep copy with overrides), `POST /act_X/adcreatives` для замены креатива.

**Сложность:** M (3-5 дней).

**Связь:** новый воркер или endpoint в `apps/api/routers/campaign_creator.py`; данные о «успешности» уже есть в `AdSnapshot`/`AdMetricHistory`/`Offer`.

### N4. Bulk-операции по фильтру

**Что делает.** «Pause all ads in campaign X where CPL > $5 in last 24h». «Reactivate all paused ads in offer DRC_CR2 with cost_per_lead < $3». Через UI или Telegram-команду.

**API/MCP:** `POST /act_X/?ids=[...]` (batch) или серия параллельных `POST /{ad_id}` (с rate-limit-budgeting). Можно через MCP-tool `ads_update_entity` с массивом ID.

**Сложность:** S-M (2-3 дня).

**Связь:** новый endpoint `POST /api/ads/bulk-status`, новые callback-кнопки в `core/telegram/bot_handler.py`, расширение `core/meta_api/actions.py` функцией `bulk_update_status`.

### N5. Динамическое изменение бюджета на основе ROI

**Что делает.** Адсет показывает ROI > 30% и `frequency < 2` → автоматически поднимаем дневной бюджет на 20% (но не выше M). Если ROI падает ниже 0 → даунгрейдим бюджет или паузим. Бот объявляет в Telegram, что сделал, с возможностью undo.

**API/MCP:** `POST /{adset_id}` с `daily_budget=NEW_VALUE_IN_CENTS`. Опционально проверка через `ads_insights_performance_trend`.

**Сложность:** L (1-1.5 недели, потому что нужна safety-логика: rate limits на budget changes, undo, audit log).

**Связь:** новое правило в `core/rules/evaluator.py` (но другая природа — это правило не на STOP, а на budget action) + новый воркер `apps/budget_optimizer_worker/` + миграция БД `budget_changes_log`.

### N6. Multi-account / multi-BM view

**Что делает.** Один инстанс бота мониторит N ad accounts из разных BM. Frontend получает selector аккаунта, дашборд агрегирует данные. Сейчас архитектура считает, что Vision-профиль один.

**API/MCP:** просто перебор аккаунтов в insights-запросах; новая модель `MetaAdAccount` (id, name, business_id, token_ref, status); миграция `ObserverSettings.fb_account_id` → `Offer.fb_account_id` (per offer).

**Сложность:** L (2 недели — масштабный refactor моделей и UI; самая болезненная часть после observer-замены).

**Связь:** касается всех мест, где сейчас используется `ObserverSettings.fb_account_id` (frontend ссылки на Ads Manager, queries, snapshots). По сути — крупная архитектурная задача.

### N7. Webhook-based реакция (real-time вместо поллинга)

**Что делает.** Подписываемся на Meta webhooks → реагируем в течение секунд, а не на следующем цикле observer. События: `ad_account` (`disapproved_ads`, `account_review`), `application` (`page_event`).

**API/MCP:** `POST /{app_id}/subscriptions` + endpoint `/webhooks/meta` на FastAPI. Marketing API webhooks ограничены — не все события доступны. Но `disapproved_ads` и `account_disabled` — критичные — есть.

**Сложность:** M (4-7 дней; нужен публичный endpoint, нормальная HMAC-проверка, idempotency).

**Связь:** новый роутер `apps/api/routers/webhooks.py`, новая таблица `webhook_events` (audit), интеграция в `core/observer/disable_reconciler.py` (сразу при disapproved → перевод в FAILED для disable-task если уже была попытка).

### N8. Auto-recovery: реактивация ошибочно отключённых

**Что делает.** Если объявление отключено ботом (auto-stop), а через час метрики «успокоились» (CPL вернулся в норму) — бот предлагает в Telegram включить обратно. Уже частично реализовано через enable_recommendation_worker (он сейчас работает с теми же snapshots). С API можно быстрее: сразу запрашивать свежие метрики, не дожидаясь следующего observer-цикла.

**API/MCP:** `GET /{ad_id}/insights?date_preset=last_hour` + `POST /{ad_id}` с `status=ACTIVE`.

**Сложность:** S (1-2 дня — улучшение существующей логики).

**Связь:** `core/enable_recommendations/service.py` получает новый источник, ad-hoc проверка через API.

### N9. Predictive: предсказание выгорания креатива до stop-rule

**Что делает.** Анализируем `frequency` + CTR-decay + CPM-trend; если прогноз показывает выгорание в ближайшие 2-4 часа — отправляем «pre-stop» алерт. Чтобы пользователь успел заменить креатив до того, как объявление улетит в полный STOP. Сейчас невозможно — DOM-парсинг даёт только текущий snapshot.

**API/MCP:** `ads_insights_performance_trend` (MCP) — он отдаёт уже агрегированные тренды. `ads_insights_anomaly_signal` — выдаёт «странные» паттерны.

**Сложность:** M-L (1-2 недели, потому что надо построить простую модель predict + откалибровать пороги).

**Связь:** новое правило в `core/rules/evaluator.py` (категория WARNING до настоящего WARNING), новый `AlertStage.PRE_WARNING` (или отдельный stream).

### N10. Industry benchmarks как контекст для AI-объяснений

**Что делает.** AI-объяснение алертов (уже работает через `core/ai_assistant/explain.py`) получает дополнительный контекст: «CPL твоего объявления в 2.3x выше indury-benchmark в этой вертикали». Resp_user полнее понимает, плохо ли $5 CPL — это субъективно или объективно.

**API/MCP:** `ads_insights_industry_benchmark`, `ads_insights_auction_ranking_benchmarks` (MCP-tools).

**Сложность:** S (1-2 дня).

**Связь:** `core/diagnostics.py` + prompt-инъекция в `core/ai_assistant/explain.py`.

### N11. Pixel/CAPI health monitoring

**Что делает.** Регулярно проверяем здоровье пикселя и CAPI-канала. Если события перестают приходить или drop matched events > 50% — алерт. Сейчас вообще не контролируется.

**API/MCP:** `dataset_quality_diagnostics` (4 tools из MCP-категории Dataset Quality & Diagnostics) или прямые Graph endpoints `/{pixel_id}/stats`.

**Сложность:** S-M (3-5 дней).

**Связь:** новый воркер `apps/pixel_health_worker/` или расширение `apps/health_watchdog/main.py`.

### N12. Cohort/RFM анализ офферов

**Что делает.** Сегментация офферов по recency/frequency/monetary с автоматической перекалибровкой порогов правил под сегмент. Пример: «горячие» офферы получают мягче warning-проценты, чем «холодные».

**API/MCP:** insights API (источник для cohort-расчёта), наша БД (история).

**Сложность:** L (1-2 недели — статистическая работа + UI для отображения).

**Связь:** `core/rules/types.py` обогащается `cohort_segment`; `OfferRuleStat` (уже есть) расширяется конкретными confidences по сегменту.

### N13. Audit log и rollback API-операций

**Что делает.** Каждая мутация через API (pause/activate/budget_change/create) пишется в `MetaApiAuditLog` с возможностью просмотра и отката (для тех операций, которые можно откатить).

**API/MCP:** наш собственный log + Marketing API mutations.

**Сложность:** S (1-2 дня).

**Связь:** новая модель `MetaApiAuditLog`, фронт-страница «Audit».

### N14. Page+Ad Account ban detection

**Что делает.** Регулярно проверяем `/{ad_account_id}?fields=disable_reason,account_status`. Если status переходит в RESTRICTED/DISABLED — bulk-пауза всех объявлений (для безопасности), большой алерт в Telegram. Эта возможность сейчас вообще не доступна — Vision-парсер не различает «нет данных» и «аккаунт забанен».

**API/MCP:** `GET /{ad_account_id}?fields=account_status,disable_reason,business`.

**Сложность:** S (1 день).

**Связь:** новый health-чек в `apps/health_watchdog/main.py`.

### N15. Собственный MCP-сервер поверх наших данных

**Что делает.** Идея из Варианта Г: мы упаковываем наши snapshots/alerts/disable_tasks/offers в MCP-tools (`fb_stop_bot.get_active_alerts`, `fb_stop_bot.find_ads`, `fb_stop_bot.get_offer_performance`, ...) — подключаем в Claude Desktop. Команда задаёт вопросы про наш бот, не залезая в фронт.

**API/MCP:** Python MCP SDK + 10-15 tool-обёрток поверх существующих FastAPI-эндпоинтов.

**Сложность:** S-M (1-3 дня).

**Связь:** новый `apps/mcp_server/` (или отдельный repo), читает БД через тот же `get_session_factory`.

---

## 4. Что НЕ закрывается через API/MCP

| Возможность | Почему не закрывается | Что с этим делать |
|---|---|---|
| **Реальный real-time (latency < 1 минута)** для свежих кампаний | Insights API лагает 5-15 минут, иногда до часа. Fast-stop правило (spend > порог при 0 событиях) требует sub-минутной реакции в первые 2 часа жизни объявления. | Оставить **Vision-fallback на «горячие» кампании в первые 2 часа** (или сделать гибрид: API + breakdowns=hourly для базы + спорадический browser-снапшот для критических). |
| **Скриншоты «как видит юзер»** | Marketing API возвращает URL и `image_hash`, но не финальную рендеренную карточку с реальным шрифтом/таргетом. | Vision-fallback для ручной проверки и `core/creatives/folder_opener.py`. |
| **OAuth-флоу подключения BM** | Это всегда браузерное действие. | Vision-профиль для шага «Authorize». Дальше токен живёт без браузера. |
| **Восстановление аккаунта при ban'е** | Apelaция, верификация, паспорт — браузерный/ручной процесс. | Вне сферы автоматизации. Бот только детектирует (см. N14). |
| **Платёжные методы и billing** | Marketing API даёт ограниченный read-доступ к `funding_source`, но управление методами оплаты — UI. | Ручное действие. |
| **Подсчёт реальных депозитов из трекера** | Marketing API не знает про вашу постбэк-логику с партнёрскими сетями. | Уже сейчас закрывается через `AdDepositCorrection` (`core/fake_deposits.py`) и интеграцию с CPA-трекером — это не Meta. |
| **Custom rules внутри Meta (Automated Rules)** | Marketing API даёт CRUD-доступ к Meta-native automated rules, но наши правила сложнее, чем поддерживает Meta (двухуровневая warning/stop, FSM, idempotency, integration с Telegram). | Continue хранить логику у себя, не делегировать Meta. |
| **A/B-тестирование с управлением траффиком** | Marketing API даёт `split_test_configurations`, но это сильно меньше, чем профессиональные A/B-системы. | Не предмет нашей миграции. |
| **Креативные предпросмотры с реальным placement-rendering** | Можно через `/ads_archive` API частично, но качество хуже Ads Manager. | Vision-fallback. |
| **Анализ комментариев под объявлением** | Это Graph API на Page-уровне, не Marketing. Доступно, но требует отдельных scope'ов. | Отдельный модуль (не в рамках этой миграции). |

---

## 5. Грубая оценка масштаба переписывания (человеко-дни, для одного инженера)

| Блок | Оценка | Комментарий |
|---|---|---|
| **0. Подготовка**: Meta App, System User Token, scope-настройка, `.env` | 1-2 дня | Бюрократия плюс пара PoC-запросов |
| **1. `core/meta_api/`** базовый клиент: `client.py`, `insights.py`, `actions.py`, `auth.py`, `errors.py`, `rate_limiter.py` | 4-6 дней | Включая обработку 429, exponential backoff, маппинг полей в `ScannedAdRow` |
| **2. Замена `services/browser-agent/` (scanner)**: убрать DOM-парсер, перевести `observer_worker` на API | 5-7 дней | Включая `feature-flag = both` для параллельного запуска и сверки данных |
| **3. Замена `disable_worker`**: на API + сохранение outbox-паттерна + retry/idempotency | 2-3 дня | Простая замена «как кликнуть toggle» на «как послать POST» |
| **4. Замена `enable_worker`**: аналогично | 1-2 дня | После disable_worker — почти копипаст |
| **5. Замена `creator_worker` и `core/campaign_creator/`**: декларативный план → серия API-вызовов | 7-10 дней | Здесь много полей (taxonomy, conversion locations, targeting, creatives upload), легко споткнуться о валидацию |
| **6. Webhook endpoint и handler (N7)** | 4-6 дней | Публичный URL, HMAC, idempotency, миграции |
| **7. Multi-account refactor (N6)** | 10-15 дней | Самая крупная архитектурная задача |
| **8. Frontend под мульти-аккаунт** | 5-7 дней | Selector, scoping queries, ссылки в AdsManager per account |
| **9. Bulk-операции (N4)** + dynamic budget (N5) | 5-7 дней | Включая audit log |
| **10. Conversational analytics (N1)** | 3-5 дней | Tool-обёртки + расширение `apps/api/routers/ai.py` |
| **11. Auto-clone (N3) + Predictive burnout (N9) + Industry benchmarks (N10) + Pixel health (N11) + Account ban detection (N14)** | 10-15 дней | Делается итеративно, после base-migration |
| **12. Self-hosted MCP-сервер поверх БД (N15)** | 2-3 дня | Маленький FastAPI-style сервис |
| **13. Деградация Vision до fallback** | 2-3 дня | Чисто конфигурационная работа + удаление кода |
| **14. Документация, тесты, migration plan** | 3-5 дней | Постоянный фон |
| **Итого baseline (только замена существующей функциональности)** | ~30-40 дней | Этапы 0-5 + 13 + 14 |
| **Итого с новыми возможностями** | ~70-100 дней | Полный набор N1-N15 |

---

## 6. Что остаётся ценным в текущем коде (не выбрасываем)

1. **FSM** (`core/observer/state_machine.py`). Идемпотентные переходы с UUID-токенами и `reopen_reactivated_alert_state` — это центральная нервная система бота. Никакая Meta API замена этого не даёт.
2. **Rule evaluator** (`core/rules/evaluator.py`, 607 строк, шесть стоп-правил + frequency-anomaly + funnel-лесенка + `determine_enable_recommendation_level`). Это наша бизнес-логика, накопленная на реальных аккаунтах. Marketing API даёт metric, evaluator даёт решение.
3. **Outbox pattern для disable/enable tasks** — устойчив к перезапускам и к временной недоступности API. Сохраняем.
4. **Telegram-обвязка**: `client.py` (Bot API), `renderer.py` (форматирование), `bot_handler.py` (команды и callbacks), `digest_scheduler.py`, `delivery.py` (через Redis-очередь), `messaging.py` (safe_edit_or_send), `message_refs.py` (per-stream dedup). Большая, хорошо отлаженная подсистема.
5. **БД-модели** (`core/models/__init__.py`). Минимум 25 ORM-классов, 33 миграции — это и есть «состояние правды». Большая часть сохраняется как есть.
6. **Адаптивный CPA baseline** (`use_adaptive_cpa`, `compute_adaptive_cpa_by_offer`). Накопленная статистика по объявлениям — наша ценность, Meta не подскажет.
7. **`OfferRuleStat`** — статистика confidence по правилам. Не выбрасываем.
8. **AI-ассистент** (`core/ai_assistant/`). Tool-use инфраструктура с whitelist'ом, кеш `AICache`, провайдеры Anthropic+OpenAI с fallback. Расширяется, но базис остаётся.
9. **Alert queue через Redis** (`core/alerts/`) — буферизирует Telegram-отправки, переживает рестарты.
10. **WebSocket pubsub** (`core/pubsub.py`, `apps/api/routers/ws.py`) — фронт реактивен, добавляем только новые каналы.
11. **Health watchdog** (`apps/health_watchdog/`) — инфраструктура самовосстановления, расширяется на Meta API.
12. **Frontend** — большая часть страниц и компонентов потребляет наши же snapshots, не зависит от источника данных.
13. **Fake deposits корректировка** — наша интеграция с реальным трекером депозитов, не Meta-зависимость.
14. **Naming tracker** — чисто аналитическая фича на ad_name regex.
15. **Creative folder scanner и spec builder** (`core/campaign_creator/creo_scanner.py`, `spec_builder.py`, `plan_builder.py`, `naming.py`) — работают с файлами и шаблонами имени, не браузером. Сохраняем — они подают spec в новый API-creator.
16. **Конфигурация и шифрование секретов** (`core/config.py`, `core/crypto.py` Fernet) — переиспользуем для META_API_TOKEN.
17. **Outbox-очередь PostgreSQL** (`core/task_queue/`) — переиспользуем для будущих воркеров (например, `budget_optimizer_worker`).

---

## Сводка для парент-агента

Проект на момент аудита значительно богаче, чем формальный обзор в `CLAUDE.md`. Дополнительно к описанным шести воркерам — `creator_worker`, `creator_recorder`, `health_watchdog`, а также Node.js gRPC-сервис `services/browser-agent/` (Playwright за gRPC), AI-ассистент, мини-приложение Telegram, аналитика, наименование, fake-deposits, scripts page, campaign recorder/creator, helm/k8s, Redis pubsub и многое другое.

Marketing API/MCP закрывает: parsing DOM (целиком), disable/enable воркеры (целиком), creator-flow (после переписки на API), значительную часть browser-agent (TS-кода). НЕ закрывает: real-time под минуту, скриншоты, OAuth-флоу, наш собственный rule engine, fake-deposit-корректировки.

Открывает 15+ новых возможностей, из которых самые ценные — conversational analytics (N1), webhook-based реакция (N7), multi-account (N6), auto-clone (N3), bulk operations (N4) и собственный MCP-сервер поверх данных (N15).

Минимальная замена legacy-функциональности — ~30-40 человеко-дней. Полная программа с новыми фичами — ~70-100 дней.

Главные «не выбрасывать» — FSM, rule evaluator, outbox-паттерн, Telegram-обвязка, БД-модели, AI-ассистент, alert queue, pubsub, frontend, creative-folder-scanner.
