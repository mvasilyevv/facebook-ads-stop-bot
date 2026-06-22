# Карта архитектуры: слой данных (модели, миграции, агрегации спенда)

Дата: 2026-06-22. Подсистема: `core/models/`, `migrations/versions/`, `core/dashboard/`, `core/adset_pro/`.

## Назначение

Слой данных решает три задачи:

1. **Схема и целостность** (`core/models/` — 35 ORM-моделей по доменам; `migrations/versions/` — Alembic-цепочка 0001→0024). Описывает каталог (offers→fb_campaigns→fb_adsets→fb_ads), FSM-состояние (ad_alert_state), временные ряды (ad_metrics, alert_events, scan_runs — все partitioned by RANGE), outbox (task_queue), трекер (adsetpro_postback_events partitioned + tracker_aggregate).
2. **Корректная агрегация кумулятивных метрик** (`core/dashboard/`). `ad_metrics` хранит КУМУЛЯТИВНЫЕ снимки спенда (растут за сутки, обнуляются в полночь кабинета). Слой даёт переиспользуемые CTE-фрагменты (`metric_aggregation.py`), которые берут «последний снимок на (ad × день)» вместо naive SUM — это и есть money-граница: ошибка тут завышает spend в десятки раз.
3. **Ingest и агрегация трекера** (`core/adset_pro/`). Приём postback'ов AdSet.pro с двухступенчатым дедупом, ротация ключей через зашифрованный singleton, пересчёт `tracker_aggregate` per (ad, country, day) идемпотентным absolute-recompute, чтение `external_deposits` для защиты от ложного STOP.

## Компоненты

### core/models/ (схема)
- **Партиционированные (RANGE)**: `ad_metrics` (cycle_ts, retention 90д), `alert_events` (created_at), `scan_runs` (started_at), `adsetpro_postback_events` (received_at, retention 60д), `meta_api_webhook_event`, `meta_api_audit_log`. Каждая несёт PK, включающий партиционный ключ, и индекс `(business_key, partition_key)`.
- **Каталог**: FK-цепочка `offers ←(SET NULL) fb_campaigns ←(CASCADE) fb_adsets ←(CASCADE) fb_ads`. Удаление оффера оставляет кампанию unmatched; удаление любого узла каскадит вниз до ad'ов и их метрик/алертов.
- **tracker_aggregate**: НЕ партиционирована. UUID PK + `UNIQUE(ad_id, country, day)` (точка идемпотентности UPSERT), FK ad_id→fb_ads CASCADE.
- **adsetpro_credentials**: singleton, Fernet-шифр поверх BYTEA.

