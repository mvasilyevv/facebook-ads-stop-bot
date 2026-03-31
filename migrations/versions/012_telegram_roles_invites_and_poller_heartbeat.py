# -*- coding: utf-8 -*-
"""Добавить роли Telegram, инвайты и heartbeat poller-а.

Revision ID: 012
Revises: 011
Create Date: 2026-03-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_settings",
        sa.Column(
            "owner_telegram_user_id", sa.String(length=64), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("owner_username", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("owner_first_name", sa.String(length=128), nullable=False, server_default=""),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("poller_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_column("telegram_settings", "pending_codes")

    op.add_column(
        "telegram_recipients",
        sa.Column("telegram_user_id", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column(
        "telegram_recipients",
        sa.Column("role", sa.String(length=32), nullable=False, server_default="recipient"),
    )
    op.create_index(
        "ix_telegram_recipients_telegram_user_id",
        "telegram_recipients",
        ["telegram_user_id"],
    )

    op.create_table(
        "telegram_invites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="recipient"),
        sa.Column(
            "created_by_telegram_user_id", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column("created_by_username", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_telegram_invites_code", "telegram_invites", ["code"])
    op.create_index("ix_telegram_invites_expires_at", "telegram_invites", ["expires_at"])
    op.create_index("ix_telegram_invites_used_at", "telegram_invites", ["used_at"])
    op.create_index("ix_telegram_invites_revoked_at", "telegram_invites", ["revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_telegram_invites_revoked_at", table_name="telegram_invites")
    op.drop_index("ix_telegram_invites_used_at", table_name="telegram_invites")
    op.drop_index("ix_telegram_invites_expires_at", table_name="telegram_invites")
    op.drop_index("ix_telegram_invites_code", table_name="telegram_invites")
    op.drop_table("telegram_invites")

    op.drop_index(
        "ix_telegram_recipients_telegram_user_id",
        table_name="telegram_recipients",
    )
    op.drop_column("telegram_recipients", "role")
    op.drop_column("telegram_recipients", "telegram_user_id")

    op.add_column(
        "telegram_settings",
        sa.Column("pending_codes", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.drop_column("telegram_settings", "poller_heartbeat_at")
    op.drop_column("telegram_settings", "owner_first_name")
    op.drop_column("telegram_settings", "owner_username")
    op.drop_column("telegram_settings", "owner_telegram_user_id")
