# -*- coding: utf-8 -*-
"""Per-offer чувствительность правил: stop_percent_of_rule + warning_percent_of_stop.

Два коэффициента в offer_rules регулируют НЕ сами правила (базовые проценты 2/10/20/…
зафиксированы в коде), а при каком % от правила/стопа срабатывает стоп/ворнинг.
Дефолт 80/80 — ровно как было захардкожено в build_rule_context, поведение не меняется.

Цепочка за 0014_scanning_default_off.

Revision ID: 0015_offer_rule_sensitivity
Revises: 0014_scanning_default_off
Create Date: 2026-06-03
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_offer_rule_sensitivity"
down_revision: str | None = "0014_scanning_default_off"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "offer_rules",
        sa.Column(
            "stop_percent_of_rule",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="80",
        ),
    )
    op.add_column(
        "offer_rules",
        sa.Column(
            "warning_percent_of_stop",
            sa.Numeric(5, 2),
            nullable=False,
            server_default="80",
        ),
    )


def downgrade() -> None:
    op.drop_column("offer_rules", "warning_percent_of_stop")
    op.drop_column("offer_rules", "stop_percent_of_rule")
