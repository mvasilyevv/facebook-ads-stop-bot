# -*- coding: utf-8 -*-
"""Добавить явные gate-поля для ранних сигналов.

Revision ID: 011
Revises: 010
Create Date: 2026-03-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_outbound_ctr_signal_min_spend_percent",
            sa.Numeric(8, 2),
            nullable=False,
            server_default="5",
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_lpv_ratio_signal_min_outbound_clicks",
            sa.Integer(),
            nullable=False,
            server_default="5",
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_cost_per_lpv_signal_min_views",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
    )


def downgrade() -> None:
    op.drop_column("offer_rule_configs", "early_cost_per_lpv_signal_min_views")
    op.drop_column("offer_rule_configs", "early_lpv_ratio_signal_min_outbound_clicks")
    op.drop_column("offer_rule_configs", "early_outbound_ctr_signal_min_spend_percent")
