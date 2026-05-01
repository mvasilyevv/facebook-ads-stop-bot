# -*- coding: utf-8 -*-
"""Добавляет страну оффера для сценариев создания кампаний.

Revision ID: 0f1a2b3c4d5e
Revises: b8c9d0e1f2a3
Create Date: 2026-04-27 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0f1a2b3c4d5e"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавляет country_name в офферы."""
    op.add_column("offers", sa.Column("country_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    """Удаляет country_name из офферов."""
    op.drop_column("offers", "country_name")
