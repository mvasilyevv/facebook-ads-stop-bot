# -*- coding: utf-8 -*-
"""Возвращает forum-topic thread_id колонки в telegram_settings.

Revision ID: c1d2e3f4a5b6
Revises: bbba0b1b80b5
Create Date: 2026-05-18 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "bbba0b1b80b5"
branch_labels = None
depends_on = None

_COLUMNS = (
    "thread_id_warning",
    "thread_id_stop",
    "thread_id_enable",
    "thread_id_ops",
    "thread_id_general",
)


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("telegram_settings", sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    for name in reversed(_COLUMNS):
        op.drop_column("telegram_settings", name)
