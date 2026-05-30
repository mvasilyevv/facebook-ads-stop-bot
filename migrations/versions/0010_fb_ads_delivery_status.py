# -*- coding: utf-8 -*-
"""Добавить fb_ads.delivery_status — текущий статус доставки объявления.

BL-12-mig: закрытие shape-расхождения frontend↔backend. Сканер уже захватывает
delivery_status в ScannedAdRow (DOM-ячейка Ads Manager / маппинг Meta
effective_status), но pipeline дропал его — в схеме не было колонки, а dashboard
отдавал фронту захардкоженный null.

Решение по месту: delivery_status — это ТЕКУЩЕЕ состояние объявления (как
is_active), а не метрика. Поэтому колонка живёт в каталоге fb_ads и обновляется
upsert'ом на каждом скане (writers.upsert_catalog_hierarchy), а не в
партиционированной ad_metrics (не раздуваем партиции low-cardinality строкой,
которую snapshot и так JOIN'ит из fb_ads без доп. стоимости).

nullable без default — существующие строки fb_ads остаются с NULL до первого
скана, который проставит реальный статус. Идемпотентный DDL (IF NOT EXISTS) —
безопасно при повторном прогоне.

Revision ID: 0010_fb_ads_delivery_status
Revises: 0008_act_via_api_default_true
Create Date: 2026-05-30
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

# 0009 намеренно зарезервирован под параллельный BL-8 — цепляемся к 0008.
revision: str = "0010_fb_ads_delivery_status"
down_revision: str | None = "0008_act_via_api_default_true"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE fb_ads ADD COLUMN IF NOT EXISTS delivery_status VARCHAR(64);")


def downgrade() -> None:
    op.execute("ALTER TABLE fb_ads DROP COLUMN IF EXISTS delivery_status;")
