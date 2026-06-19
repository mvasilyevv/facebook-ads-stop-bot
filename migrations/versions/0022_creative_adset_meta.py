# -*- coding: utf-8 -*-
"""Волна 1: превью креатива (fb_ads) + метаданные адсета (fb_adsets).

fb_ads:
  creative_thumb_url / creative_image_url — превью креатива из Graph
  (creative.thumbnail_url / image_url). Обновляется на каждом скане (URL истекает).

fb_adsets:
  pixel_id          — promoted_object.pixel_id (сверка с offer.pixel_id)
  daily_budget      — daily_budget (minor units, как отдаёт Meta)
  lifetime_budget   — lifetime_budget
  budget_remaining  — budget_remaining
  learning_stage    — learning_stage_info.status (LEARNING/LEARNING_LIMITED)

Безопасно: все колонки nullable, без дефолтов — существующие строки получают NULL,
заполнятся на ближайшем скане. Метрики/FSM не затрагиваются.

Цепочка за 0021_offer_pixel_id.

Revision ID: 0022_creative_adset_meta
Revises: 0021_offer_pixel_id
Create Date: 2026-06-19
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_creative_adset_meta"
down_revision: str | None = "0021_offer_pixel_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("fb_ads", sa.Column("creative_thumb_url", sa.String(length=1024), nullable=True))
    op.add_column("fb_ads", sa.Column("creative_image_url", sa.String(length=1024), nullable=True))
    op.add_column("fb_adsets", sa.Column("pixel_id", sa.String(length=64), nullable=True))
    op.add_column("fb_adsets", sa.Column("daily_budget", sa.String(length=32), nullable=True))
    op.add_column("fb_adsets", sa.Column("lifetime_budget", sa.String(length=32), nullable=True))
    op.add_column("fb_adsets", sa.Column("budget_remaining", sa.String(length=32), nullable=True))
    op.add_column("fb_adsets", sa.Column("learning_stage", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("fb_adsets", "learning_stage")
    op.drop_column("fb_adsets", "budget_remaining")
    op.drop_column("fb_adsets", "lifetime_budget")
    op.drop_column("fb_adsets", "daily_budget")
    op.drop_column("fb_adsets", "pixel_id")
    op.drop_column("fb_ads", "creative_image_url")
    op.drop_column("fb_ads", "creative_thumb_url")
