# DB Redesign Proposal v2.0 — Full Schema Specification

Документ на approve перед drop текущей схемы. После одобрения — становится source of truth для Alembic 0001 миграции, доменных модулей `core/models/` и cleanup-воркера.

**Объём:** 35 таблиц в Postgres + 3 namespace в Redis. Все имеют явный retention/cleanup policy.

---

## 1. Принципы

1. **Доменные модули** вместо плоского `core/models/__init__.py` (текущий — 1137 строк).
2. **Hot ephemeral → Redis** (worker_heartbeat, ai_cache, observer_runtime_status). В Postgres — только persistent state.
3. **Партиции по месяцу** для append-only растущих таблиц. `DROP PARTITION` дёшево, не блокирует hot write.
4. **Unified outbox**: один `task_queue` с дискриминатором `task_type` вместо 5 раздельных таблиц.
5. **Soft-delete = `is_active` boolean флаг**, без `deleted_at` (история через append-only события).
6. **Явные ON DELETE правила** для каждой FK. Никаких неявных каскадов.
7. **JSONB** везде (не `JSON`).
8. **Reconciler-воркер отдельно** от observer.
9. **No side effects in GET-endpoints** — все reconcile/cleanup в фоновых воркерах.
10. **Cleanup policy first-class** — каждая таблица имеет либо явный retention, либо обоснование "не растёт".

---

## 2. Структура модулей

```
core/models/
├── base.py                  # UUIDPrimaryKey, Timestamp mixin
├── settings/                # 4 таблицы
├── catalog/                 # 6 таблиц
├── observer/                # 7 таблиц
├── tasks/                   # 2 таблицы
├── telegram/                # 3 таблицы
├── creator/                 # 1 таблица
├── ad_library/              # 7 таблиц
├── meta_api/                # 3 таблицы
└── trackers/                # 2 таблицы
```

---

## 3. Партиционирование — общая стратегия

PostgreSQL native partitioning `PARTITION BY RANGE` по `created_at` (или семантически близкому полю типа `cycle_ts`, `received_at`).

**Партиционированные таблицы:**

| Таблица | Партиция по | Retention | Примерный объём/месяц |
|---|---|---|---|
| `ad_metrics` | `cycle_ts` month | 90 дней | ~290K rows / 30MB |
| `alert_events` | `created_at` month | 365 дней | ~1.5K rows / 1MB |
| `scan_runs` | `started_at` month | 30 дней | ~3K rows / 2MB |
| `meta_api_audit_log` | `created_at` month | 30 дней | зависит от трафика |
| `meta_api_webhook_event` | `received_at` month | 90 дней | зависит |
| `ad_library_snapshot` | `scanned_at` month | 14 дней | ~5-50K rows |
| `tracker_postback` | `received_at` month | 60 дней | зависит |

**Cleanup-воркер:**
- Раз в сутки в 04:00 UTC.
- `CREATE` партиции на следующий месяц (если ещё нет).
- `DROP PARTITION` старше retention.

Все партиционированные таблицы создаются как:

```sql
CREATE TABLE <name> (...) PARTITION BY RANGE (<column>);

-- Партиции создаются автоматически cleanup-воркером:
CREATE TABLE <name>_2026_05 PARTITION OF <name>
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
```

---

## 4. Retention Policy — единый конфиг

`system_config.value` под ключом `retention_policy`:

```json
{
  "ad_library_scan": "14 days",
  "ad_library_ad_orphan": "14 days",
  "ad_library_snapshot": "14 days",
  "ad_library_media_orphan": "immediate",
  "ad_metrics": "90 days",
  "alert_events": "365 days",
  "scan_runs": "30 days",
  "meta_api_audit_log": "30 days",
  "meta_api_webhook_event": "90 days",
  "tracker_postback": "60 days",
  "task_queue_completed": "30 days",
  "task_queue_failed": "90 days",
  "enable_recommendations": "30 days",
  "telegram_invites_expired": "30 days",
  "cabinet_day_archives": "365 days",
  "ad_library_winner_archive": "forever",
  "ai_cache": "redis_ttl_only"
}
```

Параметризовано — менять без миграции, только update в БД.

---

## 5. Cleanup Worker (`apps/cleanup_worker/`)

10-й воркер, запускается через supervisord. Прогон в **04:00 UTC ежедневно**.

**Алгоритм:**

1. Читает `retention_policy` из `system_config`.
2. Для партиционированных таблиц: `DROP PARTITION <name>_<YYYY>_<MM>` где партиция полностью старше retention.
3. Для непартиционированных: `DELETE FROM <table> WHERE <ts_column> < NOW() - INTERVAL '<retention>'`.
4. `ad_library_scan` старше 14 дней → DELETE → CASCADE удалит `snapshot`, `tier`, `report`, `media`.
5. `ad_library_ad` без свежих snapshot'ов → DELETE → orphan media файлы помечаются на удаление.
6. Filesystem scan `./data/ad_library_media/<country>/<id>/*` — если нет соответствующего `ad_library_media.id` в БД → `os.unlink()`.
7. `task_queue` где `status IN ('succeeded')` и `completed_at < now() - retention.completed` → DELETE.
8. `task_queue` где `status IN ('failed', 'cancelled')` и `completed_at < now() - retention.failed` → DELETE.
9. `telegram_invites` где `expires_at < now() - retention` AND `used_at IS NULL` → DELETE.
10. `CREATE` партиции на следующий месяц для всех партиционированных таблиц.
11. Запись в `system_config.value.cleanup_runs` JSONB: `{"last_run_at", "deleted_counts": {...}, "duration_ms"}`.

**Защита от ошибок:**
- Каждый DELETE — в отдельной транзакции (если упало на одной таблице — остальные продолжат).
- Лимит на один прогон: не более N млн строк (защита от runaway).
- Pubsub event `pubsub:fb_agent:cleanup:finished` для health-мониторинга.

---

## 6. Полная спецификация таблиц

### 6.1 Settings (4)

#### `observer_config`

**Назначение:** singleton статической конфигурации observer (интервалы, флаги).

**Колонки:**
```
id                              UUID         PK, default gen_random_uuid()
singleton_key                   VARCHAR(16)  NOT NULL, default 'default'
interval_seconds                INTEGER      NOT NULL, default 90
jitter_seconds                  INTEGER      NOT NULL, default 15
stale_data_threshold_seconds    INTEGER      NOT NULL, default 600
install_cost_usd                NUMERIC(10,2) NOT NULL, default 0.50
agent_commission_percent        NUMERIC(5,2) NOT NULL, default 30.0
is_scanning_enabled             BOOLEAN      NOT NULL, default true
created_at                      TIMESTAMPTZ  NOT NULL, default NOW()
updated_at                      TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:** PK (id), UNIQUE (singleton_key)

**FK:** нет

**Retention:** не растёт (1 строка). Cleanup не применяется.

**Producer:** API `settings.py` (PUT). **Consumer:** observer_worker (every cycle).

---

#### `vision_config`

**Назначение:** singleton конфиг Vision anti-detect браузера.

**Колонки:**
```
id                       UUID         PK, default gen_random_uuid()
singleton_key            VARCHAR(16)  NOT NULL, default 'default'
x_token_encrypted        TEXT         NOT NULL                      -- Fernet
profile_id               VARCHAR(64)  NOT NULL
column_widths_json       JSONB        NOT NULL, default '{}'::jsonb
created_at               TIMESTAMPTZ  NOT NULL, default NOW()
updated_at               TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:** PK (id), UNIQUE (singleton_key)

