# -*- coding: utf-8 -*-
"""Добавить таблицу vision_settings и pending_codes в telegram_settings.

Revision ID: 005
Revises: 004
Create Date: 2026-03-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004_telegram_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Таблица настроек Vision браузера
    op.create_table(
        "vision_settings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("singleton_key", sa.String(32), nullable=False),
        sa.Column("api_url", sa.String(255), nullable=False, server_default="http://127.0.0.1:3030"),
        sa.Column("x_token_encrypted", sa.Text(), nullable=False, server_default=""),
        sa.Column("profile_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("reconnect_requested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("singleton_key"),
    )
    # Добавить pending_codes в telegram_settings
    op.add_column(
        "telegram_settings",
        sa.Column("pending_codes", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("telegram_settings", "pending_codes")
    op.drop_table("vision_settings")
