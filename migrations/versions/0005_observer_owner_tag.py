# -*- coding: utf-8 -*-
"""Добавить observer_config.owner_campaign_tag (owner-scoping).

Тег владельца в названии кампании (например, "MV"). Если задан — observer
обрабатывает ТОЛЬКО кампании с этим тегом (word-boundary), остальные полностью
игнорирует. Защита от работы бота с чужими кампаниями в общем рекламном кабинете.
NULL — фильтр выключен (обрабатываются все кампании, обратная совместимость).

Revision ID: 0005_observer_owner_tag
Revises: 0004_alert_events_scan_id_index
Create Date: 2026-05-29
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0005_observer_owner_tag"
down_revision: str | None = "0004_alert_events_scan_id_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable VARCHAR — NULL означает «фильтр выключен», существующие строки не ломаются.
    op.execute(
        """
        ALTER TABLE observer_config
        ADD COLUMN IF NOT EXISTS owner_campaign_tag VARCHAR(64);
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE observer_config DROP COLUMN IF EXISTS owner_campaign_tag;")
