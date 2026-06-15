# -*- coding: utf-8 -*-
"""Мульти-кабинет (MULTI_CABINET_PLAN.md, этап M1).

Три колонки:
- offers.ad_account_ids — per-offer список кабинетов (числовые ID без act_);
  scan set observer'а = union по активным офферам. Дефолт '{}' — оффер вне скана
  до явного заполнения (валидация min 1 — на уровне API).
- fb_campaigns.ad_account_id — из какого кабинета кампания просканирована
  (NULL — исторические записи). Источник для роутинга мутаций во вкладку кабинета.
- scan_runs.ad_account_id — какой кабинет сканировался в цикле (partitioned-таблица,
  добавление nullable-колонки безопасно).

Цепочка за 0018_adsetpro_fb_ad_id_index.

Revision ID: 0019_multi_cabinet
Revises: 0018_adsetpro_fb_ad_id_index
Create Date: 2026-06-09
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "0019_multi_cabinet"
down_revision: str | None = "0018_adsetpro_fb_ad_id_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column(
            "ad_account_ids",
            ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "fb_campaigns",
        sa.Column("ad_account_id", sa.String(32), nullable=True),
    )
    op.create_index(
        "ix_fb_campaigns_ad_account",
        "fb_campaigns",
        ["ad_account_id"],
        postgresql_where=sa.text("ad_account_id IS NOT NULL"),
    )
    op.add_column(
        "scan_runs",
        sa.Column("ad_account_id", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scan_runs", "ad_account_id")
    op.drop_index("ix_fb_campaigns_ad_account", table_name="fb_campaigns")
    op.drop_column("fb_campaigns", "ad_account_id")
    op.drop_column("offers", "ad_account_ids")
