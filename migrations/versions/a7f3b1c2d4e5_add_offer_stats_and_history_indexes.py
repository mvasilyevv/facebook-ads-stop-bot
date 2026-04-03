# -*- coding: utf-8 -*-
"""add_offer_stats_and_history_indexes

Revision ID: a7f3b1c2d4e5
Revises: d225a24ac04b
Create Date: 2026-04-03 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7f3b1c2d4e5"
down_revision: str | None = "d225a24ac04b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Добавляет offer_stats_json и индексы для страницы истории."""
    op.add_column(
        "cabinet_day_archives",
        sa.Column(
            "offer_stats_json",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_index(
        "ix_cabinet_day_archive_range",
        "cabinet_day_archives",
        ["started_at", "ended_at"],
    )
    op.create_index(
        "ix_alert_event_ad_timeline",
        "alert_events",
        ["fb_ad_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_disable_task_ad_timeline",
        "disable_tasks",
        ["fb_ad_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_enable_task_ad_timeline",
        "enable_tasks",
        ["fb_ad_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Откатывает добавленные индексы и колонку."""
    op.drop_index("ix_enable_task_ad_timeline", table_name="enable_tasks")
    op.drop_index("ix_disable_task_ad_timeline", table_name="disable_tasks")
    op.drop_index("ix_alert_event_ad_timeline", table_name="alert_events")
    op.drop_index("ix_cabinet_day_archive_range", table_name="cabinet_day_archives")
    op.drop_column("cabinet_day_archives", "offer_stats_json")
