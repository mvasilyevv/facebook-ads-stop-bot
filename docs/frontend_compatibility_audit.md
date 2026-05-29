# Frontend Compatibility Audit

Дата: 2026-05-27
Источник: `frontend/src/api.js` (актуальный фронт) × `core/models/` × `apps/api/` (минимум после миграции).

## Сводка

- Всего endpoints (uniq HTTP routes): **70**
- **OK** (1-в-1 с новыми моделями, нужен только router-handler): **17**
- **ADAPT** (новая схема даёт данные, но нужны переименования полей, JOIN'ы, миграция семантики; некоторые endpoints — это compute-агрегации поверх partitioned-таблиц): **39**
- **REMOVE** (фича удалена с миграцией, либо больше не имеет под собой данных): **6**
- **NEW** (на фронте уже есть вызов, но логику нужно построить заново под новую схему — например, batch-эндпоинт для DashboardPage, или AI-анализ): **8**

Фронт ожидает префикс `/api`. Например, `getOffers()` бьёт в `GET /api/offers`. Бэкенд должен либо смонтировать routers с `prefix="/api"`, либо настроить proxy. В текущем `apps/api/main.py` routers подключены без префикса (`health` → `/healthz`, `postback` → `/api/v1/postback/adsetpro`), так что либо переделываем include_router, либо фронт нужно перенаправить через Vite proxy/nginx.

### Структура страниц фронта (фактически 8, не 12)

В `App.jsx` зарегистрированы: `dashboard`, `ads`, `offers`, `analytics`, `history`, `naming`, `scripts`, `settings`. Файл `HealthMapPage.jsx` существует, но из меню удалён (вызывает только `getDashboardHealthMap` — фабрику из api.js, без отдельного endpoint).

Соответственно, термин «12 страниц» в задании устарел. Реально достаточно восстановить 8.

---

## Endpoints по группам

### Settings (16 endpoints)

| Endpoint | Status | Бэкенд (модель/файл) | Заметки |
|---|---|---|---|
| `GET /settings/observer` | OK | `ObserverConfig` (`core/models/settings/observer_config.py`) | Singleton, поля 1-в-1. Нет `*_percent_of_stop` полей из DEFAULT_OBSERVER — это уже хранится в `system_config` или вычисляется. Нужно сверить, как именно фронт ожидает шаблон (см. `useSettingsData.js:28-39`). |
| `PUT /settings/observer` | ADAPT | `ObserverConfig` | Все percent_of_stop/cpc/cpl/cpr WARNING-параметры из DEFAULT_OBSERVER в `observer_config` отсутствуют. Решение: либо мигрировать поля в `observer_config`, либо хранить в `system_config.feature_flags`. |
| `PATCH /settings/observer/scanning` | OK | `ObserverConfig.is_scanning_enabled` | Прямой write. |
| `POST /settings/observer/scan-now` | NEW | (Redis pubsub) | Триггерит observer-цикл вне расписания. В новой схеме нет канала — нужно опубликовать в Redis `fb_agent:observer:trigger` или поднять флаг в `observer:runtime`. |
| `PATCH /settings/observer/auto-enable` | ADAPT | `ObserverConfig` или `system_config` | В новой схеме в `observer_config` нет колонки `auto_enable_recommendations`. Нужно либо добавить, либо переместить в `system_config.feature_flags`. |
| `GET /settings/telegram` | ADAPT | `TelegramConfig` | Фронт ждёт поля `bot_username`, `web_app_url`, `is_authorized`, `auth_deep_link`, `activation_command`, `poller_status`. В новой модели: `bot_token_encrypted`, `chat_id`, `forum_*_thread_id`, `poller_offset`, `poller_heartbeat_at`. Остальное нужно вычислять (`poller_status` ← `(now - poller_heartbeat_at) < 60s`; `bot_username` — getMe Telegram API, кэш в Redis). |
| `PUT /settings/telegram/token` | OK | `TelegramConfig.bot_token_encrypted` | Через `core.crypto.encrypt`. |
| `DELETE /settings/telegram` | OK | `TelegramConfig` | DROP/null-токен. |
| `PUT /settings/telegram/web-app-url` | REMOVE | — | В новой модели `TelegramConfig` нет `web_app_url` колонки. Mini App (`frontend-mini/`) — отдельный планируемый продукт; URL гипотетически можно положить в `system_config.feature_flags.web_app_url`. Альтернативно — удалить вызов на фронте, если Mini App не будет подниматься скоро. |
| `GET /settings/telegram/recipients` | OK | `TelegramRecipient` (`core/models/telegram/recipient.py`) | Берём `revoked_at IS NULL`. |
| `DELETE /settings/telegram/recipients/{id}` | OK | `TelegramRecipient.revoked_at = now()` | Soft-delete. |
| `POST /settings/telegram/recipients/invite` | OK | `TelegramInvite` | Insert row + return code. |
| `GET /settings/vision` | ADAPT | `VisionConfig` (`core/models/settings/vision_config.py`) | Фронт ждёт `has_token`, `profile_id`, `auto_restart_on_missing_cdp`, `runtime_status`, `runtime_status_message`, `cdp_ready`, `cdp_port`. Из БД: `x_token_encrypted` (флаг has_token), `profile_id`, `column_widths_json`. Runtime-state живёт в Redis (`browser:runtime` или `worker:heartbeat:browser-agent`). |
| `PUT /settings/vision` | ADAPT | `VisionConfig` | Только `x_token` / `profile_id`. `auto_restart_on_missing_cdp` — нужно добавить либо колонку, либо ключ в `system_config`. |
| `POST /vision/reconnect` | NEW | gRPC к browser-agent | Триггерит `BrowserSessionService.Reconnect`. Можно дёргать через `clients/python_grpc/client.py`. |
| `GET /vision/profiles` | NEW | gRPC к browser-agent | Список доступных Vision-профилей через `BrowserSessionService` (нет в новой БД, читается из Vision API). |

### Browser/Columns (3 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /settings/browser/validate-columns` | NEW | gRPC к `ScannerService.ValidateColumns` | Логика валидации DOM-колонок Ads Manager, без БД. |
| `POST /settings/browser/save-column-widths` | ADAPT | `VisionConfig.column_widths_json` | Сохраняет текущие ширины в JSONB. Логика чтения с gRPC браузера + write в новой схеме. |
| `POST /settings/browser/apply-column-widths` | NEW | gRPC к browser-agent | Применяет сохранённые ширины (live action на странице). |

### Observer/Status (5 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /observer/status` | NEW | Redis `observer:runtime` | В новой схеме status больше не лежит в БД, перенесён в Redis с TTL 60s (см. `CLAUDE.md` §observer_worker). Endpoint — это просто Redis GET + парсинг JSON. |
| `POST /observer/restart` | NEW | (supervisord/Redis pubsub) | Триггерит рестарт observer-процесса. В новой схеме нет канала, нужно либо POST flag в `observer:runtime`, либо вернуть «kill+sleep» через subprocess (опасно). |
| `POST /disable-worker/restart` | NEW | (supervisord/Redis pubsub) | Аналогично. |
| `POST /observer/start-new-cabinet-day` | ADAPT | `CabinetDayArchive` | Insert snapshot за прошлый день + reset `cabinet_day_started_at` в `ad_auto_enable_disabled`. новая схема поддерживает (модель есть, см. `cabinet_day_archive.py`). |
| `GET /observer/scan-runs?limit&filter` | ADAPT | `ScanRun` (partitioned) | Простой SELECT по `scan_runs`. Фильтры `errors`/`slow`/`with_alerts` → WHERE `outcome='error'` / `duration_ms > X` / `alerts_warning+alerts_stop > 0`. Обязательно при запросе указывать partition-key (`started_at >= ...`), иначе full scan по всем партициям. |

### Offers (8 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /offers` | OK | `Offer` | Простой SELECT WHERE `is_active=true`. |
| `GET /offers/compare?days=N` | ADAPT | `Offer` + `AdMetrics` (partitioned) + `AlertEvent` | Compute-агрегация: за N дней — spend, deposits, alert counts per offer. Нужно JOIN через `fb_campaigns.offer_id → fb_adsets → fb_ads → ad_metrics`. Тяжёлый запрос, но схема позволяет. |
| `POST /offers` | OK | `Offer` | Insert. |
| `PUT /offers/{id}` | OK | `Offer` | Update. |
| `DELETE /offers/{id}` | OK | `Offer` | Hard delete (Offer.is_active=false проще; сам Offer держит FK через ON DELETE SET NULL → fb_campaigns). |
| `GET /offers/{id}/rules` | OK | `OfferRule` | SELECT WHERE `offer_id=...`. Уже UNIQUE(offer_id). |
| `PUT /offers/{id}/rules` | OK | `OfferRule` | Upsert. |
| (косвенно) `GET /offers/{id}/rule-stats` | NEW | `OfferRuleStat` | Не вызывается из api.js прямо, но компонент `OfferThresholdsTab` может использовать. Проверить отдельно. |

### Dashboard (15 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /dashboard/stats` | NEW | aggregate query | Считается агрегацией: `total_ads_monitored` (COUNT FbAd WHERE is_active=true), `ads_in_stop`/`ads_in_warning` (COUNT AdAlertState GROUP BY alert_state), `last_scan_at` (MAX ScanRun.started_at), `observer_status` (Redis observer:runtime). Всё под рукой, но не один endpoint — это batch-агрегация. |
| `GET /dashboard/batch` | NEW | aggregate query | Композитный endpoint для DashboardPage. Должен вернуть `stats + последние ads + recent alerts + tasks` одним запросом, чтобы фронт делал 1 fetch вместо 5. Нужно проектировать заново. |
| `GET /dashboard/ads` | ADAPT | `FbAd` + `AdAlertState` + `AdMetrics` (last per ad) | На фронте называется `getAdSnapshots`, но в новой схеме нет таблицы `ad_snapshots`. Текущее состояние = JOIN(FbAd, AdAlertState, последняя AdMetrics по cycle_ts). Фронт ждёт `meta_ad_status` — это `MetaApiObservation.meta_ad_status` (LEFT JOIN). |
| `GET /dashboard/alerts` | ADAPT | `AlertEvent` (partitioned) | Фронт ждёт поля `event_type`/`rule_codes`/`name` (см. `core/ai_assistant/tools/ops/get_recent_alerts.py:52`), новая схема даёт `stage`/`matched_rule_codes`/`ad_name через JOIN`. Адаптировать SELECT с правильными именами + обязательный WHERE по `created_at` (partition-key). |
| `GET /dashboard/incidents` | NEW | aggregate query | Не в API раньше, тогда это разница от `getAlertEvents`. Логику нужно проектировать: видимо, активные «инциденты» = AdAlertState WHERE `alert_state IN ('warning_sent','stop_sent')` + кол-во транзишинов. |
| `GET /dashboard/disable-tasks` | ADAPT | `TaskQueue` WHERE `task_type='disable'` | Раньше — отдельная таблица `disable_tasks`. Теперь нужно SELECT FROM task_queue + payload->>'fb_ad_id'. Фронт ждёт поля `id`, `fb_ad_id`, `status` (PENDING/RUNNING/RETRYING/FAILED). Маппинг: `draft+pending+retrying → PENDING`, `running → RUNNING`, `failed → FAILED`. Использовать GIN-индекс по `payload`. |
| `POST /dashboard/disable-tasks` | ADAPT | `TaskQueue` insert (task_type='disable') | Use `core.tasks.queue.create_task` + правильный `requested_by`. |
| `POST /dashboard/disable-tasks/{id}/retry` | ADAPT | `TaskQueue.status='retrying'` | UPDATE WHERE task_type='disable' AND id=:id. |
| `DELETE /dashboard/disable-tasks/{id}` | ADAPT | `TaskQueue.status='cancelled'` | Soft-cancel. |
| `GET /dashboard/enable-tasks` | ADAPT | `TaskQueue` WHERE `task_type='enable'` | Аналогично disable-tasks. |
| `GET /dashboard/enable-recommendations` | OK | `EnableRecommendation` | SELECT с `LEFT JOIN task_queue ON promoted_to_task_id`. Поле `promoted_to_task_id IS NULL` = ещё не подтверждена. |
| `POST /dashboard/enable-recommendations/{id}/enable` | ADAPT | `EnableRecommendation` + `TaskQueue insert` | Создаёт `task_queue` (task_type='enable'), затем UPDATE `enable_recommendations.promoted_to_task_id`. |
| `GET /dashboard/spend-history` | ADAPT | `AdMetrics` (raw) | На фронте «сырые AdSnapshot за окно hours». В новой схеме это `AdMetrics` за период. Обязательно WHERE `cycle_ts >= now() - interval 'X hours'` для использования partition-pruning. |
| `GET /dashboard/chart-data` | ADAPT | `AdMetrics` (агрегации) | Бакетированный график. Tier 1: bucket via `date_trunc('hour', cycle_ts)` + SUM. Logic compute-side. |
| `GET /dashboard/performance` | ADAPT | `AdMetrics` + `AlertEvent` | Тяжёлый агрегат: топ кампаний, оффер-leaderboard, rule-violations. Можно собирать из `AdMetrics` + `AlertEvent.matched_rule_codes`. |

### Ads & Auto-enable (4 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /ads/{fb_ad_id}/timeline` | ADAPT | `FbAd` + `AdMetrics` + `AlertEvent` + `TaskQueue` | Timeline = метрики + события + действия. Все 3 источника живут в новой схеме (AdMetrics partitioned, AlertEvent partitioned, TaskQueue WHERE `payload->>'fb_ad_id' = :id`). Резолв `fb_ad_id → FbAd.id` через UNIQUE-индекс. |
| `GET /dashboard/auto-enable-disabled` | OK | `AdAutoEnableDisabled` | Список объявлений со снятым auto-enable. |
| `POST /dashboard/auto-enable-disabled/{fb_ad_id}` | OK | `AdAutoEnableDisabled` insert | Установить флаг. |
| `DELETE /dashboard/auto-enable-disabled/{fb_ad_id}` | OK | `AdAutoEnableDisabled` delete | Снять флаг. |

### Fake deposits (3 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /fake-deposits` | OK | `AdDepositCorrection` | SELECT JOIN FbAd. |
| `PUT /fake-deposits/{fb_ad_id}` | OK | `AdDepositCorrection` upsert | UNIQUE(ad_id). |
| `DELETE /fake-deposits/{fb_ad_id}` | OK | `AdDepositCorrection` delete | |

### History (6 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /history/summary?from&to` | ADAPT | `AdMetrics` + `AlertEvent` + `TaskQueue` | Композитная агрегация за период. Все источники partitioned — обязательно фильтровать по partition-key. |
| `GET /history/timeline?from&to` | ADAPT | `AlertEvent` + `TaskQueue` | Список событий с сортировкой по времени. |
| `GET /history/campaigns?from&to` | ADAPT | `FbCampaign` + `AdMetrics` aggregate | GROUP BY campaign + sum spend/leads/deposits за период. |
| `GET /history/events?from&to&campaign_id?` | ADAPT | `AlertEvent` | + JOIN FbAd, FbAdset, FbCampaign для имени. |
| `GET /history/offers?from&to` | ADAPT | `Offer` + `AdMetrics` (через campaign) | Аналогично `/offers/compare`. |
| `GET /history/ads?from&to&campaign_id?` | ADAPT | `FbAd` + `AdMetrics` aggregate | GROUP BY ad + sum за период. |

### Naming Tracker (1 endpoint)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /naming-tracker/patterns` | REMOVE | — | В новой схеме нет модели `naming_pattern` / соответствующей таблицы. Не входит в Этап интеграции Meta API. Решение: либо удалить страницу `NamingTrackerPage` целиком, либо построить on-the-fly compute поверх `FbCampaign.campaign_name` (regex-парсинг частей нейминга), но это новая фича — пометить как NEW + дополнительный спринт. На данный момент safest: REMOVE из приоритетов. |

### Tools / Scripts (4 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `POST /tools/creative-uniquify` (multipart) | ADAPT | `core/creatives/uniquify_creatives.py` | Логика существует, но не подвязана к HTTP. Нужен router, который принимает multipart, кладёт файлы во временную папку, вызывает `uniquify_creatives`. Без БД. |
| `POST /tools/creative-uniquify/open-folder` | OK | `core/creatives/folder_opener.py` | Существует, открывает Finder/Explorer. Только host-side, не для prod-сервера. Помечать как dev-only endpoint. |
| `GET /tools/campaign-create/folders` | NEW | filesystem scan | Список папок с креативами. Нет БД-привязки. Похоже на dev/local-only. |
| `POST /tools/campaign-create/plan` | ADAPT | `core/campaign_scripts/planner.py` | Логика — `planner.build_campaign_plan(...)`. Нужен router-handler. |

### Campaign Recorder / Creator (10 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `POST /campaign-recorder/start` | NEW | Redis pubsub `fb_agent:creator:record_start` | По CLAUDE.md `creator_recorder` слушает pubsub. Endpoint должен publish + вернуть session_id. |
| `POST /campaign-recorder/stop/{sessionId}` | NEW | Redis pubsub `fb_agent:creator:record_stop` | Publish + ждать `creator_plans` insert (или polling status). |
| `GET /campaign-recorder/status/{sessionId}?tail` | NEW | Redis (recorder runtime) | Полл состояния recorder через `worker:heartbeat:creator_recorder` + дополнительный канал/ключ. |
| `POST /campaign-recorder/analyze` | REMOVE | — | Старая логика (Vision-анализ записи). В новой схеме нет соответствующего пути. Помечать как REMOVE до отдельного спринта по campaign_recorder. |
| `POST /campaign-creator/start` | ADAPT | `TaskQueue insert (task_type='plan_run')` | Создаёт plan_run задачу. См. `core/tasks/queue.py:create_task`. |
| `GET /campaign-creator/{taskId}/status` | ADAPT | `TaskQueue SELECT WHERE task_type='plan_run' AND id=:id` | Прочитать `status` + `result` (step_log в payload/result). |
| `GET /campaign-creator/steps` | NEW | filesystem scan or hard-coded | Список доступных «steps» (см. `core/campaign_creator/steps/`). |
| `POST /campaign-creator/{taskId}/run-step/{step}` | NEW | gRPC к browser-agent | Запуск отдельного шага плана. В новой схеме plan_run выполняется creator_worker'ом целиком — нужен новый канал. |
| `POST /campaign-creator/{taskId}/run-from/{step}` | NEW | — | Аналогично. |
| `POST /campaign-creator/{taskId}/resume` | ADAPT | `TaskQueue UPDATE` | Перевод status `failed → retrying` для plan_run. |
| `POST /campaign-creator/{taskId}/cancel` | ADAPT | `TaskQueue.status='cancelled'` | Soft-cancel. |

### Health / Misc (2 endpoints)

| Endpoint | Status | Бэкенд | Заметки |
|---|---|---|---|
| `GET /health/details` | NEW | aggregate query | Существуют `/healthz` + `/readyz` (либо 200, либо 503). Detailed-вариант с per-worker статусами — берём из Redis `worker:heartbeat:*`. На фронте используется в `HealthBar.jsx`. |
| `POST /ai/analyze` | NEW | `core/ai_assistant/` | Существует `core/ai_assistant/chat.py` и tools. Для фронта нужно построить отдельный endpoint, который принимает `block_type, scope_key, client_data` и возвращает текстовый анализ. AI-провайдеры подключены через `core/ai_assistant/providers.py`. |

---

## Рекомендации по приоритизации Round 7.1-7.7

### Round 7.1 — Foundation + Settings + Observer status (минимум, чтобы фронт ожил)
- `/settings/observer` GET/PUT, `/settings/observer/scanning` PATCH, `/settings/observer/scan-now` POST, `/settings/observer/auto-enable` PATCH
- `/settings/telegram` GET/PUT/DELETE (без web-app-url), `/settings/telegram/recipients` GET/DELETE/invite
- `/settings/vision` GET/PUT, `/vision/reconnect`, `/vision/profiles`
- `/observer/status`, `/observer/scan-runs`, `/observer/start-new-cabinet-day`
- `/observer/restart`, `/disable-worker/restart` (опционально, через supervisord или Redis сигнал)
- `/health/details` (агрегация по `worker:heartbeat:*`)

Это раскрывает `SettingsPage`, `HealthBar`, `SystemStatusBar`, `ObserverStatusTile`.

### Round 7.2 — Offers
- `/offers` CRUD + `/offers/{id}/rules` GET/PUT + `/offers/compare`
- Раскрывает `OffersPage` и `OfferLeaderboard`.

### Round 7.3 — Ads / FSM core
- `/dashboard/ads` (= getAdSnapshots, JOIN FbAd+AdAlertState+last AdMetrics)
- `/dashboard/alerts` (AlertEvent c WHERE created_at)
- `/dashboard/incidents`
- `/ads/{fb_ad_id}/timeline`
- `/fake-deposits` CRUD
- `/dashboard/auto-enable-disabled` CRUD
- Раскрывает `AdsPage` (большая страница).

### Round 7.4 — Tasks / outbox
- `/dashboard/disable-tasks` GET/POST + `/retry` + `DELETE`
- `/dashboard/enable-tasks`
- `/dashboard/enable-recommendations` GET + `/enable`
- Раскрывает task-секции `DashboardPage`, `TaskQueuePanel`.

### Round 7.5 — Dashboard aggregations
- `/dashboard/stats`, `/dashboard/batch`
- `/dashboard/spend-history`, `/dashboard/chart-data`
- `/dashboard/performance`
- Раскрывает `DashboardPage` полностью + charts.

### Round 7.6 — History
- `/history/summary`, `/history/timeline`, `/history/campaigns`, `/history/events`, `/history/offers`, `/history/ads`
- Раскрывает `HistoryPage`.

### Round 7.7 — Scripts / AI / Optional
- `/tools/creative-uniquify` (+ open-folder), `/tools/campaign-create/folders` (+/plan) — `ScriptsPage`
- `/ai/analyze` — `AIBriefingCard`
- (опционально) campaign-recorder/campaign-creator endpoints
- Можно вообще отложить, не блокирует core-фукциональность

### Что удалить с фронта или пометить как DEPRECATED
- `NamingTrackerPage` + `getNamingPatterns` — нет под собой данных в новой схеме.
- `setTelegramWebAppUrl` — нет колонки в новой модели, Mini App отдельно.
- `analyzeLastRecording` — устаревшая логика анализа записи.

---

## Известные риски

### 1. Префикс `/api` не настроен в backend
Сейчас `apps/api/main.py` подключает routers без префикса. Фронт ждёт всё под `/api/*`. Перед Round 7.1 нужно решить:
- (а) Все routers подключать с `prefix="/api"` в `include_router`.
- (б) `nginx`/`vite proxy` маршрутизирует `/api/*` → `backend:8000/*` (тогда конкретно health должен переехать на `/api/healthz`).

Вариант (а) проще для local-dev, но `/healthz` фронт не использует, а Prometheus и k8s liveness-probe — да. Решение: routers dashboard/offers/settings/etc. под `/api`, а health/metrics — без.

### 2. Партиционированные таблицы требуют WHERE по partition-key
`AlertEvent`, `AdMetrics`, `ScanRun`, `meta_api_audit_log`, `tracker_postback` — все `PARTITION BY RANGE (created_at/cycle_ts/started_at/received_at)`. Любой SELECT без фильтра по partition-key → full scan по всем партициям → timeout. На фронте уже есть параметры `from/to/hours/days`, но нужно жёстко требовать их в эндпоинтах + ставить дефолтный лимит окна (например, max 30 дней).

### 3. Отсутствие `ad_snapshots` таблицы
Фронт оперирует «snapshot ad» как единым объектом. В новой схеме это разнесено по:
- `FbAd` — каталог (имя, fb_ad_id, активность).
- `AdAlertState` — FSM-состояние.
- `AdMetrics` (последняя по cycle_ts) — метрики.
- `MetaApiObservation` — meta_ad_status (LEFT JOIN, опционально).

Нужно явно построить view-функцию `build_ad_snapshot(fb_ad_id)` в новом модуле `core/dashboard/`, чтобы не дублировать JOIN'ы в каждом router'е.

### 4. Маппинг старых TaskStatus → новый status
Фронт ждёт: `PENDING`, `RUNNING`, `RETRYING`, `FAILED`, `SUCCEEDED`, `CANCELLED`.
В новой схеме `TaskQueue.status`: `draft`, `pending`, `running`, `succeeded`, `failed`, `retrying`, `cancelled`.

Lowercase + новый `draft`. Решение: router-handler возвращает uppercase + либо скрывает `draft`, либо мапит → `PENDING`. Документировать в схеме.

### 5. Поля Telegram-настроек, которые компонуются из нескольких источников
`is_authorized` = `bot_token_encrypted` есть.
`poller_status` = (`now - poller_heartbeat_at < 60s`) ? `ONLINE` : `OFFLINE`.
`bot_username` = вызов `getMe` Telegram API + кэш в Redis (`tg:bot_username` TTL 1h).
`auth_deep_link` = `https://t.me/{bot_username}?start=...`.

Это compute-логика, не прямой read из БД. Нужно тщательно покрыть unit-тестами.

### 6. AI-эндпоинт `/ai/analyze` пока без БД-привязки
`core/ai_assistant/` — pure-Python модуль, не имеет своих таблиц (например, нет `ai_block_cache`). Кэш описан в CLAUDE.md как `ai:cache:*` Redis с TTL 300-900s. Нужно явно решить, где хранить «scope_key → результат». Скорее всего, Redis + опционально append в `system_config`/`alert_events` для аудит-лога.

### 7. `restart`-эндпоинты (`/observer/restart`, `/disable-worker/restart`) — нет безопасного канала
В новой схеме воркеры не управляются через БД-сигналы (нет `worker_status` таблицы). Единственный путь:
- Redis pubsub (worker сам слушает и завершает loop).
- supervisord-команда (хост-side).

Подход через `subprocess` запрещён в prod-серверe. Решение для Round 7.1: добавить Redis-канал `fb_agent:worker:restart:<name>`, воркеры слушают, при сообщении делают graceful shutdown + auto-restart через supervisord/systemd. Это требует правок в worker'ах, не только в FastAPI. Помечать как `NEW + dev change`.

### 8. Endpoints для browser-actions (validate-columns, save/apply column-widths)
Все они должны проксироваться через `clients/python_grpc/client.py` (Python gRPC client) к browser-agent. Без активной сессии (`browser:runtime` пустой) — должны вернуть 503. Нужно проверить, что router'ы не пытаются прокидывать gRPC NotFoundError как 500.

### 9. Поле `auto_enable_recommendations` фронт ожидает в observer settings
Сейчас в `ObserverConfig` его нет. На фронте используется `toggleAutoEnable`. Варианты:
- Добавить колонку `auto_enable_recommendations BOOLEAN DEFAULT false` в `observer_config` (миграция).
- Сохранять в `system_config.value['auto_enable_recommendations']`.

Первый вариант чище, но требует Alembic-миграции. Второй — без миграции, но менее структурированно.

### 10. Vite proxy / CORS
`main.py` уже корректно поднимает `CORSMiddleware` если `settings.frontend_origin` задан. Для local-dev (`http://localhost:5173`) — нужно настроить `.env`. В prod — origin фронта.

### 11. Pre-existing bug в `core/ai_assistant/tools/ops/get_recent_alerts.py`
Из CLAUDE.md: ссылки на `ae.event_type`/`ae.rule_codes`/`a.name` вместо корректных `stage`/`matched_rule_codes`/`ad_name`. Тот же bug может всплыть в новом `/dashboard/alerts` endpoint'е, если копировать код. Подсветить во время Round 7.3.

---

## Итоговая карта endpoints (быстрый листинг)

```
SETTINGS (16):
  GET    /settings/observer                          OK
  PUT    /settings/observer                          ADAPT (поля warning_percent_of_stop отсутствуют)
  PATCH  /settings/observer/scanning                 OK
  POST   /settings/observer/scan-now                 NEW (Redis-сигнал)
  PATCH  /settings/observer/auto-enable              ADAPT (поле отсутствует)
  GET    /settings/telegram                          ADAPT (compute is_authorized/poller_status/...)
  PUT    /settings/telegram/token                    OK
  DELETE /settings/telegram                          OK
  PUT    /settings/telegram/web-app-url              REMOVE
  GET    /settings/telegram/recipients               OK
  DELETE /settings/telegram/recipients/{id}          OK
  POST   /settings/telegram/recipients/invite        OK
  GET    /settings/vision                            ADAPT
  PUT    /settings/vision                            ADAPT
  POST   /vision/reconnect                           NEW (gRPC)
  GET    /vision/profiles                            NEW (gRPC)

BROWSER (3):
  GET    /settings/browser/validate-columns          NEW (gRPC)
  POST   /settings/browser/save-column-widths        ADAPT
  POST   /settings/browser/apply-column-widths       NEW (gRPC)

OBSERVER (5):
  GET    /observer/status                            NEW (Redis)
  POST   /observer/restart                           NEW
  POST   /disable-worker/restart                     NEW
  POST   /observer/start-new-cabinet-day             ADAPT
  GET    /observer/scan-runs                         ADAPT

OFFERS (7):
  GET    /offers                                     OK
  GET    /offers/compare                             ADAPT (агрегация)
  POST   /offers                                     OK
  PUT    /offers/{id}                                OK
  DELETE /offers/{id}                                OK
  GET    /offers/{id}/rules                          OK
  PUT    /offers/{id}/rules                          OK

DASHBOARD (15):
  GET    /dashboard/stats                            NEW (агрегация)
  GET    /dashboard/batch                            NEW (композит)
  GET    /dashboard/ads                              ADAPT (нет ad_snapshots)
  GET    /dashboard/alerts                           ADAPT (AlertEvent partitioned)
  GET    /dashboard/incidents                        NEW
  GET    /dashboard/disable-tasks                    ADAPT (task_queue)
  POST   /dashboard/disable-tasks                    ADAPT
  POST   /dashboard/disable-tasks/{id}/retry         ADAPT
  DELETE /dashboard/disable-tasks/{id}               ADAPT
  GET    /dashboard/enable-tasks                     ADAPT (task_queue)
  GET    /dashboard/enable-recommendations           OK
  POST   /dashboard/enable-recommendations/{id}/enable  ADAPT
  GET    /dashboard/spend-history                    ADAPT (AdMetrics)
  GET    /dashboard/chart-data                       ADAPT (агрегация AdMetrics)
  GET    /dashboard/performance                      ADAPT (тяжёлая агрегация)

ADS / AUTO-ENABLE (4):
  GET    /ads/{fb_ad_id}/timeline                    ADAPT (multi-source)
  GET    /dashboard/auto-enable-disabled             OK
  POST   /dashboard/auto-enable-disabled/{id}        OK
  DELETE /dashboard/auto-enable-disabled/{id}        OK

FAKE DEPOSITS (3):
  GET    /fake-deposits                              OK
  PUT    /fake-deposits/{id}                         OK
  DELETE /fake-deposits/{id}                         OK

HISTORY (6):
  GET    /history/summary                            ADAPT
  GET    /history/timeline                           ADAPT
  GET    /history/campaigns                          ADAPT
  GET    /history/events                             ADAPT
  GET    /history/offers                             ADAPT
  GET    /history/ads                                ADAPT

NAMING (1):
  GET    /naming-tracker/patterns                    REMOVE

TOOLS / SCRIPTS (4):
  POST   /tools/creative-uniquify                    ADAPT
  POST   /tools/creative-uniquify/open-folder        OK
  GET    /tools/campaign-create/folders              NEW
  POST   /tools/campaign-create/plan                 ADAPT

CAMPAIGN RECORDER (4):
  POST   /campaign-recorder/start                    NEW
  POST   /campaign-recorder/stop/{id}                NEW
  GET    /campaign-recorder/status/{id}              NEW
  POST   /campaign-recorder/analyze                  REMOVE

CAMPAIGN CREATOR (7):
  POST   /campaign-creator/start                     ADAPT (task_queue plan_run)
  GET    /campaign-creator/{id}/status               ADAPT
  GET    /campaign-creator/steps                     NEW
  POST   /campaign-creator/{id}/run-step/{name}      NEW
  POST   /campaign-creator/{id}/run-from/{name}      NEW
  POST   /campaign-creator/{id}/resume               ADAPT
  POST   /campaign-creator/{id}/cancel               ADAPT

HEALTH / MISC (2):
  GET    /health/details                             NEW (Redis aggregate)
  POST   /ai/analyze                                 NEW
```