**FK:** нет

**Retention:** не растёт (1 строка).

**Producer:** API `settings.py`. **Consumer:** observer_worker, browser-agent gRPC.

---

#### `telegram_config`

**Назначение:** singleton конфиг Telegram бота.

**Колонки:**
```
id                          UUID         PK, default gen_random_uuid()
singleton_key               VARCHAR(16)  NOT NULL, default 'default'
bot_token_encrypted         TEXT         NOT NULL                      -- Fernet
chat_id                     BIGINT       NULL
forum_warning_thread_id     INTEGER      NULL
forum_stop_thread_id        INTEGER      NULL
forum_enable_thread_id      INTEGER      NULL
forum_ops_thread_id         INTEGER      NULL
poller_offset               BIGINT       NOT NULL, default 0
poller_heartbeat_at         TIMESTAMPTZ  NULL
created_at                  TIMESTAMPTZ  NOT NULL, default NOW()
updated_at                  TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:** PK (id), UNIQUE (singleton_key)

**FK:** нет

**Retention:** не растёт. `poller_heartbeat_at` обновляется часто, но строка одна.

**Producer:** telegram_poller (heartbeat), API. **Consumer:** все TG-логика.

---

#### `system_config`

**Назначение:** key-value JSONB конфиг для глобальных параметров (retention, feature flags, cleanup audit).

**Колонки:**
```
id           UUID         PK, default gen_random_uuid()
key          VARCHAR(64)  NOT NULL
value        JSONB        NOT NULL
description  TEXT         NULL
created_at   TIMESTAMPTZ  NOT NULL, default NOW()
updated_at   TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:** PK (id), UNIQUE (key), GIN (value) — для query внутри JSONB.

**FK:** нет

**Retention:** ~10-20 строк, не растёт.

**Известные ключи:**
- `retention_policy` — см. §4
- `cleanup_runs` — история прогонов cleanup_worker (audit)
- `feature_flags` — переключатели функций
- `meta_api_account` — текущий ad_account_id, app_id

---

### 6.2 Catalog (6) — иерархия объявлений

#### `offers`

**Назначение:** офферы (например DRC_CR2, KE_CR2).

**Колонки:**
```
id                UUID         PK, default gen_random_uuid()
code              VARCHAR(32)  NOT NULL
name              VARCHAR(128) NOT NULL
vertical          VARCHAR(32)  NULL                  -- gambling/nutra/etc
is_active         BOOLEAN      NOT NULL, default true
created_at        TIMESTAMPTZ  NOT NULL, default NOW()
updated_at        TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:** PK (id), UNIQUE (code), partial `ix_offers_active ON (id) WHERE is_active=true`

**FK:** нет (root catalog)

**Retention:** не растёт автоматически. Удаление офферов — ручное через UI (DELETE с проверкой FK).

**Producer:** API `offers.py`. **Consumer:** observer, observer/db_queries (load_offers), telegram_poller, всё что матчит.

---

#### `offer_rules`

**Назначение:** конфигурация 6 стоп-правил per оффер.

**Колонки:**
```
id                            UUID         PK, default gen_random_uuid()
offer_id                      UUID         NOT NULL
spend_no_event_threshold      NUMERIC(10,2) NULL
cpa_threshold                 NUMERIC(10,2) NULL
cpm_threshold                 NUMERIC(10,2) NULL
ctr_threshold                 NUMERIC(5,2)  NULL
frequency_threshold           NUMERIC(5,2)  NULL
funnel_ratio_threshold        NUMERIC(5,2)  NULL
created_at                    TIMESTAMPTZ  NOT NULL, default NOW()
updated_at                    TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:** PK (id), UNIQUE (offer_id)

**FK:** `offer_id` → `offers(id)` **ON DELETE CASCADE**

**Retention:** 1:1 с офферами, не растёт.

**Producer:** API. **Consumer:** observer (per cycle, joined с offers).

---

#### `offer_rule_stats`

**Назначение:** ML-confidence для каждой пары (offer × rule).

**Колонки:**
```
id              UUID         PK
offer_id        UUID         NOT NULL
rule_code       VARCHAR(32)  NOT NULL
confidence      NUMERIC(5,4) NOT NULL    -- 0..1
sample_size     INTEGER      NOT NULL
last_computed_at TIMESTAMPTZ NOT NULL
```

**Indexes:** PK (id), UNIQUE (offer_id, rule_code)

**FK:** `offer_id` → `offers(id)` **ON DELETE CASCADE**

**Retention:** ~6 строк × кол-во офферов. Не растёт.

**Producer:** offline скрипт `bin/recalc_rule_confidence.sh`. **Consumer:** observer (load_rule_confidence_by_offer).

---

#### `fb_campaigns`

**Назначение:** Facebook кампании (нормализованная иерархия).

**Колонки:**
```
id              UUID         PK, default gen_random_uuid()
fb_campaign_id  VARCHAR(32)  NULL                  -- Meta numeric ID
campaign_name   VARCHAR(255) NOT NULL
offer_id        UUID         NULL                  -- nullable (можем не сматчить)
is_active       BOOLEAN      NOT NULL, default true
first_seen_at   TIMESTAMPTZ  NOT NULL, default NOW()
last_seen_at    TIMESTAMPTZ  NOT NULL, default NOW()
created_at      TIMESTAMPTZ  NOT NULL, default NOW()
updated_at      TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (campaign_name) — observer'у важно при upsert
- UNIQUE (fb_campaign_id) WHERE fb_campaign_id IS NOT NULL
- `ix_fb_campaigns_offer` ON (offer_id) WHERE offer_id IS NOT NULL
- partial `ix_fb_campaigns_active` ON (id) WHERE is_active=true

**FK:** `offer_id` → `offers(id)` **ON DELETE SET NULL** (если оффер удалён — кампания остаётся unmatched).

**Retention:** не удаляется автоматически. Старые кампании остаются для исторических снимков. Опционально cleanup_worker может помечать `is_active=false` если `last_seen_at < NOW() - 90 days`.

**Producer:** observer/snapshot_writer (`_upsert_fb_campaigns`). **Consumer:** observer, dashboard, history.

---

#### `fb_adsets`

**Назначение:** Facebook ad sets.

**Колонки:**
```
id              UUID         PK, default gen_random_uuid()
campaign_id     UUID         NOT NULL
fb_adset_id     VARCHAR(32)  NULL
adset_name      VARCHAR(255) NOT NULL
is_active       BOOLEAN      NOT NULL, default true
first_seen_at   TIMESTAMPTZ  NOT NULL, default NOW()
last_seen_at    TIMESTAMPTZ  NOT NULL, default NOW()
created_at      TIMESTAMPTZ  NOT NULL, default NOW()
updated_at      TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (campaign_id, adset_name)
- UNIQUE (fb_adset_id) WHERE fb_adset_id IS NOT NULL
- `ix_fb_adsets_campaign` ON (campaign_id)
- partial `ix_fb_adsets_active` ON (id) WHERE is_active=true

