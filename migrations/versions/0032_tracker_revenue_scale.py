"""tracker_aggregate.revenue: Numeric(12,2) → Numeric(12,4) (MID-15).

Источник (adsetpro_postback_events / AdSet.pro postback payload) хранит revenue
с точностью 4 знака после запятой. tracker_aggregate.revenue был объявлен как
Numeric(12,2) — при агрегации (см. core/adset_pro/aggregator.py) сумма округлялась
до копеек и теряла 2 младших разряда источника. Расширение scale 2→4 —
безопасное (Postgres расширяет NUMERIC(p,s) без переписи таблицы, ALTER TYPE
той же базовой категории), данных не теряет, увеличивает точность.

Revision ID: 0032_tracker_revenue_scale
Revises: 0031_default_partitions
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_tracker_revenue_scale"
down_revision = "0031_default_partitions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tracker_aggregate",
        "revenue",
        type_=sa.Numeric(12, 4),
        existing_type=sa.Numeric(12, 2),
        existing_nullable=False,
        existing_server_default="0",
    )


def downgrade() -> None:
    # ВНИМАНИЕ: сужение scale 4→2 округляет уже накопленные значения (lossy).
    op.alter_column(
        "tracker_aggregate",
        "revenue",
        type_=sa.Numeric(12, 2),
        existing_type=sa.Numeric(12, 4),
        existing_nullable=False,
        existing_server_default="0",
    )
