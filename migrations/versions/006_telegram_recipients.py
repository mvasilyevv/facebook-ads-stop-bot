# -*- coding: utf-8 -*-
"""Добавить таблицу telegram_recipients.

Revision ID: 006
Revises: 005
Create Date: 2026-03-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telegram_recipients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(128), nullable=False, server_default=""),
        sa.Column("first_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index("ix_telegram_recipients_chat_id", "telegram_recipients", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_telegram_recipients_chat_id", "telegram_recipients")
    op.drop_table("telegram_recipients")