**FK:** `campaign_id` → `fb_campaigns(id)` **ON DELETE CASCADE**

**Retention:** связан с кампанией (CASCADE).

**Producer:** observer/snapshot_writer. **Consumer:** observer, dashboard, history.

---

#### `fb_ads`

**Назначение:** Facebook объявления (корень всех downstream FK).

**Колонки:**
```
id                  UUID         PK, default gen_random_uuid()
adset_id            UUID         NOT NULL
fb_ad_id            VARCHAR(32)  NOT NULL              -- Meta numeric ID
ad_name             VARCHAR(255) NOT NULL
creative_hash       VARCHAR(64)  NULL                  -- для дедупа креативов
is_active           BOOLEAN      NOT NULL, default true
first_seen_at       TIMESTAMPTZ  NOT NULL, default NOW()
last_seen_at        TIMESTAMPTZ  NOT NULL, default NOW()
created_at          TIMESTAMPTZ  NOT NULL, default NOW()
updated_at          TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (fb_ad_id)
- `ix_fb_ads_adset` ON (adset_id)
- `ix_fb_ads_last_seen` ON (last_seen_at)
- partial `ix_fb_ads_active` ON (id) WHERE is_active=true
- `ix_fb_ads_creative_hash` ON (creative_hash) WHERE creative_hash IS NOT NULL

**FK:** `adset_id` → `fb_adsets(id)` **ON DELETE CASCADE**

**Retention:** не удаляется автоматически (исторические FK на ad_metrics, alert_events). Опционально cleanup_worker помечает `is_active=false` если `last_seen_at < NOW() - 90 days`.

**Producer:** observer/snapshot_writer. **Consumer:** все downstream — самая горячая таблица.

---

### 6.3 Observer (7) — наблюдаемость

#### `ad_alert_state`

**Назначение:** текущее FSM состояние per объявление (выделено из старой `ad_snapshots`).

**Колонки:**
```
id                      UUID         PK, default gen_random_uuid()
ad_id                   UUID         NOT NULL
alert_state             VARCHAR(16)  NOT NULL, default 'normal'     -- normal/warning_sent/stop_sent/claimed/disabled
current_stage           VARCHAR(16)  NULL                            -- warning/stop
open_state_token        UUID         NULL                            -- UUID для FSM идемпотентности
warning_rule_codes      JSONB        NOT NULL, default '[]'::jsonb
stop_rule_codes         JSONB        NOT NULL, default '[]'::jsonb
snoozed_until           TIMESTAMPTZ  NULL                            -- слитие с alert_snoozes
last_scan_id            BIGINT       NULL
last_transition_at      TIMESTAMPTZ  NOT NULL, default NOW()
created_at              TIMESTAMPTZ  NOT NULL, default NOW()
updated_at              TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (ad_id)
- `ix_ad_alert_state_state` ON (alert_state)
- partial `ix_ad_alert_state_open` ON (ad_id, open_state_token) WHERE open_state_token IS NOT NULL
- partial `ix_ad_alert_state_snoozed` ON (ad_id) WHERE snoozed_until IS NOT NULL
- `ix_ad_alert_state_last_scan` ON (last_scan_id)

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**

**Retention:** 1:1 с fb_ads. Не растёт автоматически. При CASCADE удалении ad — снос автоматом.

**Producer:** observer (FSM transitions), disable/enable workers (mark_succeeded). **Consumer:** dashboard, observer, telegram_poller.

---

#### `ad_metrics` (partitioned by month on `cycle_ts`)

**Назначение:** все метрики во времени (единственный источник, заменяет `ad_snapshots` + `ad_metric_history`).

**Колонки:**
```
id                              UUID         default gen_random_uuid()
ad_id                           UUID         NOT NULL
cycle_ts                        TIMESTAMPTZ  NOT NULL        -- момент сканирования
scan_id                         BIGINT       NULL

-- 18 метрических полей:
spend                           NUMERIC(12,2) NULL
reach                           INTEGER       NULL
impressions                     INTEGER       NULL
clicks                          INTEGER       NULL
cpc                             NUMERIC(10,4) NULL
ctr                             NUMERIC(7,4)  NULL
cost_per_result                 NUMERIC(10,2) NULL
cpm                             NUMERIC(10,2) NULL
frequency                       NUMERIC(7,4)  NULL
leads                           INTEGER       NULL
cost_per_lead                   NUMERIC(10,2) NULL
registrations                   INTEGER       NULL
cost_per_registration           NUMERIC(10,2) NULL
deposits                        INTEGER       NULL
outbound_clicks                 INTEGER       NULL
outbound_ctr                    NUMERIC(7,4)  NULL
landing_page_views              INTEGER       NULL
cost_per_landing_page_view      NUMERIC(10,2) NULL

created_at                      TIMESTAMPTZ  NOT NULL, default NOW()

PRIMARY KEY (id, cycle_ts)            -- composite required for partition
```

**Indexes (на каждой партиции):**
- `ix_ad_metrics_<part>_ad_cycle` ON (ad_id, cycle_ts DESC) — для query "последние N метрик per ad"
- `ix_ad_metrics_<part>_scan_id` ON (scan_id) WHERE scan_id IS NOT NULL

UNIQUE (ad_id, cycle_ts) — гарантия одна запись per (ad, момент).

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**

**Partition:** by month on `cycle_ts`. Партиция per `YYYY_MM`.

**Retention:** **90 дней**. Партиции старше — `DROP PARTITION`.

**Producer:** observer/snapshot_writer (INSERT per cycle). **Consumer:** observer (compute_baselines, frequency history), API dashboard/performance.

**Growth:** ~9.6K rows/day → ~290K/month. После 3-х месяцев — 870K строк, не более ~50MB.

---

#### `alert_events` (partitioned by month on `created_at`)

**Назначение:** append-only лог всех событий алертов (WARNING/STOP трансиции).

**Колонки:**
```
id                      UUID         default gen_random_uuid()
ad_id                   UUID         NOT NULL
stage                   VARCHAR(16)  NOT NULL              -- warning/stop
state                   VARCHAR(16)  NOT NULL              -- warning_sent/stop_sent/...
matched_rule_codes      JSONB        NOT NULL
metrics_json            JSONB        NOT NULL
open_state_token        UUID         NULL
scan_id                 BIGINT       NULL
created_at              TIMESTAMPTZ  NOT NULL, default NOW()

PRIMARY KEY (id, created_at)
```

