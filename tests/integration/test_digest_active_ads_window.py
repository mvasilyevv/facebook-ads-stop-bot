# -*- coding: utf-8 -*-
"""Integration: MID #20 — active_ads с фильтром last_seen_at.

Старые ads (last_seen_at < 7 дней назад) не должны попадать в counter,
даже если is_active=TRUE. Иначе показатель «активных объявлений» рос бы вечно.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.digest_builder import _count_active_ads_normal


@pytest_asyncio.fixture
async def clean_catalog(pg_engine: AsyncEngine):
    """Чистим offers/campaigns/adsets/ads перед и после теста.

    Этот тест работает на пустом catalog'е (создаёт свои строки),
    поэтому полная очистка безопасна.
    """

    async def _truncate():
        async with pg_engine.begin() as conn:
            # Очерёдность: ad_alert_state (FK на ads) → ads → adsets → campaigns → offers.
            await conn.execute(text("DELETE FROM ad_alert_state"))
            await conn.execute(text("DELETE FROM fb_ads"))
            await conn.execute(text("DELETE FROM fb_adsets"))
            await conn.execute(text("DELETE FROM fb_campaigns"))
            await conn.execute(text("DELETE FROM offers"))

    await _truncate()
    yield
    await _truncate()


async def _seed_ads(engine: AsyncEngine, *, fresh: int, stale: int) -> None:
    """Создаёт offer→campaign→adset + N свежих + M старых ads.

    fresh — last_seen_at = now() (попадает в окно).
    stale — last_seen_at = now() - 10 days (за пределами 7-дневного окна).
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    stale_ts = now - timedelta(days=10)

    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": "TST_ACT", "n": "Test active"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": "CMP_ACT", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": "ADSET_ACT"},
        )
        for idx in range(fresh):
            await conn.execute(
                text(
                    "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active, "
                    "first_seen_at, last_seen_at) "
                    "VALUES (:i, :a, :f, :n, TRUE, :ts, :ts)"
                ),
                {
                    "i": uuid.uuid4(),
                    "a": adset_id,
                    "f": f"FRESH_{idx}",
                    "n": f"AD_F_{idx}",
                    "ts": now,
                },
            )
        for idx in range(stale):
            await conn.execute(
                text(
                    "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active, "
                    "first_seen_at, last_seen_at) "
                    "VALUES (:i, :a, :f, :n, TRUE, :first, :stale)"
                ),
                {
                    "i": uuid.uuid4(),
                    "a": adset_id,
                    "f": f"STALE_{idx}",
                    "n": f"AD_S_{idx}",
                    "first": stale_ts,
                    "stale": stale_ts,
                },
            )


# 10 свежих + 5 старых ads → counter = 10 (старые отсечены last_seen filter'ом).
@pytest.mark.asyncio
async def test_count_active_ads_excludes_stale(
    pg_engine: AsyncEngine,
    clean_catalog,
) -> None:
    await _seed_ads(pg_engine, fresh=10, stale=5)
    count = await _count_active_ads_normal(pg_engine)
    assert count == 10


# 0 свежих + N старых → counter = 0.
@pytest.mark.asyncio
async def test_count_active_ads_zero_when_only_stale(
    pg_engine: AsyncEngine,
    clean_catalog,
) -> None:
    await _seed_ads(pg_engine, fresh=0, stale=8)
    count = await _count_active_ads_normal(pg_engine)
    assert count == 0
