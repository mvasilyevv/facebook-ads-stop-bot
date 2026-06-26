"""Дроп offers.default_cpa_cents — целевой CPA консолидирован в offer_rules.cpa_threshold.

Отдельное поле дефолтного CPA оказалось дублем: единый целевой CPA оффера живёт в
правилах (offer_rules.cpa_threshold) и используется и для стоп-порогов, и для
префилла бида визарда. Колонка default_cpa_cents больше не нужна.

Revision ID: 0030_drop_offer_default_cpa
Revises: 0029_offer_default_cpa
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_drop_offer_default_cpa"
down_revision = "0029_offer_default_cpa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("offers", "default_cpa_cents")


def downgrade() -> None:
    op.add_column("offers", sa.Column("default_cpa_cents", sa.Integer(), nullable=True))
