# -*- coding: utf-8 -*-
"""Добавляет runtime-поля расписания сканирования observer.

Revision ID: f6a7b8c9d0e1
Revises: d4e5f6a7b8c1
Create Date: 2026-04-24 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "d4e5f6a7b8c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_settings",
        sa.Column("current_scan_interval_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "observer_settings",
        sa.Column("current_scan_jitter_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "observer_settings",
        sa.Column("current_scan_threat_level", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "observer_settings",
        sa.Column("next_scan_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "next_scan_at")
    op.drop_column("observer_settings", "current_scan_threat_level")
    op.drop_column("observer_settings", "current_scan_jitter_seconds")
    op.drop_column("observer_settings", "current_scan_interval_seconds")
