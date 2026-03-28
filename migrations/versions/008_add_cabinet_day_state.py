# -*- coding: utf-8 -*-
"""Добавить границу суток кабинета и архив завершившихся дней.

Revision ID: 008
Revises: 87165d779b8f
Create Date: 2026-03-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "008"
down_revision = "87165d779b8f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_settings",
        sa.Column("cabinet_day_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_observer_settings_cabinet_day_started_at"),
        "observer_settings",
        ["cabinet_day_started_at"],
        unique=False,
    )

    op.create_table(
        "cabinet_day_archives",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reset_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ads_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("campaigns_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_cabinet_day_archives_started_at"),
        "cabinet_day_archives",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cabinet_day_archives_ended_at"),
        "cabinet_day_archives",
        ["ended_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_cabinet_day_archives_reset_detected_at"),
        "cabinet_day_archives",
        ["reset_detected_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_cabinet_day_archives_reset_detected_at"), table_name="cabinet_day_archives")
    op.drop_index(op.f("ix_cabinet_day_archives_ended_at"), table_name="cabinet_day_archives")
    op.drop_index(op.f("ix_cabinet_day_archives_started_at"), table_name="cabinet_day_archives")
    op.drop_table("cabinet_day_archives")
    op.drop_index(
        op.f("ix_observer_settings_cabinet_day_started_at"),
        table_name="observer_settings",
    )
    op.drop_column("observer_settings", "cabinet_day_started_at")
