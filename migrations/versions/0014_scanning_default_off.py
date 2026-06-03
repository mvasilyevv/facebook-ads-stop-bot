# -*- coding: utf-8 -*-
"""Сменить дефолт observer_config.is_scanning_enabled на FALSE.

Чистая установка не должна начинать сканирование кабинета без явного включения
(тумблер «Сканирование»). Меняем только DEFAULT столбца — существующую singleton-строку
НЕ трогаем (её значением управляет оператор через UI).

Цепочка за 0013_vision_auto_restart.

Revision ID: 0014_scanning_default_off
Revises: 0013_vision_auto_restart
Create Date: 2026-05-31
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0014_scanning_default_off"
down_revision: str | None = "0013_vision_auto_restart"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE observer_config ALTER COLUMN is_scanning_enabled SET DEFAULT false;")


def downgrade() -> None:
    op.execute("ALTER TABLE observer_config ALTER COLUMN is_scanning_enabled SET DEFAULT true;")
