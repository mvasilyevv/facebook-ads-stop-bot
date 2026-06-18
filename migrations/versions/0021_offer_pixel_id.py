# -*- coding: utf-8 -*-
"""Offer.pixel_id — FB Pixel ID оффера (для создания кампаний: событие оптимизации).

Добавляет nullable-колонку pixel_id в offers. Прописывается в карточке оффера,
используется при создании кампаний как пиксель события Purchase/FTD.

Безопасно: nullable, без дефолта, существующие офферы получают NULL.

Цепочка за 0020_campaign_identity.

Revision ID: 0021_offer_pixel_id
Revises: 0020_campaign_identity
Create Date: 2026-06-18
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_offer_pixel_id"
down_revision: str | None = "0020_campaign_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("pixel_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "pixel_id")
