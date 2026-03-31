# -*- coding: utf-8 -*-
"""Добавить недостающие колонки Ads Manager в ad_snapshots.

Revision ID: 017
Revises: 016
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ad_snapshots",
        sa.Column(
            "budget",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column(
            "reach",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column(
            "impressions",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column("ctr", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column("cost_per_result", sa.Numeric(12, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ad_snapshots", "cost_per_result")
    op.drop_column("ad_snapshots", "ctr")
    op.drop_column("ad_snapshots", "impressions")
    op.drop_column("ad_snapshots", "reach")
    op.drop_column("ad_snapshots", "budget")