**Indexes:**
- `ix_alert_events_<part>_ad_created` ON (ad_id, created_at DESC)
- `ix_alert_events_<part>_stage` ON (stage)
- `ix_alert_events_<part>_state` ON (state)
- `ix_alert_events_<part>_token` ON (open_state_token) WHERE open_state_token IS NOT NULL

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**

**Partition:** by month on `created_at`.

**Retention:** **365 дней**.

**Producer:** observer (на каждой FSM трансиции). **Consumer:** dashboard, history, telegram_poller.

**Growth:** ~10-50 events/day → ~1.5K/month → ~18K/year.

---

#### `scan_runs` (partitioned by month on `started_at`)

**Назначение:** трекинг каждого scan-цикла observer.

**Колонки:**
```
id                      BIGSERIAL    
scan_id                 BIGINT       NOT NULL              -- монотонный счётчик
started_at              TIMESTAMPTZ  NOT NULL
finished_at             TIMESTAMPTZ  NULL
outcome                 VARCHAR(32)  NULL                  -- success/error/timeout
rows_total              INTEGER      NULL
alerts_warning          INTEGER      NULL
alerts_stop             INTEGER      NULL
error_message           TEXT         NULL
duration_ms             INTEGER      NULL

PRIMARY KEY (id, started_at)
```

**Indexes:**
- `ix_scan_runs_<part>_scan_id` ON (scan_id)
- `ix_scan_runs_<part>_started` ON (started_at DESC)

UNIQUE (scan_id) — partition-aware.

**FK:** нет.

**Partition:** by month on `started_at`.

**Retention:** **30 дней**.

**Producer:** observer (begin + finish). **Consumer:** API observer, health_watchdog.

**Growth:** ~96 rows/day → ~3K/month.

---

#### `cabinet_day_archives`

**Назначение:** ежедневный snapshot агрегатов "по дню кабинета".

**Колонки:**
```
id                  UUID         PK, default gen_random_uuid()
started_at          TIMESTAMPTZ  NOT NULL
ended_at            TIMESTAMPTZ  NULL
reset_detected_at   TIMESTAMPTZ  NULL
total_spend         NUMERIC(12,2) NULL
total_deposits      INTEGER       NULL
total_leads         INTEGER       NULL
ad_count            INTEGER       NULL
raw_aggregate       JSONB        NOT NULL, default '{}'::jsonb
created_at          TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- `ix_cabinet_archives_started` ON (started_at DESC)
- `ix_cabinet_archives_ended` ON (ended_at DESC) WHERE ended_at IS NOT NULL

**FK:** нет.

**Retention:** **365 дней**. Cleanup-воркер: `DELETE WHERE started_at < NOW() - 365 days`.

**Producer:** API observer (POST /start-new-cabinet-day). **Consumer:** dashboard, history.

**Growth:** 1 row/day → 365/year.

---

#### `ad_deposit_corrections`

**Назначение:** ручные корректировки "ложных" депозитов per объявление.

**Колонки:**
```
id                  UUID         PK, default gen_random_uuid()
ad_id               UUID         NOT NULL
corrected_deposits  INTEGER      NOT NULL, default 0
note                TEXT         NULL
created_by          VARCHAR(64)  NULL
created_at          TIMESTAMPTZ  NOT NULL, default NOW()
updated_at          TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (ad_id)

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**

**Retention:** 1:1 с активными объявлениями. CASCADE.

**Producer:** API `fake_deposits.py`. **Consumer:** observer (load_fake_deposits), dashboard.

---

#### `ad_auto_enable_disabled`

**Назначение:** флаг "не включать автоматически" per объявление.

**Колонки:**
```
id                          UUID         PK, default gen_random_uuid()
ad_id                       UUID         NOT NULL
cabinet_day_started_at      TIMESTAMPTZ  NOT NULL
reason                      VARCHAR(64)  NULL
created_at                  TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (ad_id)
- `ix_ad_auto_disable_day` ON (cabinet_day_started_at)

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**

**Retention:** сбрасывается при cabinet_day rollover (DELETE WHERE cabinet_day_started_at != current). Управляется `enable_recommendation_worker`.

**Producer:** dashboard (toggle), enable_recommendation_worker. **Consumer:** enable_recommendation_worker.

---

### 6.4 Tasks (2) — unified outbox

#### `task_queue`

**Назначение:** единая outbox-таблица для всех типов задач (disable, enable, plan_run, meta_api_mutation, ad_library_scan).

**Колонки:**
```
id                  BIGSERIAL    PK
task_type           VARCHAR(32)  NOT NULL              -- ENUM-подобный CHECK
status              VARCHAR(16)  NOT NULL              -- draft/pending/running/succeeded/failed/retrying/cancelled
idempotency_key     VARCHAR(128) NOT NULL
payload             JSONB        NOT NULL              -- специфика типа (ad_id, plan_id, mutation kind, ...)
result              JSONB        NULL                  -- output после выполнения
attempt_count       INTEGER      NOT NULL, default 0
max_attempts        INTEGER      NOT NULL, default 5
next_retry_at       TIMESTAMPTZ  NULL
last_error          TEXT         NULL
requested_by        VARCHAR(64)  NOT NULL              -- bot_auto_stop / user@tg_id / api / ai_draft
completed_at        TIMESTAMPTZ  NULL
created_at          TIMESTAMPTZ  NOT NULL, default NOW()
updated_at          TIMESTAMPTZ  NOT NULL, default NOW()

CHECK (task_type IN ('disable', 'enable', 'plan_run', 'meta_api_mutation', 'ad_library_scan'))
CHECK (status IN ('draft', 'pending', 'running', 'succeeded', 'failed', 'retrying', 'cancelled'))
```

**Indexes:**
- PK (id)
- UNIQUE (idempotency_key)
- partial `ix_task_queue_runnable` ON (task_type, next_retry_at) WHERE status IN ('pending', 'retrying') — горячий индекс для poll
- partial `ix_task_queue_running` ON (updated_at) WHERE status='running' — для stuck-detector
- partial `ix_task_queue_draft` ON (created_at) WHERE status='draft' — для draft-cleanup
- `ix_task_queue_completed` ON (completed_at) WHERE completed_at IS NOT NULL — для retention
- `ix_task_queue_requested_by` ON (requested_by, created_at DESC)
- GIN (payload) — для query внутри JSONB (например `payload->>'ad_id'`)

**FK:** нет прямых (ad_id и т.п. — внутри payload JSONB).

**Retention (cleanup_worker):**
- `succeeded` + `completed_at < NOW() - 30 days` → DELETE
- `failed`/`cancelled` + `completed_at < NOW() - 90 days` → DELETE
- `draft` + `created_at < NOW() - 24 hours` → DELETE (protect against forgotten AI drafts)

**Reconciler (reconciler_worker, every 30s):**
- `running` + `updated_at < NOW() - 30 minutes` → переход в `retrying`, `attempt_count++`, `last_error='stuck timeout'`

**Producer:** все воркеры + API. **Consumer:** disable_worker / enable_worker / creator_worker / meta_api_worker (фильтр по task_type).

**Growth:** ~50-200 task/day. После cleanup — стабильное ~10K строк.

---

#### `enable_recommendations`

**Назначение:** event log рекомендаций на включение (не очередь; live-batch-овый).

**Колонки:**
```
id                          UUID         PK, default gen_random_uuid()
ad_id                       UUID         NOT NULL
snapshot_metrics            JSONB        NOT NULL
recommendation_level        VARCHAR(16)  NOT NULL          -- ok/warning
live_batch_started_at       TIMESTAMPTZ  NOT NULL
promoted_to_task_id         BIGINT       NULL              -- ссылка на task_queue.id
idempotency_key             VARCHAR(128) NOT NULL
created_at                  TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (idempotency_key)
- `ix_enable_recs_ad` ON (ad_id)
- `ix_enable_recs_level` ON (recommendation_level)
- `ix_enable_recs_batch` ON (live_batch_started_at DESC)
- `ix_enable_recs_promoted` ON (promoted_to_task_id) WHERE promoted_to_task_id IS NOT NULL
- `ix_enable_recs_created` ON (created_at)

