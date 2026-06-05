# -*- coding: utf-8 -*-
"""Удалить observer_config.act_via_api — DOM-канал toggle-действий выпилен.

Отключение/включение рекламы теперь всегда через Marketing API
(meta_api_mutation pause_ad/activate_ad). DOM-toggle (browser-agent toggle_ad)
и сам флаг выбора канала больше не нужны — колонка удаляется.

downgrade восстанавливает колонку с DEFAULT TRUE (состояние до удаления,
после 0008_act_via_api_default_true), чтобы откат был симметричным.

Revision ID: 0016_drop_observer_act_via_api
Revises: 0015_offer_rule_sensitivity
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0016_drop_observer_act_via_api"
down_revision: str | None = "0015_offer_rule_sensitivity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE observer_config DROP COLUMN IF EXISTS act_via_api;")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE observer_config "
        "ADD COLUMN IF NOT EXISTS act_via_api BOOLEAN NOT NULL DEFAULT TRUE;"
    )
