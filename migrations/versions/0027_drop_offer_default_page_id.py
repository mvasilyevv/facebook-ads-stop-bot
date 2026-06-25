# -*- coding: utf-8 -*-
"""Откат Offer.default_page_id — страница FB не свойство оффера, а контекст залива.

Решение владельца: выбор FB-страницы делается на шаге создания кампании из дропдауна
кабинета (act_{id}/promote_pages), а не префиллится из оффера. Колонка
offers.default_page_id (добавлена в 0026) больше не нужна — дропаем.

countries (гео оффера, та же миграция 0026) — валидное свойство оффера, ОСТАЁТСЯ.

Безопасно на проде: 0026 уже применена (колонка существует), DROP COLUMN убирает
неиспользуемое поле. downgrade возвращает колонку nullable (без данных).

Цепочка за 0026_offer_countries_page (текущая голова).

Revision ID: 0027_drop_offer_default_page_id
Revises: 0026_offer_countries_page
Create Date: 2026-06-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_drop_offer_default_page_id"
down_revision: str | None = "0026_offer_countries_page"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("offers", "default_page_id")


def downgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("default_page_id", sa.String(length=64), nullable=True),
    )
