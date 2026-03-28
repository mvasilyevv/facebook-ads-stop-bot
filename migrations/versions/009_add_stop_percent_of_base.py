# -*- coding: utf-8 -*-
"""Добавить глобальный коэффициент досрочного стопа в observer settings.

Revision ID: 009
Revises: 008
Create Date: 2026-03-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_settings",
        sa.Column(
            "stop_percent_of_base",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="100",
        ),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "stop_percent_of_base")