**FK:**
- `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**
- `promoted_to_task_id` → `task_queue(id)` **ON DELETE SET NULL**

**Retention:** **30 дней** (cleanup_worker: `DELETE WHERE created_at < NOW() - 30 days`).

**Producer:** enable_recommendation_worker. **Consumer:** dashboard, manual promote API.

**Growth:** ~10-30 recommendations/day → ~600/month.

---

### 6.5 Telegram (3)

#### `telegram_invites`

**Назначение:** invite-коды для подключения новых TG-пользователей.

**Колонки:**
```
id              UUID         PK, default gen_random_uuid()
code            VARCHAR(32)  NOT NULL
created_by      VARCHAR(64)  NOT NULL
expires_at      TIMESTAMPTZ  NOT NULL
used_at         TIMESTAMPTZ  NULL
used_by         VARCHAR(64)  NULL
revoked_at      TIMESTAMPTZ  NULL
created_at      TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (code)
- partial `ix_invites_active` ON (id) WHERE used_at IS NULL AND revoked_at IS NULL
- `ix_invites_expires` ON (expires_at)

**FK:** нет.

**Retention:** **30 дней после expired/revoked/used**. Cleanup: `DELETE WHERE COALESCE(used_at, revoked_at, expires_at) < NOW() - 30 days`.

**Producer:** API settings. **Consumer:** telegram bot_handler.

---

#### `telegram_recipients`

**Назначение:** TG-пользователи, подключённые к боту.

**Колонки:**
```
id                  UUID         PK, default gen_random_uuid()
chat_id             BIGINT       NOT NULL
telegram_user_id    BIGINT       NOT NULL
username            VARCHAR(64)  NULL
display_name        VARCHAR(128) NULL
role                VARCHAR(16)  NOT NULL              -- owner/recipient
invite_id           UUID         NULL
created_at          TIMESTAMPTZ  NOT NULL, default NOW()
revoked_at          TIMESTAMPTZ  NULL
```

**Indexes:**
- PK (id)
- UNIQUE (chat_id, telegram_user_id)
- partial `ix_recipients_active` ON (chat_id) WHERE revoked_at IS NULL
- `ix_recipients_role` ON (role)

**FK:** `invite_id` → `telegram_invites(id)` **ON DELETE SET NULL**

**Retention:** revoked recipients остаются в БД для audit. Cleanup: `DELETE WHERE revoked_at < NOW() - 365 days`.

**Producer:** telegram bot_handler (/start с кодом), API. **Consumer:** все TG-логика.

---

#### `telegram_message_refs`

**Назначение:** ссылки на конкретные TG-сообщения для редактирования (единственное место хранения Telegram delivery state).

**Колонки:**
```
id                  UUID         PK, default gen_random_uuid()
chat_id             BIGINT       NOT NULL
ad_id               UUID         NOT NULL
incident_key        VARCHAR(64)  NOT NULL              -- (open_state_token + stage)
stream_kind         VARCHAR(16)  NOT NULL              -- warning/stop/enable/ops
message_id          BIGINT       NOT NULL
thread_id           INTEGER      NULL
sent_at             TIMESTAMPTZ  NOT NULL, default NOW()
last_edited_at      TIMESTAMPTZ  NULL
deleted_at          TIMESTAMPTZ  NULL                  -- soft-delete если сообщение удалили в TG
```

**Indexes:**
- PK (id)
- UNIQUE (chat_id, ad_id, incident_key, stream_kind)
- `ix_message_refs_ad` ON (ad_id)
- partial `ix_message_refs_active` ON (chat_id, ad_id) WHERE deleted_at IS NULL

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**

**Retention:** живёт пока жив FB ad (CASCADE).

**Producer:** observer (после отправки алерта). **Consumer:** dashboard, manual disable handler (delete-button), telegram_poller.

---

### 6.6 Creator (1)

#### `creator_plans`

**Назначение:** записанные планы создания кампаний (v2 architecture).

