"""Добавить per-offer проценты warning/stop в offer_rule_configs.

NULL означает «использовать глобальное значение из observer_settings».

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-05-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


_NEW_COLUMNS = (
    "warning_percent_of_stop",
    "stop_percent_of_base",
    "cpc_warning_percent_of_stop",
    "cpc_stop_percent_of_base",
    "cpl_warning_percent_of_stop",
    "cpl_stop_percent_of_base",
    "cpr_warning_percent_of_stop",
    "cpr_stop_percent_of_base",
)


def upgrade() -> None:
    for column in _NEW_COLUMNS:
        op.add_column(
            "offer_rule_configs",
            sa.Column(column, sa.Numeric(6, 2), nullable=True),
        )


def downgrade() -> None:
    for column in _NEW_COLUMNS:
        op.drop_column("offer_rule_configs", column)
