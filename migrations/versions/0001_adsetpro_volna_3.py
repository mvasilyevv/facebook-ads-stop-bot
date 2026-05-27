# -*- coding: utf-8 -*-
"""Волна 3: AdSet.pro postback inbox + credentials singleton.

См. META_INTEGRATION_PLAN.md §5 Волна 3 + Этап 6.

Создаёт:
- adsetpro_postback_events  — partitioned by month (RANGE received_at), UNIQUE дедуп
  (click_id, event_type, received_at), индексы по received_at/fb_ad_fk/click_id.
- adsetpro_credentials       — Singleton, Fernet-encrypted MCP key + postback secret.
- Партиции на текущий и следующий месяц (для нулевого даунтайма после миграции).

Дополнительно регистрирует таблицу в system_config.retention_policy = '60 days'.

Revision ID: 0001_adsetpro_volna_3
Revises:
Create Date: 2026-05-27
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_adsetpro_volna_3"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    """Возвращает (from, to) для PARTITION OF — начало текущего и следующего месяца."""
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    return f"{year:04d}-{month:02d}-01", f"{next_year:04d}-{next_month:02d}-01"


def upgrade() -> None:
    # ============================================================================
    # 1. adsetpro_postback_events — partitioned by RANGE (received_at)
    # ============================================================================
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS adsetpro_postback_events (
            id                 BIGSERIAL    NOT NULL,
            received_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
            click_id           VARCHAR(128) NOT NULL,
            fb_ad_id           VARCHAR(64),
            fb_ad_fk           UUID         REFERENCES fb_ads(id) ON DELETE SET NULL,
            event_type         VARCHAR(32)  NOT NULL,
            revenue            NUMERIC(12, 4),
            currency           VARCHAR(8)   NOT NULL DEFAULT 'USD',
            raw_json           JSONB        NOT NULL,
            signature_valid    BOOLEAN,
            is_duplicate       BOOLEAN      NOT NULL DEFAULT FALSE,
            processed_at       TIMESTAMPTZ,
            PRIMARY KEY (id, received_at),
            CONSTRAINT uq_adsetpro_postback_dedup
                UNIQUE (click_id, event_type, received_at)
        ) PARTITION BY RANGE (received_at);
        """
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_adsetpro_postback_received "
        "ON adsetpro_postback_events (received_at);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_adsetpro_postback_fb_ad "
        "ON adsetpro_postback_events (fb_ad_fk, received_at) "
        "WHERE fb_ad_fk IS NOT NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_adsetpro_postback_click "
        "ON adsetpro_postback_events (click_id, event_type);"
    )

    # Партиции на текущий + следующий месяц (без даунтайма после применения).
    now = datetime.now(timezone.utc)
    current_from, current_to = _month_bounds(now.year, now.month)
    if now.month == 12:
        next_from, next_to = _month_bounds(now.year + 1, 1)
    else:
        next_from, next_to = _month_bounds(now.year, now.month + 1)

    for year_month, fr, to in [
        (f"{now.year:04d}_{now.month:02d}", current_from, current_to),
        (next_from.replace("-", "_")[:7], next_from, next_to),
    ]:
        op.execute(
            f"CREATE TABLE IF NOT EXISTS adsetpro_postback_events_{year_month} "
            f"PARTITION OF adsetpro_postback_events "
            f"FOR VALUES FROM ('{fr}') TO ('{to}');"
        )

    # ============================================================================
    # 2. adsetpro_credentials — Singleton (Fernet-encrypted)
    # ============================================================================
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS adsetpro_credentials (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            singleton_key               VARCHAR(16) NOT NULL UNIQUE DEFAULT 'default',
            api_key_encrypted           BYTEA NOT NULL,
            postback_secret_encrypted   BYTEA,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    # ============================================================================
    # 3. Зарегистрировать retention для cleanup_worker
    # ============================================================================
    policy_patch = {"adsetpro_postback_events": "60 days"}
    op.execute(
        sa.text(
            """
            INSERT INTO system_config (key, value, description)
            VALUES ('retention_policy', CAST(:patch AS JSONB),
                    'Retention per table — см. DB_REDESIGN.md §4')
            ON CONFLICT (key) DO UPDATE
                SET value = system_config.value || EXCLUDED.value,
                    updated_at = NOW()
            """
        ).bindparams(patch=json.dumps(policy_patch))
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS adsetpro_credentials;")
    # CASCADE — снимет привязанные партиции adsetpro_postback_events_YYYY_MM.
    op.execute("DROP TABLE IF EXISTS adsetpro_postback_events CASCADE;")
    op.execute(
        sa.text(
            """
            UPDATE system_config
            SET value = value - 'adsetpro_postback_events',
                updated_at = NOW()
            WHERE key = 'retention_policy'
            """
        )
    )
