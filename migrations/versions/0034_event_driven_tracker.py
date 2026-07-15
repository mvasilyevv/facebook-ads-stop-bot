"""Event-driven AdSet.pro inbox, click projection and durable processing queue.

Revision ID: 0034_event_driven_tracker
Revises: 0033_tracker_agg_setnull
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_event_driven_tracker"
down_revision = "0033_tracker_agg_setnull"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Boundary used by tracker-driven cancellation and meta_api_worker under the
    # same per-ad advisory lock.
    op.add_column(
        "task_queue",
        sa.Column("external_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Older installations may contain the naming-convention-rendered double
    # prefix produced by migration 0025, while a previous downgrade of this
    # migration restores the canonical short name.  Accept both shapes.
    op.execute(
        "ALTER TABLE task_queue DROP CONSTRAINT IF EXISTS ck_task_queue_ck_task_queue_task_type"
    )
    op.execute("ALTER TABLE task_queue DROP CONSTRAINT IF EXISTS ck_task_queue_task_type")
    op.execute(
        """
        ALTER TABLE task_queue ADD CONSTRAINT ck_task_queue_task_type CHECK (
            task_type IN ('disable', 'enable', 'plan_run', 'meta_api_mutation',
                          'ad_library_scan', 'campaign_create', 'tracker_event_process')
        ) NOT VALID
        """
    )
    op.execute("ALTER TABLE task_queue VALIDATE CONSTRAINT ck_task_queue_task_type")

    op.add_column(
        "adsetpro_postback_events",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "adsetpro_postback_events",
        sa.Column("source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "adsetpro_postback_events",
        sa.Column("provider_event_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "adsetpro_postback_events",
        sa.Column("attribution_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "adsetpro_postback_events",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "adsetpro_postback_events",
        sa.Column("last_error", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "adsetpro_postback_events",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE adsetpro_postback_events
        SET occurred_at = received_at,
            source = 'adsetpro',
            fb_ad_id = NULLIF(COALESCE(raw_json->>'sub8', raw_json->>'ext_sub8'), ''),
            fb_ad_fk = NULL,
            attribution_status = 'unmatched',
            event_type = CASE lower(trim(event_type))
                WHEN 'reg' THEN 'registration'
                WHEN 'registration' THEN 'registration'
                WHEN 'hold' THEN 'registration'
                WHEN 'cpa_hold' THEN 'registration'
                WHEN 'ftd' THEN 'ftd'
                WHEN 'accept' THEN 'ftd'
                WHEN 'cpa_accept' THEN 'ftd'
                WHEN 'redep' THEN 'redeposit'
                WHEN 'redeposit' THEN 'redeposit'
                WHEN 'cpa_redep' THEN 'redeposit'
                ELSE lower(trim(event_type))
            END,
            provider_event_id = COALESCE(
                raw_json->>'provider_event_id', raw_json->>'event_id',
                raw_json->>'transaction_id', raw_json->>'transactionId',
                raw_json->>'txn_id', raw_json->>'conversion_id',
                raw_json->>'postback_id'
            )
        """
    )
    # Negative and unknown statuses have no domain meaning. Repeat deposits are
    # analytical only and require a stable provider transaction identifier.
    op.execute(
        """
        DELETE FROM adsetpro_postback_events
        WHERE event_type NOT IN ('registration', 'ftd', 'redeposit')
           OR (event_type = 'redeposit' AND provider_event_id IS NULL)
        """
    )
    op.alter_column(
        "adsetpro_postback_events",
        "occurred_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )
    op.alter_column(
        "adsetpro_postback_events",
        "source",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=sa.text("'adsetpro'"),
    )
    op.alter_column(
        "adsetpro_postback_events",
        "attribution_status",
        existing_type=sa.String(length=32),
        nullable=False,
        server_default=sa.text("'unmatched'"),
    )

    # Re-resolve direct sub8 after deliberately discarding legacy fb_ad_id values:
    # the old ingest incorrectly treated ext_sub6 (adset) as a Meta ad id.
    op.execute(
        """
        UPDATE adsetpro_postback_events e
        SET fb_ad_fk = a.id,
            fb_ad_id = a.fb_ad_id,
            attribution_status = 'matched_direct'
        FROM fb_ads a
        WHERE e.fb_ad_fk IS NULL
          AND e.fb_ad_id IS NOT NULL
          AND a.fb_ad_id = e.fb_ad_id
        """
    )
    # Old campaigns without sub8 use only the exact account+campaign+adset+ad
    # tuple. Ambiguous tuples remain unmatched and enter the durable retry queue.
    op.execute(
        """
        WITH matches AS (
            SELECT e.id, e.received_at,
                   (ARRAY_AGG(a.id ORDER BY a.id))[1] AS ad_id,
                   (ARRAY_AGG(a.fb_ad_id ORDER BY a.id))[1] AS fb_ad_id,
                   COUNT(*) AS match_count
            FROM adsetpro_postback_events e
            JOIN fb_campaigns c
              ON regexp_replace(COALESCE(c.ad_account_id, ''), '^act_', '')
                 = regexp_replace(COALESCE(
                    e.raw_json->>'sub4', e.raw_json->>'ext_sub4',
                    e.raw_json->>'account', e.raw_json->>'account_id',
                    e.raw_json->>'ad_account_id'
                 ), '^act_', '')
             AND c.campaign_name = COALESCE(
                    e.raw_json->>'sub5', e.raw_json->>'ext_sub5',
                    e.raw_json->>'campaign', e.raw_json->>'campaign_name'
                 )
            JOIN fb_adsets s
              ON s.campaign_id = c.id
             AND s.adset_name = COALESCE(
                    e.raw_json->>'sub6', e.raw_json->>'ext_sub6',
                    e.raw_json->>'adset', e.raw_json->>'adset_name'
                 )
            JOIN fb_ads a
              ON a.adset_id = s.id
             AND a.ad_name = COALESCE(
                    e.raw_json->>'sub7', e.raw_json->>'ext_sub7',
                    e.raw_json->>'ad', e.raw_json->>'ad_name'
                 )
            WHERE e.fb_ad_fk IS NULL
            GROUP BY e.id, e.received_at
        )
        UPDATE adsetpro_postback_events e
        SET fb_ad_fk = m.ad_id,
            fb_ad_id = m.fb_ad_id,
            attribution_status = 'matched_legacy'
        FROM matches m
        WHERE m.match_count = 1
          AND e.id = m.id
          AND e.received_at = m.received_at
        """
    )
    op.execute(
        """
        ALTER TABLE adsetpro_postback_events
        ADD CONSTRAINT ck_adsetpro_postback_events_adsetpro_event_type
        CHECK (event_type IN ('registration', 'ftd', 'redeposit')) NOT VALID
        """
    )
    op.execute(
        "ALTER TABLE adsetpro_postback_events "
        "VALIDATE CONSTRAINT ck_adsetpro_postback_events_adsetpro_event_type"
    )
    # PostgreSQL cannot CREATE INDEX CONCURRENTLY on the partitioned parent.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_adsetpro_postback_source_provider "
        "ON adsetpro_postback_events (source, provider_event_id) "
        "WHERE provider_event_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_adsetpro_postback_source_click "
        "ON adsetpro_postback_events (source, click_id, event_type)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_adsetpro_postback_processing "
        "ON adsetpro_postback_events (attribution_status, next_retry_at) "
        "WHERE processed_at IS NULL"
    )

    op.create_table(
        "tracker_click_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("click_id", sa.String(length=128), nullable=False),
        sa.Column("ad_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("fb_ad_id", sa.String(length=64), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column(
            "attribution_status",
            sa.String(length=32),
            server_default="unmatched",
            nullable=False,
        ),
        sa.Column("registration", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ftd", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "confirmed_deposit", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("registration_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ftd_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_deposit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ftd_revenue", sa.Numeric(12, 4), server_default="0", nullable=False),
        sa.Column("redeposits", sa.Integer(), server_default="0", nullable=False),
        sa.Column("redeposit_revenue", sa.Numeric(12, 4), server_default="0", nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ad_id"], ["fb_ads.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "click_id", name="uq_tracker_click_state_source_click"),
    )
    op.create_index("ix_tracker_click_state_ad", "tracker_click_state", ["ad_id", "last_event_at"])
    op.create_index("ix_tracker_click_state_last_event", "tracker_click_state", ["last_event_at"])
    op.create_index(
        "ix_tracker_click_state_unmatched",
        "tracker_click_state",
        ["last_event_at"],
        postgresql_where=sa.text("ad_id IS NULL"),
    )

    # Build the monotonic source+click projection from retained positive history.
    # registration/FTD are one-shot facts even if the legacy inbox contains
    # multiple deliveries; redeposits are distinct only by stable provider id.
    op.execute(
        """
        WITH one_shot AS (
            SELECT DISTINCT ON (source, click_id, event_type)
                   source, click_id, event_type, occurred_at, received_at,
                   fb_ad_fk, revenue, raw_json
            FROM adsetpro_postback_events
            WHERE is_duplicate = FALSE
              AND event_type IN ('registration', 'ftd')
              AND click_id <> ''
            ORDER BY source, click_id, event_type, occurred_at, received_at, id
        ),
        repeat_deposits AS (
            SELECT DISTINCT ON (source, provider_event_id)
                   source, click_id, event_type, occurred_at, received_at,
                   fb_ad_fk, revenue, raw_json
            FROM adsetpro_postback_events
            WHERE is_duplicate = FALSE
              AND event_type = 'redeposit'
              AND provider_event_id IS NOT NULL
              AND click_id <> ''
            ORDER BY source, provider_event_id, occurred_at, received_at, id
        ),
        facts AS (
            SELECT * FROM one_shot
            UNION ALL
            SELECT * FROM repeat_deposits
        ),
        grouped AS (
            SELECT
                source,
                click_id,
                CASE WHEN COUNT(DISTINCT fb_ad_fk) = 1 THEN
                    (ARRAY_AGG(fb_ad_fk ORDER BY received_at DESC)
                        FILTER (WHERE fb_ad_fk IS NOT NULL))[1]
                END AS ad_id,
                CASE
                    WHEN COUNT(DISTINCT fb_ad_fk) = 1 THEN 'matched_legacy'
                    WHEN COUNT(DISTINCT fb_ad_fk) > 1 THEN 'ambiguous'
                    ELSE 'unmatched'
                END AS attribution_status,
                (ARRAY_AGG(
                    UPPER(COALESCE(
                        raw_json->>'country', raw_json->>'country_code', raw_json->>'geo'
                    )) ORDER BY received_at DESC
                ) FILTER (
                    WHERE char_length(UPPER(COALESCE(
                        raw_json->>'country', raw_json->>'country_code', raw_json->>'geo'
                    ))) = 2
                ))[1] AS country,
                BOOL_OR(event_type = 'registration') AS registration,
                BOOL_OR(event_type = 'ftd') AS ftd,
                MIN(occurred_at) FILTER (WHERE event_type = 'registration') AS registration_at,
                MIN(occurred_at) FILTER (WHERE event_type = 'ftd') AS ftd_at,
                COALESCE(SUM(revenue) FILTER (WHERE event_type = 'ftd'), 0) AS ftd_revenue,
                COUNT(*) FILTER (WHERE event_type = 'redeposit')::int AS redeposits,
                COALESCE(SUM(revenue) FILTER (WHERE event_type = 'redeposit'), 0)
                    AS redeposit_revenue,
                MAX(received_at) AS last_event_at
            FROM facts
            GROUP BY source, click_id
        ),
        state_rows AS (
            SELECT g.*,
                   (registration AND ftd) AS confirmed_deposit,
                   CASE WHEN registration AND ftd
                        THEN GREATEST(registration_at, ftd_at)
                   END AS confirmed_deposit_at
            FROM grouped g
        )
        INSERT INTO tracker_click_state
            (id, source, click_id, ad_id, fb_ad_id, country, attribution_status,
             registration, ftd, confirmed_deposit, registration_at, ftd_at,
             confirmed_deposit_at, ftd_revenue, redeposits, redeposit_revenue,
             last_event_at, version, created_at, updated_at)
        SELECT gen_random_uuid(), s.source, s.click_id, s.ad_id, a.fb_ad_id,
               s.country, s.attribution_status, s.registration, s.ftd,
               s.confirmed_deposit, s.registration_at, s.ftd_at,
               s.confirmed_deposit_at, s.ftd_revenue, s.redeposits,
               s.redeposit_revenue, s.last_event_at, 1, now(), now()
        FROM state_rows s
        LEFT JOIN fb_ads a ON a.id = s.ad_id
        """
    )

    # Historical unmatched positives must participate in the same durable
    # re-attribution loop as new postbacks. Reset processed_at because old
    # aggregation did not mean attribution had succeeded.
    op.execute(
        """
        UPDATE adsetpro_postback_events
        SET processed_at = NULL,
            next_retry_at = now()
        WHERE fb_ad_fk IS NULL AND is_duplicate = FALSE
        """
    )
    op.execute(
        """
        INSERT INTO task_queue
            (task_type, status, idempotency_key, payload, requested_by,
             attempt_count, max_attempts, next_retry_at, created_at, updated_at)
        SELECT 'tracker_event_process', 'pending',
               LEFT('tracker:legacy:' || e.source || ':' || e.id || ':' ||
                    EXTRACT(EPOCH FROM e.received_at)::text, 128),
               jsonb_build_object(
                   'event_id', e.id,
                   'received_at', to_char(e.received_at AT TIME ZONE 'UTC',
                                          'YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00',
                   'source', e.source,
                   'click_id', e.click_id
               ),
               'adsetpro_migration', 0, 10080, now(), now(), now()
        FROM adsetpro_postback_events e
        WHERE e.fb_ad_fk IS NULL AND e.is_duplicate = FALSE
        ON CONFLICT (idempotency_key) DO NOTHING
        """
    )

    op.add_column(
        "tracker_aggregate", sa.Column("ftds", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "tracker_aggregate",
        sa.Column("confirmed_deposits", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "tracker_aggregate",
        sa.Column("redeposits", sa.Integer(), nullable=False, server_default="0"),
    )

    # Legacy tracker_aggregate treated raw FTD/baddep/redep as deposits. Rebuild
    # it exclusively from the new projection so only registration+FTD confirms a
    # deposit and unsupported historical statuses cannot survive the migration.
    op.execute("DELETE FROM tracker_aggregate")
    op.execute(
        """
        WITH state_facts AS (
            SELECT ad_id, COALESCE(country, 'XX') AS country,
                   registration_at AS occurred_at, 1 AS registrations, 0 AS ftds,
                   0 AS confirmed_deposits, 0 AS redeposits, 0::numeric AS revenue,
                   last_event_at
            FROM tracker_click_state
            WHERE ad_id IS NOT NULL AND registration_at IS NOT NULL
            UNION ALL
            SELECT ad_id, COALESCE(country, 'XX'), ftd_at, 0, 1, 0, 0,
                   CASE WHEN confirmed_deposit THEN ftd_revenue ELSE 0 END,
                   last_event_at
            FROM tracker_click_state
            WHERE ad_id IS NOT NULL AND ftd_at IS NOT NULL
            UNION ALL
            SELECT ad_id, COALESCE(country, 'XX'), confirmed_deposit_at,
                   0, 0, 1, 0, 0::numeric, last_event_at
            FROM tracker_click_state
            WHERE ad_id IS NOT NULL AND confirmed_deposit_at IS NOT NULL
        ),
        repeat_facts AS (
            SELECT DISTINCT ON (e.source, e.provider_event_id)
                   e.fb_ad_fk AS ad_id,
                   CASE
                       WHEN char_length(UPPER(COALESCE(
                           e.raw_json->>'country', e.raw_json->>'country_code',
                           e.raw_json->>'geo'
                       ))) = 2
                       THEN UPPER(COALESCE(
                           e.raw_json->>'country', e.raw_json->>'country_code',
                           e.raw_json->>'geo'
                       ))
                       ELSE 'XX'
                   END AS country,
                   e.occurred_at, 0 AS registrations, 0 AS ftds,
                   0 AS confirmed_deposits, 1 AS redeposits,
                   COALESCE(e.revenue, 0) AS revenue,
                   e.received_at AS last_event_at
            FROM adsetpro_postback_events e
            WHERE e.event_type = 'redeposit'
              AND e.provider_event_id IS NOT NULL
              AND e.fb_ad_fk IS NOT NULL
              AND e.is_duplicate = FALSE
            ORDER BY e.source, e.provider_event_id, e.occurred_at, e.received_at, e.id
        ),
        facts AS (
            SELECT * FROM state_facts
            UNION ALL
            SELECT * FROM repeat_facts
        )
        INSERT INTO tracker_aggregate
            (id, ad_id, country, day, installs, registrations, ftds, deposits,
             confirmed_deposits, redeposits, revenue, last_postback_at,
             created_at, updated_at)
        SELECT gen_random_uuid(), ad_id, country,
               (occurred_at AT TIME ZONE 'UTC')::date, 0,
               SUM(registrations)::int, SUM(ftds)::int,
               SUM(confirmed_deposits)::int, SUM(confirmed_deposits)::int,
               SUM(redeposits)::int, COALESCE(SUM(revenue), 0),
               MAX(last_event_at), now(), now()
        FROM facts
        WHERE occurred_at IS NOT NULL
        GROUP BY ad_id, country, (occurred_at AT TIME ZONE 'UTC')::date
        """
    )


def downgrade() -> None:
    op.drop_column("tracker_aggregate", "redeposits")
    op.drop_column("tracker_aggregate", "confirmed_deposits")
    op.drop_column("tracker_aggregate", "ftds")
    op.drop_table("tracker_click_state")
    op.execute("DROP INDEX IF EXISTS ix_adsetpro_postback_processing")
    op.execute("DROP INDEX IF EXISTS ix_adsetpro_postback_source_click")
    op.execute("DROP INDEX IF EXISTS ix_adsetpro_postback_source_provider")
    # This constraint was created with raw SQL above.  Passing its already
    # rendered name through Alembic's naming convention prefixes it a second
    # time (``ck_<table>_ck_<table>_...``), so downgrade must use the exact
    # database identifier as well.
    op.execute(
        "ALTER TABLE adsetpro_postback_events "
        "DROP CONSTRAINT IF EXISTS ck_adsetpro_postback_events_adsetpro_event_type"
    )
    for column in (
        "next_retry_at",
        "last_error",
        "attempt_count",
        "attribution_status",
        "provider_event_id",
        "source",
        "occurred_at",
    ):
        op.drop_column("adsetpro_postback_events", column)
    # The upgrade also creates this rendered name with raw SQL, so keep the
    # downgrade outside Alembic's CHECK naming convention for the same reason.
    op.execute("ALTER TABLE task_queue DROP CONSTRAINT IF EXISTS ck_task_queue_task_type")
    op.execute(
        "ALTER TABLE task_queue ADD CONSTRAINT ck_task_queue_task_type CHECK ("
        "task_type IN ('disable', 'enable', 'plan_run', 'meta_api_mutation', "
        "'ad_library_scan', 'campaign_create'))"
    )
    op.drop_column("task_queue", "external_started_at")