**Колонки:**
```
id              UUID         PK, default gen_random_uuid()
name            VARCHAR(255) NOT NULL
schema_version  INTEGER      NOT NULL, default 1
steps           JSONB        NOT NULL              -- массив PlanAction
variables       JSONB        NOT NULL, default '{}'::jsonb
description     TEXT         NULL
created_by      VARCHAR(64)  NULL
is_archived     BOOLEAN      NOT NULL, default false
created_at      TIMESTAMPTZ  NOT NULL, default NOW()
updated_at      TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (name) WHERE is_archived=false
- partial `ix_plans_active` ON (id) WHERE is_archived=false

**FK:** нет.

**Retention:** не растёт автоматически. Архивирование — ручное через UI. Архивные планы не удаляются (нужны для исторических PlanRun).

**Producer:** API campaign_recorder. **Consumer:** creator_worker (через task_queue.payload.plan_id).

**Примечание:** PlanRun теперь живёт в `task_queue` (тип `plan_run`), step_log хранится в `task_queue.payload`.

---

### 6.7 Ad Library (7)

#### `ad_library_scan`

**Назначение:** запуск сканирования (slot × country × timestamp).

**Колонки:**
```
id              UUID         PK, default gen_random_uuid()
slot            VARCHAR(64)  NOT NULL              -- "Chicken Road 2"
country         VARCHAR(2)   NOT NULL              -- ISO-2 "KE"
search_type     VARCHAR(32)  NOT NULL              -- keyword_unordered/keyword_exact_phrase/page
max_pages       INTEGER      NOT NULL, default 10
ads_count       INTEGER      NULL
status          VARCHAR(16)  NOT NULL              -- running/done/failed
started_at      TIMESTAMPTZ  NOT NULL, default NOW()
finished_at    TIMESTAMPTZ  NULL
duration_ms     INTEGER      NULL
triggered_by    VARCHAR(64)  NOT NULL              -- user@tg_id/api/cron
error_message   TEXT         NULL
```

**Indexes:**
- PK (id)
- `ix_ad_library_scan_slot_country` ON (slot, country, started_at DESC) — для query "последний scan по этому слоту"
- `ix_ad_library_scan_status` ON (status)
- `ix_ad_library_scan_started` ON (started_at)

**FK:** нет.

**Retention:** **14 дней** (cleanup_worker: `DELETE WHERE started_at < NOW() - 14 days` → CASCADE удаляет snapshot/tier/report).

**Producer:** API `/spy` (TG/TMA), ad_library_scanner. **Consumer:** dashboard, report generator.

---

#### `ad_library_ad`

**Назначение:** нормализованная запись объявления по уникальному `ad_archive_id` (один на все scan'ы).

**Колонки:**
```
ad_archive_id           BIGINT       PK              -- Meta numeric ID
page_id                 BIGINT       NOT NULL
page_name               VARCHAR(255) NOT NULL
page_url                TEXT         NULL
slot                    VARCHAR(64)  NOT NULL          -- последний known slot
country                 VARCHAR(2)   NOT NULL
started_running_on      DATE         NULL              -- когда запустили
ad_format               VARCHAR(32)  NULL              -- video/image/carousel
total_ads_in_group      INTEGER      NULL
classification_score    NUMERIC(5,4) NULL
vertical                VARCHAR(32)  NULL              -- gambling/nutra/etc
ai_summary              JSONB        NULL
first_seen_at           TIMESTAMPTZ  NOT NULL, default NOW()
last_seen_at            TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (ad_archive_id)
- `ix_ad_library_ad_slot_country` ON (slot, country, started_running_on DESC) — главный rank index
- `ix_ad_library_ad_page` ON (page_id) — для группировки по странице
- `ix_ad_library_ad_last_seen` ON (last_seen_at)
- `ix_ad_library_ad_vertical` ON (vertical) WHERE vertical IS NOT NULL

**FK:** нет.

**Retention:** **14 дней без snapshot'ов**. Cleanup: `DELETE FROM ad_library_ad WHERE NOT EXISTS (SELECT 1 FROM ad_library_snapshot WHERE ad_archive_id=ad_library_ad.ad_archive_id AND scanned_at > NOW() - 14 days) AND NOT EXISTS (SELECT 1 FROM ad_library_winner_archive WHERE ad_archive_id=ad_library_ad.ad_archive_id)`.

При DELETE — CASCADE снесёт media и winners-archive ссылки. Файлы media удалит cleanup_worker этапом filesystem-scan.

**Producer:** ad_library_scanner (UPSERT). **Consumer:** report builder, enricher.

---

#### `ad_library_snapshot` (partitioned by month on `scanned_at`)

**Назначение:** append-only снимок видимости в конкретном scan'е.

**Колонки:**
```
id              UUID         default gen_random_uuid()
scan_id         UUID         NOT NULL
ad_archive_id   BIGINT       NOT NULL
scanned_at      TIMESTAMPTZ  NOT NULL, default NOW()
is_active       BOOLEAN      NOT NULL
position_rank   INTEGER      NULL              -- позиция в выдаче
raw_json        JSONB        NOT NULL          -- сырой GraphQL response для этого ad

PRIMARY KEY (id, scanned_at)
```

**Indexes:**
- UNIQUE (scan_id, ad_archive_id) — partition-aware
- `ix_ad_library_snapshot_<part>_ad_scanned` ON (ad_archive_id, scanned_at DESC)
- `ix_ad_library_snapshot_<part>_scan` ON (scan_id)

**FK:**
- `scan_id` → `ad_library_scan(id)` **ON DELETE CASCADE**
- `ad_archive_id` → `ad_library_ad(ad_archive_id)` **ON DELETE CASCADE**

**Partition:** by month on `scanned_at`.

**Retention:** **14 дней**.

**Producer:** ad_library_scanner. **Consumer:** report builder.

---

#### `ad_library_media`

**Назначение:** скачанные медиа (видео/картинки) + транскрипты + AI summary.

**Колонки:**
```
id                  UUID         PK, default gen_random_uuid()
ad_archive_id       BIGINT       NOT NULL
media_type          VARCHAR(16)  NOT NULL              -- video/image/thumbnail
local_path          TEXT         NOT NULL              -- ./data/ad_library_media/<country>/<id>/...
sha256              VARCHAR(64)  NOT NULL
file_size_bytes     BIGINT       NOT NULL
duration_s          NUMERIC(8,2) NULL
width               INTEGER      NULL
height              INTEGER      NULL
transcript          TEXT         NULL                  -- для video, через Whisper
ai_summary          JSONB        NULL                  -- {hook, cta, tone, claims}
downloaded_at       TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (sha256) — дедуп креативов
- `ix_ad_library_media_ad` ON (ad_archive_id)
- `ix_ad_library_media_type` ON (media_type)

**FK:** `ad_archive_id` → `ad_library_ad(ad_archive_id)` **ON DELETE CASCADE**

**Retention:** связан с ad. CASCADE при удалении ad. Файлы на диске чистит cleanup_worker (filesystem-scan, удаляет orphan'ы).

**Producer:** media downloader (Stream B). **Consumer:** enricher, TMA `/spy` page.

---

#### `ad_library_tier`

**Назначение:** ранжирование S/A/B/C tier per scan.

**Колонки:**
```
id              UUID         PK, default gen_random_uuid()
scan_id         UUID         NOT NULL
ad_archive_id   BIGINT       NOT NULL
tier            VARCHAR(1)   NOT NULL              -- S/A/B/C
score           NUMERIC(8,4) NOT NULL
reason_json     JSONB        NOT NULL              -- {days_running, page_history_count, cluster_size, ...}
created_at      TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (scan_id, ad_archive_id)
- `ix_ad_library_tier_scan_tier_score` ON (scan_id, tier, score DESC) — для выдачи "топ S-tier в scan'е"
- `ix_ad_library_tier_ad` ON (ad_archive_id)

**FK:**
- `scan_id` → `ad_library_scan(id)` **ON DELETE CASCADE**
- `ad_archive_id` → `ad_library_ad(ad_archive_id)` **ON DELETE CASCADE**

**Retention:** связан со scan'ом (CASCADE через 14 дней).

**Producer:** tier_ranker (Stream C). **Consumer:** report builder, TMA `/spy` page.

---

#### `ad_library_report`

**Назначение:** финальный markdown-отчёт per scan.

