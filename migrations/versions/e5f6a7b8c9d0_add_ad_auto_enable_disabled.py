# -*- coding: utf-8 -*-
"""Добавляет таблицу ad_auto_enable_disabled для per-ad флага автовключения.

Revision ID: e5f6a7b8c9d0
Revises: c3f1a2b4d5e6
Create Date: 2026-04-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "c3f1a2b4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ad_auto_enable_disabled",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fb_ad_id", sa.String(32), nullable=False),
        sa.Column("cabinet_day_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fb_ad_id"),
    )
    op.create_index("ix_ad_auto_enable_disabled_fb_ad_id", "ad_auto_enable_disabled", ["fb_ad_id"])


def downgrade() -> None:
    op.drop_index("ix_ad_auto_enable_disabled_fb_ad_id", table_name="ad_auto_enable_disabled")
    op.drop_table("ad_auto_enable_disabled")