### core/dashboard/
- **metric_aggregation.py** — две чистые функции, возвращающие текст CTE:
  - `latest_per_ad_window_cte` — `DISTINCT ON (ad_id [, bucket]) ... ORDER BY ..., cycle_ts DESC`. Для окна в пределах суток или per-bucket.
  - `latest_per_ad_per_day_cte` — `DISTINCT ON (ad_id, date_trunc('day', cycle_ts))`. Для многодневных окон (складывает дневные итоги через посуточные reset'ы). Партиционный фильтр `cycle_ts BETWEEN :from AND :to` зашит внутрь CTE.
- **snapshot.py** — `build_ad_snapshot` / `build_ad_snapshot_with_cursor` / `build_incidents_snapshot`. Композитный SELECT: fb_ads LEFT JOIN alert_state + 4 LATERAL'а (последняя метрика, последний alert_event, FILTER-агрегация last_warning/last_stop) + каталог + meta_observation. Keyset-пагинация по (last_seen_at DESC NULLS LAST, id ASC). transitions_count для инцидентов — batch unnest (анти-N+1).
- **history_queries.py** — 8 SQL-функций HistoryPage (summary/timeline/campaigns/events/offers/ads). Все метрические агрегации через `latest_per_ad_per_day_cte`; алерты считаются ОТДЕЛЬНОЙ CTE (не прямым JOIN к метрикам — иначе fan-out ad×alert дробит SUM(spend)).
- **cabinet_spend.py** — `current_day_spend`: «спенд текущих суток кабинета с нуля». Per-account граница полуночи (CASE по ad_account_id) + LATERAL latest-per-ad с полом по границе. Явный `prune_floor` сохраняет partition pruning при per-row CASE-границе.

### core/adset_pro/
- **ingest.py** — `ingest_postback`: (1) резолв fb_ad_fk через `fb_ads.fb_ad_id` (отдельным соединением), (2) пред-INSERT SELECT по (click_id, event_type) в окне 24h, (3) INSERT `ON CONFLICT (uq_adsetpro_postback_dedup) DO NOTHING`. Два уровня дедупа.
- **aggregator.py** — `aggregate_postback_events`: один SQL `WITH normalized/filtered → INSERT ... SELECT ... GROUP BY ad_id,country,day ON CONFLICT DO UPDATE SET = пересчёт`. Absolute recompute целых UTC-дней, перекрытых окном. `RETURNING (xmax=0)` различает INSERT/UPDATE.
- **queries.py** — `load_external_deposits[_batch]`: COUNT депозитных событий per fb_ad_id в окне 24h (is_duplicate=FALSE). Потребитель — evaluator через `RuleContext.external_deposits` (используется как булев гейт `>= 1`).
- **credentials.py** — `load/resolve/upsert_adsetpro_credentials`: БД-first с .env-фолбэком, ротация без рестарта.

## Последовательности вызовов

### Запись метрик (горячий путь, ~90с)
```
observer_worker → process_scan_rows (pipeline.py)
  → load_external_deposits_batch(engine, fb_ad_ids)      [core/adset_pro/queries]
       SELECT COUNT FROM adsetpro_postback_events WHERE fb_ad_id=ANY ... received_at>=since
  → writers.upsert_catalog_hierarchy / insert_metrics    [INSERT ad_metrics(cycle_ts)]
```

### Ingest postback (HTTP)
```
POST /api/v1/postback/adsetpro
  → resolve_adsetpro_postback_secret(engine)             [БД→.env, timing-safe compare]
  → ingest_postback(engine, event)
       _resolve_fb_ad_fk()  (conn #1)
       BEGIN: SELECT dedup-window (conn #2) → INSERT ON CONFLICT DO NOTHING
```

### Агрегация трекера (tracker_aggregator_worker, ~5мин)
```
worker → aggregate_postback_events(window_start, window_end)
  → _utc_day_bounds → [day_floor, day_ceil)
  → BEGIN: _AGGREGATE_SQL (recompute + UPSERT tracker_aggregate)
```

### Чтение аналитики (FastAPI)
```
GET /dashboard/ads        → build_ad_snapshot (snapshot.py)
GET /dashboard/incidents  → build_incidents_snapshot (+ batch transitions COUNT)
GET /history/*            → history_queries.fetch_* (latest_per_ad_per_day_cte)
GET /offers/compare       → latest_per_ad_per_day_cte
GET /dashboard/performance, /chart-data → DISTINCT ON inline (тот же паттерн)
GET /dashboard/spend (cabinet) → current_day_spend (cabinet_spend.py)
```

### Управление партициями (cleanup_worker, 04:00 UTC)
```
worker → drop_old_partitions (по retention из system_config) → DROP TABLE целиком устаревших
       → create_next_partition_if_missing (текущий + следующий месяц для всех _PARTITIONED)
```
Первичный seed партиций: `scripts/apply_schema.py::_create_first_partitions` (fresh bootstrap) + migration 0001 (только adsetpro_postback_events).

## Зависимости

- **Подсистема зависит от**: SQLAlchemy 2.x async + asyncpg; `core.crypto` (Fernet для credentials); `core.config.get_settings` (.env-фолбэк); `core.tasks.channel` (disable/enable SQL-предикаты в history_queries); `core.adset_pro.queries.DEPOSIT_EVENT_TYPES` (общий контракт с aggregator и evaluator).
- **От подсистемы зависят**: все dashboard/history-роутеры FastAPI; observer pipeline (external_deposits, запись ad_metrics); evaluator (через RuleContext.external_deposits); digest_builder; frequency_analyzer; tracker_aggregator_worker; cleanup_worker.
- **Контракты-инварианты**: имена метрических колонок ad_metrics ↔ `_DEFAULT_METRIC_COLUMNS` / `_METRIC_FIELDS`; `DEPOSIT_EVENT_TYPES` единый для ingest-классификации, aggregator и evaluator; партиционный ключ обязателен во WHERE на каждой partitioned-таблице.

## Потоки данных

- **ScannedAdRow → ad_metrics** (кумулятивный snapshot per scan) → CTE latest-per-(ad,day) → SUM → JSON для фронта.
- **AdSet.pro postback (JSON) → adsetpro_postback_events** (raw, partitioned) → (а) COUNT в evaluator (защита от STOP), (б) recompute → tracker_aggregate (per ad×country×day).
- **alert_events** (append-only, partitioned) → LATERAL/CTE last_warning/last_stop/transitions для snapshot и history.
- **Redis** (через cabinet_spend косвенно): `meta:tz` карта таймзон кабинетов (читается в роутере, не в самом cabinet_spend — туда приходит готовый tz_map).

## Внешние взаимодействия

- **Postgres**: единственное хранилище. asyncpg-quirk: `::uuid` cast в параметризованном text() не поддерживается → UUID конвертируется в Python-объект до params; CAST(:p AS timestamptz) вместо `:p::ts` (cabinet_spend).
- **Redis**: tz-карта/observer:runtime читаются роутерами, не самим data-слоем.
- **HTTP**: AdSet.pro шлёт postback → ingest. Исходящий postback (outgoing.py) — вне ядра данных.
- **Шифрование**: adsetpro_credentials Fernet поверх BYTEA.

## Инварианты (и где хрупкие)

1. **Кумулятив agg**: spend суммируется ТОЛЬКО через latest-per-(ad,day). Держится дисциплинированно во всех 8+ местах. Хрупкость: правило живёт в комментариях и code-review, а не в типах — новый naive SUM пройдёт компиляцию.
2. **Partition pruning**: каждый запрос к ad_metrics/alert_events/adsetpro_postback_events несёт фильтр по партиционному ключу. Проверено — выполняется везде.
3. **Идемпотентность agg**: absolute-recompute per UTC-день → повторный/перекрывающийся прогон сходится. Держится.
4. **Дедуп ingest**: двухступенчатый (SELECT-окно + UNIQUE). Хрупкость: ключ дедупа `(click_id, event_type)` — для event_type, который ЛЕГИТИМНО повторяется по тому же click_id (redep), подавляет реальное событие (см. findings).
5. **DEPOSIT_EVENT_TYPES единый**: assert на старте ловит пересечение списков. Держится.
6. **FK CASCADE цепочка**: удаление ad'а уносит метрики/алерты/агрегаты. Сознательно (retention), но tracker_aggregate-история теряется при удалении ad — не защищена winner-archive-паттерном.
7. **external_deposits как булев гейт**: evaluator смотрит `>= 1`, не точное число → недосчёт depозитов в ingest безопасен для STOP-решений, но врёт в revenue-аналитике tracker_aggregate.
