# -*- coding: utf-8 -*-
"""Удаляет forum-topic поля из telegram_settings (деградирует до private-chat).

Revision ID: d0e1f2a3b4c5
Revises: c4d5e6f7a8b9
Create Date: 2026-05-03 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Удаляем все forum-topic колонки и колонку delivery_mode
    op.drop_column("telegram_settings", "control_topic_id")
    op.drop_column("telegram_settings", "warning_topic_id")
    op.drop_column("telegram_settings", "stop_topic_id")
    op.drop_column("telegram_settings", "enable_topic_id")
    op.drop_column("telegram_settings", "topic_alerts_thread_id")
    op.drop_column("telegram_settings", "topic_disabled_thread_id")
    op.drop_column("telegram_settings", "topic_recommendations_thread_id")
    op.drop_column("telegram_settings", "topic_ops_thread_id")
    op.drop_column("telegram_settings", "topic_logs_thread_id")
    op.drop_column("telegram_settings", "forum_topics_enabled")
    op.drop_column("telegram_settings", "delivery_mode")
    # Удаляем pg-enum тип, больше не нужен
    op.execute("DROP TYPE IF EXISTS telegram_delivery_mode_enum")


def downgrade() -> None:
    # Восстанавливаем enum и колонки (только для отката)
    op.execute("CREATE TYPE telegram_delivery_mode_enum AS ENUM ('PRIVATE_CHAT', 'FORUM_GROUP')")
    op.add_column(
        "telegram_settings",
        sa.Column(
            "delivery_mode",
            sa.Enum("PRIVATE_CHAT", "FORUM_GROUP", name="telegram_delivery_mode_enum"),
            nullable=False,
            server_default="PRIVATE_CHAT",
        ),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("forum_topics_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_logs_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_ops_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_recommendations_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_disabled_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("topic_alerts_thread_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("enable_topic_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("stop_topic_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("warning_topic_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("control_topic_id", sa.Integer(), nullable=True),
    )
