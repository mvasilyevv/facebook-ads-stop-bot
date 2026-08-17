# -*- coding: utf-8 -*-
"""Пресет хранит ставку, стратегию и отображаемую ссылку.

Без `bid_amount` заготовка была неполна ровно в обязательном поле: стратегия
`COST_CAP` без ставки не собирается (core/campaign_builder/config.py), и после
загрузки пресета оператор всё равно вводил её руками.

Дефолты честные: существующие пресеты получают `COST_CAP` (то, с чем они и
создавались) и пустые строки там, где старый контракт ничего не хранил.
Пустая ставка при `COST_CAP` — не молчаливый ноль, а признак «в заготовке нет,
введи для запуска».
"""

from __future__ import annotations

from alembic import op

revision = "0007_preset_bid_and_link"
down_revision = "0006_campaign_preset_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.campaign_preset
            ADD COLUMN bid_strategy text NOT NULL DEFAULT 'COST_CAP',
            ADD COLUMN bid_amount text NOT NULL DEFAULT '',
            ADD COLUMN display_link text NOT NULL DEFAULT ''
        """
    )


def downgrade() -> None:
    raise RuntimeError("campaign preset bid/display-link migration is forward-only")
