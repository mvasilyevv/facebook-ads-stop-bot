# -*- coding: utf-8 -*-
"""Добавляет поле auto_enable_recommendations в observer_settings.

Revision ID: c3f1a2b4d5e6
Revises: b2912a123fdf
Create Date: 2026-04-23 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3f1a2b4d5e6"
down_revision = "a7c3e9f1d2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_settings",
        sa.Column(
            "auto_enable_recommendations",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "auto_enable_recommendations")
