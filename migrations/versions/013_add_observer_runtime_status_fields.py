# -*- coding: utf-8 -*-
"""Добавить runtime-статус observer worker.

Revision ID: 013
Revises: 012
Create Date: 2026-03-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_settings",
        sa.Column("worker_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "observer_settings",
        sa.Column("worker_message", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "observer_settings",
        sa.Column("worker_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "observer_settings",
        sa.Column("worker_last_error", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "observer_settings",
        sa.Column("worker_last_error_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "worker_last_error_at")
    op.drop_column("observer_settings", "worker_last_error")
    op.drop_column("observer_settings", "worker_heartbeat_at")
    op.drop_column("observer_settings", "worker_message")
    op.drop_column("observer_settings", "worker_status")
