# -*- coding: utf-8 -*-
"""Авторизация Telegram: зашифрованный токен, auth_code, is_authorized.

Revision ID: 004_telegram_auth
Revises: 003_fix_snapshot_index
Create Date: 2026-03-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "004_telegram_auth"
down_revision: str | None = "003_fix_snapshot_index"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Добавляем новые колонки
    op.add_column(
        "telegram_settings",
        sa.Column("bot_token_encrypted", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("is_authorized", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("auth_code", sa.String(16), server_default="", nullable=False),
    )
    op.add_column(
        "telegram_settings",
        sa.Column("bot_username", sa.String(128), server_default="", nullable=False),
    )

    # Переносим bot_token → bot_token_encrypted (пока как plaintext,
    # шифрование применится при следующем сохранении через API)
    op.execute("""
        UPDATE telegram_settings
        SET bot_token_encrypted = bot_token
        WHERE bot_token IS NOT NULL AND bot_token != ''
    """)

    # Удаляем старую колонку
    op.drop_column("telegram_settings", "bot_token")


def downgrade() -> None:
    op.add_column(
        "telegram_settings",
        sa.Column("bot_token", sa.String(255), server_default="", nullable=False),
    )
    op.execute("""
        UPDATE telegram_settings
        SET bot_token = bot_token_encrypted
    """)
    op.drop_column("telegram_settings", "bot_username")
    op.drop_column("telegram_settings", "auth_code")
    op.drop_column("telegram_settings", "is_authorized")
    op.drop_column("telegram_settings", "bot_token_encrypted")
