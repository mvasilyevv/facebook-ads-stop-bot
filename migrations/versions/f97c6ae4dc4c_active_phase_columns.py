"""active_phase columns

Revision ID: f97c6ae4dc4c
Revises: 5b3af4f6df36
Create Date: 2026-05-22 17:32:05.839544
"""

import sqlalchemy as sa
from alembic import op

revision = "f97c6ae4dc4c"
down_revision = "5b3af4f6df36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "observer_settings",
        sa.Column("active_phase", sa.String(32), nullable=True),
    )
    op.add_column(
        "observer_settings",
        sa.Column("phase_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("observer_settings", "phase_started_at")
    op.drop_column("observer_settings", "active_phase")
