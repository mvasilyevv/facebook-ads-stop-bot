# -*- coding: utf-8 -*-
"""Store Vision cloud credentials and token-refresh attempt state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_vision_token_self_heal"
down_revision = "0003_tighten_retention_policy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vision_config",
        sa.Column("username_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "vision_config",
        sa.Column("password_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "vision_config",
        sa.Column("team_id_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "vision_config",
        sa.Column("folder_id_encrypted", sa.Text(), nullable=True),
    )
    op.add_column(
        "vision_config",
        sa.Column(
            "token_refresh_attempted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Vision token self-heal migration is forward-only")
