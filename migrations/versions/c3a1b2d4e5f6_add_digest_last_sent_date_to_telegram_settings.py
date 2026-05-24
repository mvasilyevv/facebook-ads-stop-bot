"""add digest_last_sent_date to telegram_settings

Revision ID: c3a1b2d4e5f6
Revises: 02df0e95f3d5
Create Date: 2026-05-24 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "c3a1b2d4e5f6"
down_revision = "02df0e95f3d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_settings",
        sa.Column("digest_last_sent_date", sa.String(length=10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_settings", "digest_last_sent_date")
