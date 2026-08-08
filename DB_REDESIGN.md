# Database schema contract

Схема safety-first не поддерживает исторические runtime-контракты. Она
разворачивается одним frozen Alembic baseline только в пустой
PostgreSQL-базе. Любая историческая revision или unversioned non-empty
схема отклоняется до DDL.

Текущие источники истины:

- `core/models/` — ORM contract;
- `migrations/versions/0001_safety_first_baseline.py` и его checksum-protected
  SQL asset — frozen physical schema, functions, triggers, views и DEFAULT
  partitions;
- `core/tasks/` — queue, lease и fencing semantics;
- `core/incidents/` и `core/telegram/` — incident/notification plane;
- production-like migration tests — проверка fresh schema и явного
  отказа для legacy target.

Точная retention policy seed-ится baseline и тестом сравнивается с
`apps.cleanup_worker.retention.get_default_policy()`. Операционные таблицы,
включая `operator_revision_events`, после bootstrap пусты.

Cleanup обслуживает ровно пять partitioned-контрактов:

- `ad_metrics(cycle_ts)` — 90 дней;
- `alert_events(created_at)` — 365 дней;
- `scan_runs(started_at)` — 30 дней;
- `meta_api_audit_log(created_at)` — 30 дней;
- `adsetpro_postback_events(received_at)` — 60 дней.

`adsetpro_postback_events` — единственный durable raw postback inbox. Отдельных
postback-таблиц и неиспользуемых cabinet-day snapshot-архивов в схеме нет.

Индексы и constraints создаются в одной transactional migration без
`CONCURRENTLY`/`NOT VALID`: preflight доказывает, что target пуст и на нём нет
читателей или writers. Для будущих изменений живой baseline-базы
используются отдельные forward-only revisions с online-safe DDL.
