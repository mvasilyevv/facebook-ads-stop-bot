# Архитектурная карта: FastAPI Surface (`apps/api/`)

Дата аудита: 2026-06-22  
Аудитор: claude-sonnet-4-6 (subagent)

---

## Назначение

FastAPI-приложение (`apps/api/`) — HTTP-шлюз FB Stop Bot. Обслуживает три категории клиентов: веб-дашборд (desktop React), Telegram Mini App (TMA/мобильный), и внешние интеграции (AdSet.pro postback, k8s healthz/readyz, Prometheus metrics). Не содержит бизнес-логики обнаружения/отключения — только чтение состояния из БД+Redis и постановка задач в outbox (`task_queue`).

---

## Компоненты

### Точка входа и фабрика

- **`run_api.py`** — uvicorn на порту 8000.
- **`apps/api/main.py`** — `create_app()` factory + lifespan (AsyncEngine + Redis pool, init и teardown). Модульный уровень: `app = create_app()` для uvicorn. Тестовый уровень: каждый тест вызывает `create_app()` независимо.

### Middleware (порядок применения)

Порядок КРИТИЧЕН — Starlette применяет middleware в обратном порядке добавления (последний добавленный = первый в обработке запроса):

| Порядок | Middleware | Назначение |
|---------|-----------|-----------|
| 1 (внутри) | `MetricsMiddleware` (`@app.middleware("http")`) | Prometheus counters + histograms |
| 2 | `ApiKeyAuthMiddleware` | Защита write-методов (POST/PUT/PATCH/DELETE) по X-API-Key; exempt: `/api/v1/postback/*`, `/api/tma` |
| 3 | `BodySizeLimitMiddleware` | 64 KB cap по Content-Length; exempt: `/api/tools/` (multipart) |
| 4 | `RequestIdMiddleware` | Echo X-Request-Id или генерация UUID |
| 5 (снаружи) | `CORSMiddleware` | Только если `frontend_origin` задан; `"*"` → RuntimeError на старте |

### Dependency Injection (`apps/api/deps.py`)

| Alias | Тип | Источник |
|-------|-----|----------|
| `DepEngine` | `AsyncEngine` | lifespan: `create_async_engine(settings.pg_dsn)` |
| `DepRedis` | `Redis` (aioredis) | lifespan: `from_url(settings.redis_url)` |
| `DepSettings` | `Settings` | `get_settings()` singleton |

`get_adset_pro_client()` — async generator, создаёт и закрывает httpx-клиент на каждый запрос.

### Маршрутизация

**`apps/api/routers/v1/__init__.py`** — `register_all(app)`: авто-обнаружение модулей в пакете через `pkgutil.iter_modules`, импорт через `importlib.import_module`, включение любого модуля имеющего атрибут `router: APIRouter` с `prefix="/api"`. Ошибка импорта → `logger.exception` + `continue` (роутер молча отсутствует).

**Прямые роутеры (не в v1/):**
- `routers/postback.py` → `POST /api/v1/postback/adsetpro`
- `routers/ws.py` → WebSocket `/ws/dashboard`

### Роутеры v1 (17 модулей)

| Модуль | Ключевые эндпоинты |
|--------|-------------------|
| `settings_observer.py` | `GET/PUT /settings/observer`, `PATCH /settings/observer/scanning`, `POST /settings/observer/scan-now`, `POST /settings/observer/refresh-campaigns` |
| `settings_telegram.py` | `GET/PUT /settings/telegram`, `POST /recipients/invite` |
| `settings_vision.py` | `GET/PUT /settings/vision`, `POST /vision/reconnect` |
| `settings_cabinet_autostart.py` | `GET/PUT /settings/cabinet-autostart` |
| `observer.py` | `GET /observer/status`, `GET /observer/scan-runs`, `POST /observer/start-new-cabinet-day`, `POST /observer/restart` |
| `health_details.py` | `GET /health/details` |
| `offers.py` | `GET/POST/PUT/DELETE /offers/*`, `GET /offers/compare` |
| `dashboard.py` | `GET /dashboard/ads`, `GET /dashboard/alerts`, `GET /dashboard/incidents` |
| `dashboard_stats.py` | `GET /dashboard/stats`, `GET /dashboard/batch` |
| `dashboard_timeseries.py` | `GET /dashboard/spend-history`, `GET /dashboard/chart-data` |
| `dashboard_performance.py` | `GET /dashboard/performance` |
| `ads_timeline.py` | `GET /ads/{fb_ad_id}/timeline` |
| `ads_actions.py` | `POST /dashboard/ads/{fb_ad_id}/snooze`, `POST /dashboard/ads/bulk-snooze` |
| `ads_admin.py` | `POST /dashboard/ads/bulk-delete` |
| `disable_tasks.py` | `GET/POST/DELETE/POST-retry /dashboard/disable-tasks` |
| `enable_tasks.py` | `GET /dashboard/enable-tasks` |
| `enable_recommendations.py` | `GET /dashboard/enable-recommendations`, `POST /{id}/enable` |
| `fake_deposits.py` | `GET/PUT/DELETE /fake-deposits/*` |
| `auto_enable.py` | `GET/POST/DELETE /dashboard/auto-enable-disabled/*` |
| `history.py` | `GET /history/summary`, `/timeline`, `/campaigns`, `/events`, `/offers`, `/ads` |
| `tools.py` | `POST /tools/creative-uniquify`, `GET /tools/campaign-create/folders`, `POST /tools/campaign-create/plan` |
| `ai_analyze.py` | `POST /ai/analyze` |
| `tma.py` | TMA auth + `/tma/ads`, `/tma/snooze`, `/tma/disable`, `/tma/cabinet-autostart` |