**Колонки:**
```
id                          UUID         PK, default gen_random_uuid()
scan_id                     UUID         NOT NULL
top_winners_json            JSONB        NOT NULL          -- топ-5 ads с метаданными
vertical_breakdown_json     JSONB        NOT NULL
markdown_report             TEXT         NOT NULL
generated_at                TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (scan_id)
- `ix_ad_library_report_generated` ON (generated_at DESC)

**FK:** `scan_id` → `ad_library_scan(id)` **ON DELETE CASCADE**

**Retention:** связан со scan'ом (CASCADE).

**Producer:** report builder (Stream C). **Consumer:** TG /spy command, TMA `/spy` page.

---

#### `ad_library_winner_archive`

**Назначение:** топ-винеры S-tier, hold forever (защищены от cleanup).

**Колонки:**
```
id                  UUID         PK, default gen_random_uuid()
ad_archive_id       BIGINT       NOT NULL
original_scan_id    UUID         NULL                  -- может стать NULL при cleanup
slot                VARCHAR(64)  NOT NULL
country             VARCHAR(2)   NOT NULL
tier                VARCHAR(1)   NOT NULL              -- S (или принудительно повышенный)
score               NUMERIC(8,4) NOT NULL
reason              TEXT         NULL
pinned_by           VARCHAR(64)  NULL                  -- если ручной pin: user@tg_id
archived_at         TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (ad_archive_id)
- `ix_winner_archive_slot_country` ON (slot, country, archived_at DESC)
- `ix_winner_archive_pinned` ON (pinned_by) WHERE pinned_by IS NOT NULL

**FK:**
- `ad_archive_id` → `ad_library_ad(ad_archive_id)` **ON DELETE RESTRICT** — нельзя удалить ad, если он в winner archive (cleanup_worker исключает такие из cleanup)
- `original_scan_id` → `ad_library_scan(id)` **ON DELETE SET NULL** — scan может уйти через 14 дней, но winner останется

**Retention:** **forever**. Cleanup игнорирует эту таблицу.

**Producer:** tier_ranker (auto-promote S-tier), пользователь (manual pin через UI). **Consumer:** report builder (показывает archived winners в новых отчётах для эталона), TMA "коллекция".

---

### 6.8 Meta API (3)

#### `meta_api_observation`

