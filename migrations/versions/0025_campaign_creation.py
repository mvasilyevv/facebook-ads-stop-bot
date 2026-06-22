# -*- coding: utf-8 -*-
"""Сервис создания кампаний: таблицы campaign_preset + campaign_run.

campaign_preset — стабильный переиспользуемый конфиг залива (идентичность кабинета,
цель/оптимизация, атрибуция, шаблоны нейминга/трекинга) с SOP-дефолтами.

campaign_run — снимок CampaignConfig + прогресс исполнения воркером campaign_creator.
Money-критично: idempotency_key (UNIQUE) против двойного создания при retry/гонке.

Цепочка за 0024_drop_forum_thread_columns.

Revision ID: 0025_campaign_creation
Revises: 0024_drop_forum_thread_columns
Create Date: 2026-06-22
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_campaign_creation"
down_revision: str | None = "0024_drop_forum_thread_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Канон статусов (зеркало core.models.campaigns.run.CAMPAIGN_RUN_STATUSES).
_RUN_STATUSES = (
    "queued",
    "uniquifying",
    "uploading",
    "creating",
    "succeeded",
    "failed",
    "cancelled",
)
_STATUS_IN = ", ".join(f"'{s}'" for s in _RUN_STATUSES)


def upgrade() -> None:
    op.create_table(
        "campaign_preset",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("act_id", sa.String(length=64), nullable=False),
        sa.Column("page_id", sa.String(length=64), nullable=False),
        sa.Column("pixel_id", sa.String(length=64), nullable=False),
        sa.Column("tz_offset", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("offer_code", sa.String(length=64), nullable=True),
        sa.Column("byer_tag", sa.String(length=64), nullable=True),
        sa.Column(
            "objective",
            sa.String(length=64),
            server_default=sa.text("'OUTCOME_SALES'"),
            nullable=False,
        ),
        sa.Column(
            "optimization_goal",
            sa.String(length=64),
            server_default=sa.text("'OFFSITE_CONVERSIONS'"),
            nullable=False,
        ),
        sa.Column(
            "custom_event_type",
            sa.String(length=64),
            server_default=sa.text("'PURCHASE'"),
            nullable=False,
        ),
        sa.Column(
            "special_ad_categories",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("""'["NONE"]'::jsonb"""),
            nullable=False,
        ),
        sa.Column(
            "cta", sa.String(length=64), server_default=sa.text("'PLAY_GAME'"), nullable=False
        ),
        sa.Column(
            "text_optimizations",
            sa.String(length=32),
            server_default=sa.text("'OPT_OUT'"),
            nullable=False,
        ),
        sa.Column("click_through_days", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("view_through_days", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("url_tags_template", sa.String(length=1024), nullable=True),
        sa.Column("naming_template", sa.String(length=512), nullable=True),
        sa.Column(
            "extra",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_preset"),
        sa.UniqueConstraint("name", name="uq_campaign_preset_name"),
    )

    op.create_table(
        "campaign_run",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("preset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column(
            "progress",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_meta_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("created_by_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["preset_id"],
            ["campaign_preset.id"],
            name="fk_campaign_run_preset_id_campaign_preset",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_run"),
        sa.UniqueConstraint("idempotency_key", name="uq_campaign_run_idempotency_key"),
        sa.CheckConstraint(f"status IN ({_STATUS_IN})", name="ck_campaign_run_status"),
    )
    op.create_index("ix_campaign_run_status", "campaign_run", ["status"])
    op.create_index("ix_campaign_run_created_at", "campaign_run", ["created_at"])
    op.create_index("ix_campaign_run_preset", "campaign_run", ["preset_id"])

    # Регистрируем task_type='campaign_create' в CHECK task_queue (воркер campaign_creator).
    op.drop_constraint("ck_task_queue_task_type", "task_queue", type_="check")
    op.create_check_constraint(
        "ck_task_queue_task_type",
        "task_queue",
        "task_type IN ('disable', 'enable', 'plan_run', 'meta_api_mutation', "
        "'ad_library_scan', 'campaign_create')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_task_queue_task_type", "task_queue", type_="check")
    op.create_check_constraint(
        "ck_task_queue_task_type",
        "task_queue",
        "task_type IN ('disable', 'enable', 'plan_run', 'meta_api_mutation', 'ad_library_scan')",
    )
    op.drop_index("ix_campaign_run_preset", table_name="campaign_run")
    op.drop_index("ix_campaign_run_created_at", table_name="campaign_run")
    op.drop_index("ix_campaign_run_status", table_name="campaign_run")
    op.drop_table("campaign_run")
    op.drop_table("campaign_preset")
