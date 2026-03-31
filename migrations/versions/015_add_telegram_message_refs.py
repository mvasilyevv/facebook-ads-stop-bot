# -*- coding: utf-8 -*-
"""Добавить refs сообщений Telegram по потокам уведомлений.

Revision ID: 015
Revises: 014
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    """Проверяет наличие таблицы."""
    return sa.inspect(bind).has_table(table_name)


def _get_index_names(bind, table_name: str) -> set[str]:
    """Возвращает индексы таблицы."""
    if not _has_table(bind, table_name):
        return set()
    return {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    stream_enum = postgresql.ENUM(
        "EARLY",
        "WARNING",
        "STOP",
        "ENABLE",
        name="telegram_notification_stream_enum",
        create_type=False,
    )
    stream_enum.create(bind, checkfirst=True)

    if not _has_table(bind, "telegram_message_refs"):
        op.create_table(
            "telegram_message_refs",
            sa.Column("telegram_chat_id", sa.String(length=64), nullable=False),
            sa.Column("telegram_message_id", sa.Integer(), nullable=False),
            sa.Column("fb_ad_id", sa.String(length=32), nullable=False),
            sa.Column("incident_key", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("stream_kind", stream_enum, nullable=False),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = _get_index_names(bind, "telegram_message_refs")
    indexes = (
        (op.f("ix_telegram_message_refs_fb_ad_id"), ["fb_ad_id"], False),
        (op.f("ix_telegram_message_refs_incident_key"), ["incident_key"], False),
        (op.f("ix_telegram_message_refs_stream_kind"), ["stream_kind"], False),
        (op.f("ix_telegram_message_refs_telegram_chat_id"), ["telegram_chat_id"], False),
        (
            op.f("ix_telegram_message_refs_telegram_message_id"),
            ["telegram_message_id"],
            False,
        ),
        (
            "uq_telegram_message_refs_stream",
            ["telegram_chat_id", "fb_ad_id", "incident_key", "stream_kind"],
            True,
        ),
    )
    for index_name, columns, is_unique in indexes:
        if index_name not in existing_indexes:
            op.create_index(index_name, "telegram_message_refs", columns, unique=is_unique)


def downgrade() -> None:
    op.drop_index("uq_telegram_message_refs_stream", table_name="telegram_message_refs")
    op.drop_index(
        op.f("ix_telegram_message_refs_telegram_message_id"),
        table_name="telegram_message_refs",
    )
    op.drop_index(
        op.f("ix_telegram_message_refs_telegram_chat_id"),
        table_name="telegram_message_refs",
    )
    op.drop_index(op.f("ix_telegram_message_refs_stream_kind"), table_name="telegram_message_refs")
    op.drop_index(op.f("ix_telegram_message_refs_incident_key"), table_name="telegram_message_refs")
    op.drop_index(op.f("ix_telegram_message_refs_fb_ad_id"), table_name="telegram_message_refs")
    op.drop_table("telegram_message_refs")

    stream_enum = postgresql.ENUM(
        "EARLY",
        "WARNING",
        "STOP",
        "ENABLE",
        name="telegram_notification_stream_enum",
        create_type=False,
    )
    stream_enum.drop(op.get_bind(), checkfirst=True)
