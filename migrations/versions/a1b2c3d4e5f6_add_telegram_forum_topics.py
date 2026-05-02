# -*- coding: utf-8 -*-
"""Добавляет поля forum topics в telegram_settings и значение OPS в enum потоков.

Revision ID: a1b2c3d4e5f6
Revises: b8c9d0e1f2a3
Create Date: 2026-05-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "0f1a2b3c4d5e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Добавляем новое значение OPS в pg-enum потоков уведомлений
    op.execute("ALTER TYPE telegram_notification_stream_enum ADD VALUE IF NOT EXISTS 'OPS'")

    op.add_column(
        "telegram_settings",
        sa.Column("topic_alerts_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_disabled_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_recommendations_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_ops_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_logs_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column(
            "forum_topics_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("web_app_url", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_settings", "web_app_url")
    op.drop_column("telegram_settings", "forum_topics_enabled")
    op.drop_column("telegram_settings", "topic_logs_thread_id")
    op.drop_column("telegram_settings", "topic_ops_thread_id")
    op.drop_column("telegram_settings", "topic_recommendations_thread_id")
    op.drop_column("telegram_settings", "topic_disabled_thread_id")
    op.drop_column("telegram_settings", "topic_alerts_thread_id")
    # Значение enum удалить нельзя без пересоздания типа; при откате оставляем как есть
