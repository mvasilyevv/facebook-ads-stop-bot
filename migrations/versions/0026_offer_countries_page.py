# -*- coding: utf-8 -*-
"""Offer.countries + Offer.default_page_id — offer-centric визард (Фаза 1).

Две колонки в offers:
- countries — ARRAY(text), дефолт '{}' (как ad_account_ids). Гео оффера (ISO-2 upper,
  мультигео); визард префиллит goal.countries из этого списка.
- default_page_id — text NULL. FB Page ID обычной страницы оффера; преселект в дропдауне
  страниц кабинета при создании кампании. Опционально.

Безопасно на проде с данными: обе колонки additive, countries имеет server_default '{}'
(существующие офферы получают пустой массив), default_page_id nullable без дефолта (NULL).

Цепочка за 0025_campaign_creation.

Revision ID: 0026_offer_countries_page
Revises: 0025_campaign_creation
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0026_offer_countries_page"
down_revision: str | None = "0025_campaign_creation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column(
            "countries",
            ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "offers",
        sa.Column("default_page_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("offers", "default_page_id")
    op.drop_column("offers", "countries")
