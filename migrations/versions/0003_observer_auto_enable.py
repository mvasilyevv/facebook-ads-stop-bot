# -*- coding: utf-8 -*-
"""Добавить observer_config.auto_enable_recommendations.

Колонка управляет автоматическим применением рекомендаций по re-enable объявлений.
Фронт ожидает это поле через PATCH /settings/observer/auto-enable.

Revision ID: 0003_observer_auto_enable
Revises: 0002_taskq_chat_id
Create Date: 2026-05-27
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0003_observer_auto_enable"
down_revision: str | None = "0002_taskq_chat_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Добавляем boolean-флаг с дефолтом false — существующие строки получают false
    # без ручного заполнения. NOT NULL обеспечивается DEFAULT.
    op.execute(
        """
        ALTER TABLE observer_config
        ADD COLUMN IF NOT EXISTS auto_enable_recommendations BOOLEAN NOT NULL DEFAULT false;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE observer_config DROP COLUMN IF EXISTS auto_enable_recommendations;")
