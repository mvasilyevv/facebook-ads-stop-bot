"""Сделать per-offer пороги NOT NULL с дефолтом 80/80.

Убираем nullable с полей warning/stop в offer_rule_configs.
Убираем глобальные пороги из observer_settings.

Revision ID: e8f9a0b1c2d3
Revises: 31684a725d7e
Create Date: 2026-05-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8f9a0b1c2d3"
down_revision = "31684a725d7e"
branch_labels = None
depends_on = None

_OFFER_THRESHOLD_COLUMNS = (
    ("warning_percent_of_stop", "80"),
    ("stop_percent_of_base", "80"),
    ("cpc_warning_percent_of_stop", "80"),
    ("cpc_stop_percent_of_base", "80"),
    ("cpl_warning_percent_of_stop", "80"),
    ("cpl_stop_percent_of_base", "80"),
    ("cpr_warning_percent_of_stop", "80"),
    ("cpr_stop_percent_of_base", "80"),
)

_OBSERVER_THRESHOLD_COLUMNS = (
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
    # 1. offer_rule_configs: заполнить NULL → дефолт, затем NOT NULL
    for col, default in _OFFER_THRESHOLD_COLUMNS:
        op.execute(f"UPDATE offer_rule_configs SET {col} = {default} WHERE {col} IS NULL")
        op.alter_column(
            "offer_rule_configs",
            col,
            existing_type=sa.Numeric(6, 2),
            nullable=False,
            server_default=default,
        )

    # 2. observer_settings: удалить глобальные пороги (они больше не нужны)
    for col in _OBSERVER_THRESHOLD_COLUMNS:
        op.drop_column("observer_settings", col)


def downgrade() -> None:
    # Восстановить колонки в observer_settings
    for col in reversed(_OBSERVER_THRESHOLD_COLUMNS):
        op.add_column(
            "observer_settings",
            sa.Column(col, sa.Numeric(6, 2), nullable=True),
        )

    # offer_rule_configs: вернуть nullable
    for col, _ in reversed(_OFFER_THRESHOLD_COLUMNS):
        op.alter_column(
            "offer_rule_configs",
            col,
            existing_type=sa.Numeric(6, 2),
            nullable=True,
            server_default=None,
        )