**Назначение:** latency-tolerant данные из Marketing API (status объявления, рекламные insight'ы по запросу).

**Колонки:**
```
id                      UUID         PK, default gen_random_uuid()
ad_id                   UUID         NOT NULL
last_api_observed_at    TIMESTAMPTZ  NOT NULL
meta_ad_status          VARCHAR(32)  NOT NULL              -- ACTIVE/PAUSED/...
effective_status        VARCHAR(64)  NULL                  -- более точный статус
api_metrics             JSONB        NULL                  -- insights snapshot
account_id              VARCHAR(32)  NOT NULL
created_at              TIMESTAMPTZ  NOT NULL, default NOW()
updated_at              TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (ad_id)
- `ix_meta_observation_status` ON (meta_ad_status)
- `ix_meta_observation_account` ON (account_id, last_api_observed_at DESC)

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**

**Retention:** 1:1 с ad (CASCADE).

**Producer:** meta_api_worker (per Marketing API poll). **Consumer:** dashboard.

---

#### `meta_api_webhook_event` (partitioned by month on `received_at`)

**Назначение:** инкоминг webhook'и от Meta (ad status changes, payment events).

**Колонки:**
```
id              BIGSERIAL
event_type      VARCHAR(64)  NOT NULL
ad_account_id   VARCHAR(32)  NULL
fb_object_id    VARCHAR(64)  NULL              -- ad/campaign/account ID
payload         JSONB        NOT NULL
signature_valid BOOLEAN      NOT NULL
received_at     TIMESTAMPTZ  NOT NULL, default NOW()
processed_at    TIMESTAMPTZ  NULL
processing_error TEXT        NULL

PRIMARY KEY (id, received_at)
```

**Indexes:**
- `ix_meta_webhook_<part>_event_type` ON (event_type)
- `ix_meta_webhook_<part>_unprocessed` ON (received_at) WHERE processed_at IS NULL
- `ix_meta_webhook_<part>_account` ON (ad_account_id) WHERE ad_account_id IS NOT NULL

**FK:** нет (объекты могут не существовать на нашей стороне).

**Partition:** by month on `received_at`.

**Retention:** **90 дней**.

**Producer:** webhook_consumer (новый воркер). **Consumer:** meta_api_worker (processing), audit.

---

#### `meta_api_audit_log` (partitioned by month on `created_at`)

**Назначение:** audit-лог каждого вызова Marketing API.

**Колонки:**
```
id                  BIGSERIAL
endpoint            VARCHAR(128) NOT NULL
http_method         VARCHAR(8)   NOT NULL
http_status         INTEGER      NOT NULL
ad_account_id       VARCHAR(32)  NULL
initiated_by        VARCHAR(64)  NOT NULL          -- worker/user@tg/ai_draft
request_payload     JSONB        NULL
response_payload    JSONB        NULL
duration_ms         INTEGER      NULL
created_at          TIMESTAMPTZ  NOT NULL, default NOW()

PRIMARY KEY (id, created_at)
```

**Indexes:**
- `ix_meta_audit_<part>_created` ON (created_at DESC)
- `ix_meta_audit_<part>_initiated` ON (initiated_by, created_at DESC)
- partial `ix_meta_audit_<part>_errors` ON (created_at) WHERE http_status >= 400
- partial `ix_meta_audit_<part>_account` ON (ad_account_id, created_at) WHERE ad_account_id IS NOT NULL

**FK:** нет.

**Partition:** by month.

**Retention:** **30 дней**.

**Producer:** core/meta_api/client.py (every call). **Consumer:** audit UI, debug.

---

### 6.9 Trackers (2) — AdsetPro

#### `tracker_postback` (partitioned by month on `received_at`)

**Назначение:** raw postback'и от AdsetPro.

**Колонки (под схему AdsetPro):**
```
id                  BIGSERIAL
click_id            VARCHAR(128) NOT NULL
tracker_offer_id    VARCHAR(64)  NULL
goal                VARCHAR(32)  NULL              -- install/registration/deposit
payout              NUMERIC(10,2) NULL
currency            VARCHAR(8)   NULL
country             VARCHAR(2)   NULL
ip                  INET         NULL
user_agent          TEXT         NULL
fb_ad_id_raw        VARCHAR(64)  NULL              -- из click_id parsing
ad_id               UUID         NULL              -- сматченный
received_at         TIMESTAMPTZ  NOT NULL, default NOW()
raw_payload         JSONB        NOT NULL          -- весь остальной запрос

PRIMARY KEY (id, received_at)
```

**Indexes:**
- `ix_tracker_postback_<part>_click` ON (click_id)
- `ix_tracker_postback_<part>_ad` ON (ad_id) WHERE ad_id IS NOT NULL
- `ix_tracker_postback_<part>_goal` ON (goal)
- `ix_tracker_postback_<part>_received` ON (received_at DESC)

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE SET NULL** (postback остаётся даже если ad удалён, для audit/biling).

**Partition:** by month.

**Retention:** **60 дней**.

**Producer:** webhook endpoint `/api/trackers/adsetpro/postback`. **Consumer:** tracker_aggregator (агрегирует в tracker_aggregate).

---

#### `tracker_aggregate`

**Назначение:** агрегаты per (ad_id, country, day) — для быстрого чтения в дашборде.

**Колонки:**
```
id                  UUID         PK, default gen_random_uuid()
ad_id               UUID         NOT NULL
country             VARCHAR(2)   NOT NULL
day                 DATE         NOT NULL
installs            INTEGER      NOT NULL, default 0
registrations      INTEGER      NOT NULL, default 0
deposits            INTEGER      NOT NULL, default 0
revenue             NUMERIC(12,2) NOT NULL, default 0
roi_percent         NUMERIC(7,2) NULL
last_postback_at    TIMESTAMPTZ  NOT NULL
created_at          TIMESTAMPTZ  NOT NULL, default NOW()
updated_at          TIMESTAMPTZ  NOT NULL, default NOW()
```

**Indexes:**
- PK (id)
- UNIQUE (ad_id, country, day)
- `ix_tracker_agg_ad_day` ON (ad_id, day DESC)
- `ix_tracker_agg_day` ON (day DESC)

**FK:** `ad_id` → `fb_ads(id)` **ON DELETE CASCADE**

**Retention:** связан с ad (CASCADE). Опционально cleanup_worker может удалять `day < NOW() - 365 days`, но обычно ad умирает быстрее.

**Producer:** tracker_aggregator (rebuild from postbacks). **Consumer:** dashboard, observer (для check ROI правил).

---

## 7. Redis namespace (вместо БД)

| Pattern | TTL | Назначение | Producer |
|---|---|---|---|
| `worker:heartbeat:<name>` | 60s | `{status, last_at, pid, message}` | каждый воркер, каждые 30s |
| `ai:cache:<block_type>:<scope_key>:<hash>` | 300-900s | Кэшированный AI-ответ | core/ai_assistant/cache.py |
| `observer:runtime` | 60s | `{worker_status, active_phase, next_scan_at, last_successful_scan_at}` | observer_worker |
| `pubsub:fb_agent:scan:finished` | event | Trigger фронт refetch | observer |
| `pubsub:fb_agent:alert:created` | event | Trigger | observer |
| `pubsub:fb_agent:task:changed` | event | Trigger | disable/enable workers |
| `pubsub:fb_agent:cleanup:finished` | event | Trigger health monitor | cleanup_worker |

Все Redis-ключи автоматически чистятся через TTL — нет проблем с накоплением.

---

## 8. ER-диаграмма (textual)

```
┌─ settings/ ──────────────┐
│ observer_config          │ singletons, 1 row каждый
│ vision_config            │
│ telegram_config          │
│ system_config (key/value)│
└──────────────────────────┘

┌─ catalog/ ─────────────────────────────────────────────────────┐
│                                                                 │
│  offers ─┬─ offer_rules (1:1, CASCADE)                          │
│          └─ offer_rule_stats (CASCADE)                          │
│                                                                 │
│  fb_campaigns (offer_id SET NULL)                               │
│   └─ fb_adsets (CASCADE)                                        │
│       └─ fb_ads ─────────────────────────┐ ROOT downstream     │
└──────────────────────────────────────────┼─────────────────────┘
                                           │
                                           ↓ CASCADE везде
                          ┌────────────────┴───────────────┐
                          │                                 │
┌─ observer/ ─────────────┴───┐  ┌─ meta_api/ ──────────────┴──┐
│ ad_alert_state (1:1)        │  │ meta_api_observation (1:1)   │
│ ad_metrics (partitioned)    │  │ meta_api_webhook_event       │
│ alert_events (partitioned)  │  │   (no FK, may не match ad)   │
│ ad_deposit_corrections (1:1)│  │ meta_api_audit_log (partit.) │
│ ad_auto_enable_disabled     │  └──────────────────────────────┘
│ scan_runs (partit., no FK)  │
│ cabinet_day_archives (no FK)│  ┌─ trackers/ ────────────────────┐
└─────────────────────────────┘  │ tracker_postback (ad SET NULL) │
                                  │ tracker_aggregate (CASCADE)    │
┌─ tasks/ ────────────────────┐  └────────────────────────────────┘
│ task_queue (no direct FK,    │
│  ad_id in payload JSONB)     │  ┌─ telegram/ ────────────────────┐
│                              │  │ telegram_invites (no FK)        │
│ enable_recommendations       │  │ telegram_recipients             │
│  ├─ ad_id (CASCADE)          │  │  (invite_id SET NULL)           │
│  └─ promoted_to_task_id      │  │ telegram_message_refs           │
│     (SET NULL)               │  │  (ad_id CASCADE)                │
└──────────────────────────────┘  └─────────────────────────────────┘

┌─ creator/ ──────────────────┐
│ creator_plans               │ no FK, плана живут отдельно
│  (PlanRun → task_queue)     │
└─────────────────────────────┘

┌─ ad_library/ ───────────────────────────────────────────────┐
│                                                              │
│  ad_library_scan ─┬─ snapshot (partitioned, CASCADE × 2)    │
│                   ├─ tier (CASCADE × 2)                      │
│                   └─ report (CASCADE)                        │
│                                                              │
│  ad_library_ad ─┬─ ad_library_media (CASCADE)                │
│                 └─ ad_library_winner_archive (RESTRICT)     │
│                                                              │
│  winner_archive ─ original_scan_id (SET NULL)                │
└──────────────────────────────────────────────────────────────┘
```

---

## 9. Финальные решения

1. **Tracker** = AdsetPro. Конкретные колонки в `tracker_postback` (click_id, goal, payout, country, ...).
2. **AdLibraryWinnerArchive** = да, 7-я таблица. Автопромоут S-tier + ручной pin.
3. **Reconciler stuck-timeout** = 30 минут. `task_queue.status='running' AND updated_at < now() - 30 min` → retrying.
4. **AlertEvent retention** = 365 дней (партиции).
5. **FB hierarchy soft-delete** = `is_active` boolean. Observer фильтрует `WHERE is_active=true`.

---

## 10. План миграции (фазы)

| Фаза | Описание | Длительность |
|---|---|---|
| 0 | Approve этого документа | — |
| 1 | Backup секретов (`scripts/backup_secrets.py`) | 10 мин |
| 2 | Создать `core/models/` модули (Wave 1, 4 агента параллельно) | 3-4 часа |
| 3 | Alembic `0001_initial.py` с партициями и индексами | 1 час |
| 4 | Создать `apps/cleanup_worker/` + `apps/reconciler_worker/` | 1 день |
| 5 | Drop schema + apply 0001 + restore секретов | 30 мин |
| 6 | Migrate 9 воркеров под новые таблицы | 2-3 дня |
| 7 | Migrate API роутеры (разнести dashboard, добавить ad_library, cleanup) | 2 дня |
| 8 | Migrate frontend под новые endpoint'ы | 1 день |
| 9 | Ad Library Stream A/B/C/D (4 агента параллельно) | 2-3 дня |
| 10 | E2E smoke: оффер → scan → alert → disable → enable → /spy → report → cleanup | 1 день |

**Итого: ~10-12 дней работы.**