### WebSocket (`routers/ws.py`)

Эндпоинт `/ws/dashboard`. Auth ДО `accept()` по query-param `?api_key=`. На каждое соединение создаётся отдельный Redis pubsub-клиент. Два asyncio-таска: `_pubsub_loop` (Redis → WS forward) + `_heartbeat_loop` (ping каждые 30 с). Cleanup в `finally`.

### TMA-авторизация (`routers/v1/tma.py`)

1. `POST /tma/auth` — HMAC-валидация Telegram initData → `itsdangerous.URLSafeTimedSerializer` → signed token.
2. Bearer токен на money-эндпоинтах → `get_tma_principal()` — декодирует + проверяет recipient в БД (ревокация работает сразу).
3. `_tma_secret()` — берёт `settings.tma_secret` или fallback на `settings.encryption_key` (Fernet-ключ).

### Утилиты

- **`utils/partition.py`** — `default_window(hours=168)`: стандартное окно для partitioned-запросов.
- **`utils/status_mapper.py`** — конвертация `task_queue.status` (lowercase БД) ↔ frontend uppercase.
- **`utils/alert_serializer.py`** — `alert_event_row_to_out()`: alias `matched_rule_codes` → `triggered_by_rule_codes`.
- **`utils/task_serializer.py`** — `task_row_to_out()`: маппинг `next_retry_at`/`last_error`/`created_by_chat_id`.

---

## Последовательности вызовов

### Авто-стоп через UI (desktop)

```
POST /dashboard/disable-tasks
  → ApiKeyAuthMiddleware (проверка X-API-Key)
  → disable_tasks.create_disable_task()
  → core.tasks.queue.create_task(task_type='meta_api_mutation', status='pending')
  → INSERT task_queue
  → [асинхронно] meta_api_worker.claim_next_task() → ExecuteGraphCall(pause_ad)
  → core.meta_api.fsm_sync.sync_fsm_after_mutation() → ad_alert_state = 'disabled'
```

### Авто-стоп через TMA

```
POST /tma/disable
  → Bearer-токен → get_tma_principal() → recipient в БД
  → tma_claim_ad(): SELECT ad_alert_state FOR UPDATE
  → _create_disable_action(): INSERT task_queue (idempotency via open_state_token)
  → [асинхронно] meta_api_worker (аналогично desktop)
```

### Dashboard batch (главный экран)

```
GET /dashboard/batch
  → _safe_call(x6): asyncio.gather([stats, incidents, alerts, disable_tasks, enable_tasks, enable_reco])
  → каждый _safe_call: отдельный engine.connect() → SQL → маппинг
  → partial-failure: упавшая секция → empty default, остальные ОК
```

### Observer scan-now

```
POST /settings/observer/scan-now
  → ApiKeyAuthMiddleware
  → redis.publish('fb_agent:observer:trigger', payload)
  → observer_worker._wait_interruptible() прерывается → немедленный скан
```

### Postback ingestion

```
POST /api/v1/postback/adsetpro
  → (ApiKeyAuth EXEMPT для этого пути)
  → X-Postback-Secret → secrets.compare_digest (timing-safe)
  → core.adset_pro.ingest.ingest_postback(): dedup SELECT 24h + ON CONFLICT DO NOTHING
  → INSERT adsetpro_postback_events (partitioned by received_at)
```

---

## Зависимости

### Внутренние модули core/

