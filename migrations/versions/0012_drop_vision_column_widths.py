# -*- coding: utf-8 -*-
"""Удалить vision_config.column_widths_json — ширины колонок DOM-эпохи.

column_widths_json хранил кастомные ширины колонок Ads Manager для выравнивания
DOM-парсинга. После перехода на am_tabular (graph-канал UI) DOM-скан выпилен,
ширины колонок больше не нужны — дропаем поле.

Revision ID: 0012_drop_vision_column_widths
Revises: 0011_observer_scan_source
Create Date: 2026-05-31
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0012_drop_vision_column_widths"
down_revision: str | None = "0011_observer_scan_source"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE vision_config DROP COLUMN IF EXISTS column_widths_json;")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE vision_config "
        "ADD COLUMN IF NOT EXISTS column_widths_json JSONB NOT NULL DEFAULT '{}'::jsonb;"
    )
