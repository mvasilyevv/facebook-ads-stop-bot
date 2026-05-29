# -*- coding: utf-8 -*-
"""Расширить observer_config.owner_campaign_tag до VARCHAR(255) для мультитега.

Owner-scoping теперь поддерживает несколько тегов через запятую ("MV,ABC,XYZ").
Старое одиночное значение ("MV") остаётся валидным (частный случай CSV).

Revision ID: 0006_owner_tag_multi
Revises: 0005_observer_owner_tag
Create Date: 2026-05-29
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0006_owner_tag_multi"
down_revision: str | None = "0005_observer_owner_tag"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE observer_config ALTER COLUMN owner_campaign_tag TYPE VARCHAR(255);")


def downgrade() -> None:
    # Обрезка до 64 при откате (значения длиннее 64 будут усечены).
    op.execute(
        "ALTER TABLE observer_config "
        "ALTER COLUMN owner_campaign_tag TYPE VARCHAR(64) USING LEFT(owner_campaign_tag, 64);"
    )
