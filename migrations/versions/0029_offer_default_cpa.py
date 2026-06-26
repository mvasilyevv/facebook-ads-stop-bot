"""offers.default_cpa_cents — дефолтный целевой CPA оффера (центы).

Визард префиллит поле «Целевой CPA, $» на шаге «Параметры» из этого значения
(как кабинет/пиксель/гео тянутся из оффера). NULL — дефолт не задан.

Revision ID: 0029_offer_default_cpa
Revises: 0028_creative_seq_ledger
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_offer_default_cpa"
down_revision = "0028_creative_seq_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("default_cpa_cents", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "default_cpa_cents")
