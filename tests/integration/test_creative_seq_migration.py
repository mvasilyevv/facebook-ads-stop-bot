# -*- coding: utf-8 -*-
"""Проверяет, что миграция 0028 создала таблицы offer_creative_seq и campaign_creative."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def test_tables_exist(pg_engine: AsyncEngine) -> None:
    """Таблицы offer_creative_seq и campaign_creative должны существовать после миграции."""
    async with pg_engine.connect() as conn:
        for t in ("offer_creative_seq", "campaign_creative"):
            ok = (await conn.execute(text("SELECT to_regclass(:t)"), {"t": t})).scalar()
            assert ok is not None, f"таблица {t} не создана"
