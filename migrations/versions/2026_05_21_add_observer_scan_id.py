"""add observer scan_id tracking

Revision ID: 884763540a4c
Revises: 6ada1843542c
Create Date: 2026-05-21 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "884763540a4c"
down_revision: str | None = "6ada1843542c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Монотонный счётчик циклов observer для отслеживания «последнего батча».
    op.add_column(
        "observer_settings",
        sa.Column("current_scan_id", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Идентификатор последнего scan-цикла, обновившего эту запись (не FK).
    op.add_column(
        "ad_snapshots",
        sa.Column("last_scan_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_ad_snapshots_last_scan_id",
        "ad_snapshots",
        ["last_scan_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ad_snapshots_last_scan_id", table_name="ad_snapshots")
    op.drop_column("ad_snapshots", "last_scan_id")
    op.drop_column("observer_settings", "current_scan_id")
