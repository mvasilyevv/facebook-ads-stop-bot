# -*- coding: utf-8 -*-
"""Добавить флаг scan_requested в observer_settings для запуска скана из UI.

Revision ID: 007
Revises: 006
Create Date: 2026-03-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_settings",
        sa.Column(
            "scan_requested",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "scan_requested")
