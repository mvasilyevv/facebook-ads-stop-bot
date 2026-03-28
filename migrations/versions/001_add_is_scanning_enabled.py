# -*- coding: utf-8 -*-
"""Добавить поле is_scanning_enabled в observer_settings.

Revision ID: 001_scanning_flag
Revises:
Create Date: 2026-03-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "001_scanning_flag"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Добавляем колонку is_scanning_enabled с дефолтным значением True
    op.add_column(
        "observer_settings",
        sa.Column("is_scanning_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    # Удаляем колонку при откате
    op.drop_column("observer_settings", "is_scanning_enabled")
