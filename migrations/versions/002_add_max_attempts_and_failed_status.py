# -*- coding: utf-8 -*-
"""Добавить поле max_attempts в disable_tasks и статус FAILED в enum.

Revision ID: 002_max_attempts_failed
Revises: 001_scanning_flag
Create Date: 2026-03-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002_max_attempts_failed"
down_revision: str | None = "001_scanning_flag"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Добавляем значение FAILED в enum disable_task_status_enum
    op.execute("ALTER TYPE disable_task_status_enum ADD VALUE IF NOT EXISTS 'FAILED'")

    # Добавляем колонку max_attempts с дефолтным значением 10
    op.add_column(
        "disable_tasks",
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("10")),
    )


def downgrade() -> None:
    # Удаляем колонку max_attempts
    op.drop_column("disable_tasks", "max_attempts")

    # Примечание: удаление значения из enum в PostgreSQL требует пересоздания типа,
    # что сложно при наличии данных. Оставляем FAILED в enum при откате.
