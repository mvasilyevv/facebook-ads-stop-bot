# -*- coding: utf-8 -*-
"""Добавить EARLY_SIGNAL, новые post-click метрики и подробные причины алертов.

Revision ID: 010
Revises: 009
Create Date: 2026-03-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def _add_enum_values() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute("ALTER TYPE alert_stage_enum ADD VALUE IF NOT EXISTS 'EARLY_SIGNAL'")
    op.execute("ALTER TYPE alert_state_enum ADD VALUE IF NOT EXISTS 'EARLY_SIGNAL_SENT'")


def upgrade() -> None:
    _add_enum_values()

    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_outbound_ctr_signal_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_outbound_ctr_signal_min_percent",
            sa.Numeric(8, 2),
            nullable=False,
            server_default="0.80",
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_lpv_ratio_signal_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_lpv_ratio_signal_min_percent",
            sa.Numeric(8, 2),
            nullable=False,
            server_default="60",
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_cost_per_lpv_signal_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "early_cost_per_lpv_signal_percent_of_cpa",
            sa.Numeric(8, 2),
            nullable=False,
            server_default="5",
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "frequency_elevated_threshold",
            sa.Numeric(8, 2),
            nullable=False,
            server_default="2",
        ),
    )
    op.add_column(
        "offer_rule_configs",
        sa.Column(
            "frequency_critical_threshold",
            sa.Numeric(8, 2),
            nullable=False,
            server_default="3",
        ),
    )

    op.add_column(
        "ad_snapshots",
        sa.Column(
            "outbound_clicks",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column("outbound_ctr", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column(
            "landing_page_views",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column("cost_per_landing_page_view", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column("cpm", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column("frequency", sa.Numeric(12, 4), nullable=True),
    )
    op.add_column(
        "ad_snapshots",
        sa.Column(
            "early_signal_rule_codes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    op.add_column(
        "alert_events",
        sa.Column("reason_title", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "alert_events",
        sa.Column("reason_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alert_events", "reason_text")
    op.drop_column("alert_events", "reason_title")

    op.drop_column("ad_snapshots", "early_signal_rule_codes")
    op.drop_column("ad_snapshots", "frequency")
    op.drop_column("ad_snapshots", "cpm")
    op.drop_column("ad_snapshots", "cost_per_landing_page_view")
    op.drop_column("ad_snapshots", "landing_page_views")
    op.drop_column("ad_snapshots", "outbound_ctr")
    op.drop_column("ad_snapshots", "outbound_clicks")

    op.drop_column("offer_rule_configs", "frequency_critical_threshold")
    op.drop_column("offer_rule_configs", "frequency_elevated_threshold")
    op.drop_column("offer_rule_configs", "early_cost_per_lpv_signal_percent_of_cpa")
    op.drop_column("offer_rule_configs", "early_cost_per_lpv_signal_enabled")
    op.drop_column("offer_rule_configs", "early_lpv_ratio_signal_min_percent")
    op.drop_column("offer_rule_configs", "early_lpv_ratio_signal_enabled")
    op.drop_column("offer_rule_configs", "early_outbound_ctr_signal_min_percent")
    op.drop_column("offer_rule_configs", "early_outbound_ctr_signal_enabled")
