"""add creator_plans and creator_plan_runs

Revision ID: bbba0b1b80b5
Revises: 091a639e018a
Create Date: 2026-05-17 20:45:44.898771
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bbba0b1b80b5"
down_revision: str | None = "091a639e018a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Создаём enum явно, чтобы downgrade мог корректно его удалить.
    plan_run_status = postgresql.ENUM(
        "queued",
        "running",
        "success",
        "failed",
        "requires_attention",
        name="plan_run_status_enum",
        create_type=False,
    )
    plan_run_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "creator_plans",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "creator_plan_runs",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "queued",
                "running",
                "success",
                "failed",
                "requires_attention",
                name="plan_run_status_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("step_log", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["creator_plans.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Индексы для воркер-поллинга и lookup'ов по plan_id.
    op.create_index("ix_creator_plan_runs_plan_id", "creator_plan_runs", ["plan_id"])
    op.create_index("ix_creator_plan_runs_status", "creator_plan_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_creator_plan_runs_status", table_name="creator_plan_runs")
    op.drop_index("ix_creator_plan_runs_plan_id", table_name="creator_plan_runs")
    op.drop_table("creator_plan_runs")
    op.drop_table("creator_plans")
    postgresql.ENUM(name="plan_run_status_enum").drop(op.get_bind(), checkfirst=True)
