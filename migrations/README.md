# Migration contract

`0001_safety_first_baseline` is the only bootstrap revision. It is online-only,
irreversible and accepts exactly two target states:

- an empty PostgreSQL `public` schema;
- a complete database already stamped `0001_safety_first_baseline`.

Historical revisions and unversioned non-empty schemas fail before DDL. The
migrator never stamps, drops, upgrades or converts legacy data. Production
adoption therefore uses a separate empty database and an explicitly reviewed
export/recreate/import cutover; the incumbent database remains untouched.
After `upgrade head`, the release migrator runs `alembic check`; an exact
baseline stamp with extra legacy tables or ORM drift is rejected rather than
accepted as a no-op.

The accompanying SQL asset is frozen and SHA-256 protected. It contains the
PostgreSQL 16 physical schema, migration-owned functions/triggers/view and only
date-independent DEFAULT partitions. Alembic executes it through the existing
SQLAlchemy/asyncpg connection; no `psql` binary exists in the runtime path.
`cleanup_worker` creates current/next month partitions for `ad_metrics`,
`alert_events`, `scan_runs`, `meta_api_audit_log` and the sole postback inbox
`adsetpro_postback_events` on every startup before retention work.

Do not regenerate or edit the asset from a live database. A replacement
baseline requires a disposable PostgreSQL 16 instance, a clean ORM drift check,
zero operational rows, schema round-trip verification and a new checksum. Once
this baseline is released, normal schema evolution uses reviewed forward-only
revisions; it does not rewrite `0001`.