```
apps/api/
  ├── core.tasks.queue          — create_task, claim_next, mark_*
  ├── core.dashboard.snapshot   — build_ad_snapshot, build_incidents_snapshot
  ├── core.dashboard.history_queries — история (6 endpoints)
  ├── core.dashboard.metric_aggregation — latest_per_ad_window_cte (DISTINCT ON)
  ├── core.observer.runtime     — read_observer_runtime (единая точка)
  ├── core.observer.accounts    — load_ad_account_id_for_fb_ad
  ├── core.telegram.settings_compute — bot_username Redis-cache TTL 1h
  ├── core.meta_api.client      — BrowserAgentClient (для refresh-campaigns)
  ├── core.adset_pro.ingest     — ingest_postback
  ├── core.scheduler.cabinet_autostart — read/write_autostart_config
  ├── core.creatives.service    — uniquify_creatives
  ├── core.ai_assistant.chat    — ChatSession (для /ai/analyze)
  └── core.auth.tma             — initData HMAC validation
```

### Внешние сервисы

| Сервис | Протокол | Использование |
|--------|----------|--------------|
| PostgreSQL 16 | asyncpg (через SQLAlchemy) | Все запросы к БД |
| Redis | aioredis | heartbeat чтение, pubsub publish, AI-кэш, TMA rate-limit |
| browser-agent gRPC :50051 | gRPC | refresh-campaigns, vision/reconnect |
| Telegram Bot API | httpx | settings_telegram |
| Anthropic/OpenAI (proxy) | httpx | /ai/analyze |

---

## Потоки данных

### Чтение состояния (read path)

```
Client GET → FastAPI router
  → engine.connect() as conn → conn.execute(text(sql))
  → результат → response_model (Pydantic v2)
```

Partitioned таблицы: `ad_metrics`, `alert_events`, `scan_runs`, `adsetpro_postback_events` — все SELECT обязаны фильтровать по partition-key (`cycle_ts`/`created_at`/`started_at`/`received_at`).

Кумулятивные метрики `ad_metrics` (spend нарастает в рамках cabinet day): агрегации используют `DISTINCT ON (ad_id, day)` или `DISTINCT ON (bucket, ad_id)` через `core.dashboard.metric_aggregation` — не naive SUM.

### Запись (write path)

```
Client POST/PUT/DELETE → ApiKeyAuthMiddleware → router
  → engine.begin() as conn (транзакция)
  → INSERT/UPDATE/DELETE
  → [опционально] redis.publish(channel, event)
```

Все write-операции на объявления через `task_queue` outbox (не прямые мутации Meta API из FastAPI).

### AI-анализ

```
POST /ai/analyze
  → rate-limiter Redis sliding-window (20/hour per IP)
  → cache check Redis ai:cache:analyze:{block_type}:{scope_key} TTL 600s
  → [cache miss] ChatSession.ask() → Anthropic/OpenAI
  → cache set → response
```

---

## Внешние взаимодействия

- **Telegram Mini App (TMA):** HMAC initData → signed token → Bearer. Владелец (`role='owner'`) может менять cabinet-autostart; обычный recipient — только читать и снузить/отключать.
- **AdSet.pro postback:** webhook `POST /api/v1/postback/adsetpro`, X-Postback-Secret (timing-safe), двухступенчатый dedup.
- **k8s/Docker:** `GET /healthz` (liveness, всегда 200), `GET /readyz` (readiness, SELECT 1 + Redis PING, TTL-кэш 5 с).
- **Prometheus:** `GET /metrics` — стандартная exposition, лейблы path (route template) + method + status.

---

## Инварианты

1. **Никаких прямых мутаций Meta API из FastAPI.** Все отключения/включения — через `task_queue` outbox, исполняет `meta_api_worker`.
2. **ApiKeyAuth fail-closed:** если `api_key` задан но пуст в env → 503, не 401 (невозможно работать без ключа в проде).
3. **CORS fail-fast:** `"*"` в `frontend_origin` → RuntimeError при старте `create_app()`.
4. **Partition pruning обязателен:** все SELECT к partitioned-таблицам фильтруют по partition-key.
5. **Timing-safe сравнение секретов:** `secrets.compare_digest()` для X-API-Key и X-Postback-Secret.
6. **TMA ревокация работает немедленно:** `get_tma_principal()` проверяет recipient в БД на каждый запрос.
7. **Кумулятивные метрики: DISTINCT ON, не SUM.** Нарушение = двойной счёт денег (прецедент CRIT-1 из Round 10).
8. **Partial-failure в `/dashboard/batch`:** упавшая секция → empty default, клиент получает частичные данные без 5xx.
