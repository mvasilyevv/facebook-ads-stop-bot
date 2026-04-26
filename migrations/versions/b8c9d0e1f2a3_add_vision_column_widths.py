"""add vision column widths

Revision ID: b8c9d0e1f2a3
Revises: f6a7b8c9d0e1
Create Date: 2026-04-26 10:05:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b8c9d0e1f2a3"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавить сохранённый слепок ширины колонок Ads Manager."""
    op.add_column(
        "vision_settings",
        sa.Column(
            "column_widths_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.alter_column("vision_settings", "column_widths_json", server_default=None)


def downgrade() -> None:
    """Удалить сохранённый слепок ширины колонок Ads Manager."""
    op.drop_column("vision_settings", "column_widths_json")
