"""add_context_json_to_campaign_creator_tasks

Revision ID: c8d9e0f1a2b3
Revises: 5a89e836b225
Create Date: 2026-05-12 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: str | None = "5a89e836b225"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_creator_tasks",
        sa.Column("context_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("campaign_creator_tasks", "context_json")
