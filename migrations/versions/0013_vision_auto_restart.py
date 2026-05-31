# -*- coding: utf-8 -*-
"""Добавить vision_config.auto_restart_on_missing_cdp — флаг self-heal Vision-сессии.

При пропаже primary-вкладки Ads Manager (закрылась вкладка / отвалился CDP) observer
самовосстанавливает сессию: browser-agent переоткрывает вкладку, а Python-клиент при
неустранимой пропаже эскалирует reconnect/StartBrowser. Этот флаг (дефолт TRUE) даёт
ручной kill-switch для observer-side эскалации — без него мониторинг лежал ~104 минуты,
пока человек не перезапустил стек.

Цепочка за 0012_drop_vision_column_widths (am_tabular), чтобы не было multiple heads.

Revision ID: 0013_vision_auto_restart
Revises: 0012_drop_vision_column_widths
Create Date: 2026-05-31
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0013_vision_auto_restart"
down_revision: str | None = "0012_drop_vision_column_widths"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE vision_config "
        "ADD COLUMN IF NOT EXISTS auto_restart_on_missing_cdp BOOLEAN NOT NULL DEFAULT TRUE;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE vision_config DROP COLUMN IF EXISTS auto_restart_on_missing_cdp;")
