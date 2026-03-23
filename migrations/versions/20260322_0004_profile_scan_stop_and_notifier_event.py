"""Добавляет стоп сканирования на уровне профиля и новый тип notifier-события.

Revision ID: 20260322_0004
Revises: 20260321_0003
Create Date: 2026-03-22 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260322_0004"
down_revision = "20260321_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column("scan_suspended", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "profiles", sa.Column("scan_suspend_reason", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "profiles", sa.Column("scan_suspend_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "ALTER TYPE telegram_event_type_enum ADD VALUE IF NOT EXISTS 'SCAN_SOURCE_UNAVAILABLE'"
    )


def downgrade() -> None:
    op.drop_column("profiles", "scan_suspend_at")
    op.drop_column("profiles", "scan_suspend_reason")
    op.drop_column("profiles", "scan_suspended")
