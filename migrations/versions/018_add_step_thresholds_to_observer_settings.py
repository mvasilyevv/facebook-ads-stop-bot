# -*- coding: utf-8 -*-
"""Добавить step-level пороги observer для CPC/CPL/CPR.

Revision ID: 018
Revises: 017
Create Date: 2026-03-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_settings",
        sa.Column(
            "cpc_warning_percent_of_stop",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="80",
        ),
    )
    op.add_column(
        "observer_settings",
        sa.Column(
            "cpc_stop_percent_of_base",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="100",
        ),
    )
    op.add_column(
        "observer_settings",
        sa.Column(
            "cpl_warning_percent_of_stop",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="80",
        ),
    )
    op.add_column(
        "observer_settings",
        sa.Column(
            "cpl_stop_percent_of_base",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="100",
        ),
    )
    op.add_column(
        "observer_settings",
        sa.Column(
            "cpr_warning_percent_of_stop",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="80",
        ),
    )
    op.add_column(
        "observer_settings",
        sa.Column(
            "cpr_stop_percent_of_base",
            sa.Numeric(6, 2),
            nullable=False,
            server_default="100",
        ),
    )

    op.execute(
        """
        UPDATE observer_settings
        SET
            cpc_warning_percent_of_stop = warning_percent_of_stop,
            cpc_stop_percent_of_base = stop_percent_of_base,
            cpl_warning_percent_of_stop = warning_percent_of_stop,
            cpl_stop_percent_of_base = stop_percent_of_base,
            cpr_warning_percent_of_stop = warning_percent_of_stop,
            cpr_stop_percent_of_base = stop_percent_of_base
        """
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "cpr_stop_percent_of_base")
    op.drop_column("observer_settings", "cpr_warning_percent_of_stop")
    op.drop_column("observer_settings", "cpl_stop_percent_of_base")
    op.drop_column("observer_settings", "cpl_warning_percent_of_stop")
    op.drop_column("observer_settings", "cpc_stop_percent_of_base")
    op.drop_column("observer_settings", "cpc_warning_percent_of_stop")
