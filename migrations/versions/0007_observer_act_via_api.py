# -*- coding: utf-8 -*-
"""Добавить observer_config.act_via_api — канал исполнения toggle-действий.

FALSE (дефолт) — disable/enable через DOM-клик browser-agent (текущее поведение).
TRUE — через Marketing API (meta_api_mutation pause_ad/activate_ad): точно по ad_id,
не промахивается по кнопке. Detect всегда через DOM, меняется только act (#39).

Revision ID: 0007_observer_act_via_api
Revises: 0006_owner_tag_multi
Create Date: 2026-05-29
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0007_observer_act_via_api"
down_revision: str | None = "0006_owner_tag_multi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE observer_config "
        "ADD COLUMN IF NOT EXISTS act_via_api BOOLEAN NOT NULL DEFAULT FALSE;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE observer_config DROP COLUMN IF EXISTS act_via_api;")
