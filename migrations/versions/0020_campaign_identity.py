# -*- coding: utf-8 -*-
"""Идентичность кампании = fb_campaign_id, не имя (HIGH-3 из docs/multi_cabinet_audit.md).

Проблема: UNIQUE(campaign_name) сливал одноимённые кампании РАЗНЫХ кабинетов в одну
строку каталога (ads обоих кабинетов цеплялись к ней, ad_account_id прыгал между
сканами). Деньги не страдали (pause/enable точно по ad_id), но history/аналитика
смешивала кабинеты.

Фикс: уникальность по fb_campaign_id (partial unique ix_fb_campaigns_fb_id_unique
уже существует с 0001), имя — обычный неуникальный индекс для lookup'ов.
Upsert в core/observer/writers.py переведён на ON CONFLICT (fb_campaign_id).

Downgrade вернёт UNIQUE(campaign_name) и УПАДЁТ, если за время работы появились
дубли имён (одноимённые кампании разных кабинетов) — это ожидаемо: сначала
разрулить дубли вручную.

Цепочка за 0019_multi_cabinet.

Revision ID: 0020_campaign_identity
Revises: 0019_multi_cabinet
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0020_campaign_identity"
down_revision: str | None = "0019_multi_cabinet"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_fb_campaigns_campaign_name", "fb_campaigns", type_="unique")
    # Неуникальный индекс на имя — для fallback-lookup'а в writers и фильтров UI.
    op.create_index("ix_fb_campaigns_campaign_name", "fb_campaigns", ["campaign_name"])


def downgrade() -> None:
    op.drop_index("ix_fb_campaigns_campaign_name", table_name="fb_campaigns")
    op.create_unique_constraint(
        "uq_fb_campaigns_campaign_name", "fb_campaigns", ["campaign_name"]
    )
