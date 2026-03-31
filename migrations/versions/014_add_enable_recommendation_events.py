# -*- coding: utf-8 -*-
"""Добавить recommendation events для включения объявлений.

Revision ID: 014
Revises: 013
Create Date: 2026-03-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    """Проверяет наличие таблицы."""
    return sa.inspect(bind).has_table(table_name)


def _get_column_names(bind, table_name: str) -> set[str]:
    """Возвращает набор имён колонок таблицы."""
    if not _has_table(bind, table_name):
        return set()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _get_index_names(bind, table_name: str) -> set[str]:
    """Возвращает набор индексов таблицы."""
    if not _has_table(bind, table_name):
        return set()
    return {index["name"] for index in sa.inspect(bind).get_indexes(table_name)}


def _get_foreign_key_names(bind, table_name: str) -> set[str]:
    """Возвращает набор ограничений foreign key таблицы."""
    if not _has_table(bind, table_name):
        return set()
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(bind).get_foreign_keys(table_name)
        if foreign_key.get("name")
    }


def upgrade() -> None:
    bind = op.get_bind()
    recommendation_level_enum = postgresql.ENUM(
        "OK",
        "EARLY_SIGNAL",
        "WARNING",
        name="enable_recommendation_level_enum",
        create_type=False,
    )
    recommendation_level_enum.create(bind, checkfirst=True)

    if not _has_table(bind, "enable_recommendation_events"):
        op.create_table(
            "enable_recommendation_events",
            sa.Column("snapshot_id", sa.Uuid(), nullable=True),
            sa.Column("offer_id", sa.Uuid(), nullable=True),
            sa.Column("fb_ad_id", sa.String(length=32), nullable=False),
            sa.Column("ad_name", sa.String(length=255), nullable=False),
            sa.Column("delivery_status", sa.String(length=64), nullable=False),
            sa.Column("recommendation_level", recommendation_level_enum, nullable=False),
            sa.Column("matched_rule_codes", sa.JSON(), nullable=False),
            sa.Column("reason_title", sa.String(length=255), nullable=True),
            sa.Column("reason_text", sa.Text(), nullable=True),
            sa.Column("metrics_json", sa.JSON(), nullable=False),
            sa.Column("live_batch_started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("idempotency_key", sa.String(length=160), nullable=False),
            sa.Column("telegram_chat_id", sa.String(length=64), nullable=True),
            sa.Column("telegram_message_id", sa.Integer(), nullable=True),
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["snapshot_id"], ["ad_snapshots.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_event_indexes = _get_index_names(bind, "enable_recommendation_events")
    event_indexes = (
        (op.f("ix_enable_recommendation_events_fb_ad_id"), ["fb_ad_id"], False),
        (
            op.f("ix_enable_recommendation_events_live_batch_started_at"),
            ["live_batch_started_at"],
            False,
        ),
        (op.f("ix_enable_recommendation_events_offer_id"), ["offer_id"], False),
        (
            op.f("ix_enable_recommendation_events_recommendation_level"),
            ["recommendation_level"],
            False,
        ),
        (op.f("ix_enable_recommendation_events_snapshot_id"), ["snapshot_id"], False),
        (
            op.f("ix_enable_recommendation_events_telegram_message_id"),
            ["telegram_message_id"],
            False,
        ),
        ("uq_enable_recommendation_event_idempotency", ["idempotency_key"], True),
    )
    for index_name, columns, is_unique in event_indexes:
        if index_name not in existing_event_indexes:
            op.create_index(index_name, "enable_recommendation_events", columns, unique=is_unique)

    if "recommendation_event_id" not in _get_column_names(bind, "enable_tasks"):
        op.add_column(
            "enable_tasks",
            sa.Column("recommendation_event_id", sa.Uuid(), nullable=True),
        )

    enable_task_foreign_keys = _get_foreign_key_names(bind, "enable_tasks")
    if "fk_enable_tasks_recommendation_event_id" not in enable_task_foreign_keys:
        op.create_foreign_key(
            "fk_enable_tasks_recommendation_event_id",
            "enable_tasks",
            "enable_recommendation_events",
            ["recommendation_event_id"],
            ["id"],
            ondelete="SET NULL",
        )

    enable_task_indexes = _get_index_names(bind, "enable_tasks")
    enable_task_index_name = op.f("ix_enable_tasks_recommendation_event_id")
    if enable_task_index_name not in enable_task_indexes:
        op.create_index(
            enable_task_index_name,
            "enable_tasks",
            ["recommendation_event_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_enable_tasks_recommendation_event_id"), table_name="enable_tasks")
    op.drop_constraint(
        "fk_enable_tasks_recommendation_event_id",
        "enable_tasks",
        type_="foreignkey",
    )
    op.drop_column("enable_tasks", "recommendation_event_id")

    op.drop_index(
        "uq_enable_recommendation_event_idempotency",
        table_name="enable_recommendation_events",
    )
    op.drop_index(
        op.f("ix_enable_recommendation_events_telegram_message_id"),
        table_name="enable_recommendation_events",
    )
    op.drop_index(
        op.f("ix_enable_recommendation_events_snapshot_id"),
        table_name="enable_recommendation_events",
    )
    op.drop_index(
        op.f("ix_enable_recommendation_events_recommendation_level"),
        table_name="enable_recommendation_events",
    )
    op.drop_index(
        op.f("ix_enable_recommendation_events_offer_id"),
        table_name="enable_recommendation_events",
    )
    op.drop_index(
        op.f("ix_enable_recommendation_events_live_batch_started_at"),
        table_name="enable_recommendation_events",
    )
    op.drop_index(
        op.f("ix_enable_recommendation_events_fb_ad_id"),
        table_name="enable_recommendation_events",
    )
    op.drop_table("enable_recommendation_events")

    recommendation_level_enum = postgresql.ENUM(
        "OK",
        "EARLY_SIGNAL",
        "WARNING",
        name="enable_recommendation_level_enum",
        create_type=False,
    )
    recommendation_level_enum.drop(op.get_bind(), checkfirst=True)
