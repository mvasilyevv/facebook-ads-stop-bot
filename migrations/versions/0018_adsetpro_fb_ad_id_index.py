# -*- coding: utf-8 -*-
"""Индекс под deposits hot-path: (fb_ad_id, received_at) на adsetpro_postback_events.

load_external_deposits_batch (core/adset_pro/queries.py) вызывается на КАЖДОМ скане
из pipeline и фильтрует по сырому fb_ad_id (VARCHAR) + received_at. Индекс был только
на fb_ad_fk (UUID) → seq-scan партиции в money hot-path, деградирует с ростом
постбэков. Partial (fb_ad_id IS NOT NULL): запрос `fb_ad_id = ANY(...)` не матчит NULL.

Партиционированная таблица (RANGE received_at) → CREATE INDEX на родителе
пропагируется на все партиции (и будущие). CONCURRENTLY на партиционном родителе
недопустим, поэтому обычный CREATE INDEX (краткая блокировка приемлема для миграции).

Revision ID: 0018_adsetpro_fb_ad_id_index
Revises: 0017_telegram_topics
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0018_adsetpro_fb_ad_id_index"
down_revision: str | None = "0017_telegram_topics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_adsetpro_postback_fb_ad_id "
        "ON adsetpro_postback_events (fb_ad_id, received_at) "
        "WHERE fb_ad_id IS NOT NULL;"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_adsetpro_postback_fb_ad_id;")
