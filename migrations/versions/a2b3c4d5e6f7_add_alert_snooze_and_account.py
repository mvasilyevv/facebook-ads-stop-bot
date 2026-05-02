# -*- coding: utf-8 -*-
"""Добавляет таблицу alert_snoozes и колонку fb_account_id в observer_settings.

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-05-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a2b3c4d5e6f7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Таблица снузов — создаётся один раз
    op.create_table(
        "alert_snoozes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("fb_ad_id", sa.String(length=32), nullable=False),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_telegram_user_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alert_snoozes_fb_ad_id", "alert_snoozes", ["fb_ad_id"])
    op.create_index("ix_alert_snoozes_snoozed_until", "alert_snoozes", ["snoozed_until"])

    # Идентификатор рекламного кабинета Facebook для ссылки на Ads Manager
    op.add_column(
        "observer_settings",
        sa.Column("fb_account_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "fb_account_id")
    op.drop_index("ix_alert_snoozes_snoozed_until", table_name="alert_snoozes")
    op.drop_index("ix_alert_snoozes_fb_ad_id", table_name="alert_snoozes")
    op.drop_table("alert_snoozes")
