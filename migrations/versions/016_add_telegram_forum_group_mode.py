# -*- coding: utf-8 -*-
"""Добавить forum-group режим Telegram и topic ids.

Revision ID: 016
Revises: 015
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    """Проверяет наличие таблицы."""
    return sa.inspect(bind).has_table(table_name)


def _column_names(bind, table_name: str) -> set[str]:
    """Возвращает список колонок таблицы."""
    if not _has_table(bind, table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _index_names(bind, table_name: str) -> set[str]:
    """Возвращает список индексов таблицы."""
    if not _has_table(bind, table_name):
        return set()
    return {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}


def _unique_constraints(bind, table_name: str) -> list[dict]:
    """Возвращает unique-ограничения таблицы."""
    if not _has_table(bind, table_name):
        return []
    return list(sa.inspect(bind).get_unique_constraints(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    delivery_mode_enum = postgresql.ENUM(
        "PRIVATE_CHAT",
        "FORUM_GROUP",
        name="telegram_delivery_mode_enum",
        create_type=False,
    )
    delivery_mode_enum.create(bind, checkfirst=True)

    telegram_settings_columns = _column_names(bind, "telegram_settings")
    if "delivery_mode" not in telegram_settings_columns:
        op.add_column(
            "telegram_settings",
            sa.Column(
                "delivery_mode",
                delivery_mode_enum,
                nullable=False,
                server_default="PRIVATE_CHAT",
            ),
        )
    for column_name in (
        "control_topic_id",
        "early_topic_id",
        "warning_topic_id",
        "stop_topic_id",
        "enable_topic_id",
    ):
        if column_name not in telegram_settings_columns:
            op.add_column("telegram_settings", sa.Column(column_name, sa.Integer(), nullable=True))

    for constraint in _unique_constraints(bind, "telegram_recipients"):
        if constraint.get("column_names") == ["chat_id"] and constraint.get("name"):
            op.drop_constraint(
                constraint["name"],
                "telegram_recipients",
                type_="unique",
            )

    telegram_recipient_indexes = _index_names(bind, "telegram_recipients")
    if "uq_telegram_recipients_chat_and_user" not in telegram_recipient_indexes:
        op.create_index(
            "uq_telegram_recipients_chat_and_user",
            "telegram_recipients",
            ["chat_id", "telegram_user_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    telegram_recipient_indexes = _index_names(bind, "telegram_recipients")
    if "uq_telegram_recipients_chat_and_user" in telegram_recipient_indexes:
        op.drop_index(
            "uq_telegram_recipients_chat_and_user",
            table_name="telegram_recipients",
        )
    unique_constraints = _unique_constraints(bind, "telegram_recipients")
    if not any(item.get("column_names") == ["chat_id"] for item in unique_constraints):
        op.create_unique_constraint(
            "uq_telegram_recipients_chat_id",
            "telegram_recipients",
            ["chat_id"],
        )

    telegram_settings_columns = _column_names(bind, "telegram_settings")
    for column_name in (
        "enable_topic_id",
        "stop_topic_id",
        "warning_topic_id",
        "early_topic_id",
        "control_topic_id",
        "delivery_mode",
    ):
        if column_name in telegram_settings_columns:
            op.drop_column("telegram_settings", column_name)

    delivery_mode_enum = postgresql.ENUM(
        "PRIVATE_CHAT",
        "FORUM_GROUP",
        name="telegram_delivery_mode_enum",
        create_type=False,
    )
    delivery_mode_enum.drop(bind, checkfirst=True)
