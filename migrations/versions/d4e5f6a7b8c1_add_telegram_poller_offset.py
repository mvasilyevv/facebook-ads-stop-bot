# -*- coding: utf-8 -*-
"""Добавляет offset Telegram poller-а."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c1"
down_revision: str | None = "3977ebe2c606"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Добавляет сохранение offset Telegram poller-а."""
    op.add_column("telegram_settings", sa.Column("poller_offset", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Удаляет сохранение offset Telegram poller-а."""
    op.drop_column("telegram_settings", "poller_offset")
