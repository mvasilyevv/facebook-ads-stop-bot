"""offer creator fields: landing_url, cabinet_id, pixel_id, geo_code, geo_slot_name

Revision ID: 5a89e836b225
Revises: e8f9a0b1c2d3
Create Date: 2026-05-12 20:03:05.379696
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5a89e836b225"
down_revision: str | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("landing_url", sa.String(length=512), nullable=True))
    op.add_column("offers", sa.Column("cabinet_id", sa.String(length=64), nullable=True))
    op.add_column("offers", sa.Column("pixel_id", sa.String(length=64), nullable=True))
    op.add_column("offers", sa.Column("geo_code", sa.String(length=2), nullable=True))
    op.add_column("offers", sa.Column("geo_slot_name", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "geo_slot_name")
    op.drop_column("offers", "geo_code")
    op.drop_column("offers", "pixel_id")
    op.drop_column("offers", "cabinet_id")
    op.drop_column("offers", "landing_url")
