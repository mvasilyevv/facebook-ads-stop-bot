# -*- coding: utf-8 -*-
"""Store the human-visible Ads Manager columns preset."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_am_columns_setting"
down_revision = "0004_vision_token_self_heal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_config",
        sa.Column("am_columns_qs", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError("Ads Manager columns setting migration is forward-only")
