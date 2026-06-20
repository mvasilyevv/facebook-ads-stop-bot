# -*- coding: utf-8 -*-
"""Дайджест-total суммирует спенд per-ad-per-day (не теряет день до cabinet-полуночи).

CRIT-1: окно дайджеста 09:00-09:00 UTC пересекает cabinet-полночь.
spend в ad_metrics СБРАСЫВАЕТСЯ при cabinet-полночи (не UTC midnight).
Наивный DISTINCT ON (ad_id) берёт ПОСЛЕДНИЙ snapshot за окно → теряет день N-1.
Правильный подход: DISTINCT ON (ad_id, day) → SUM (дневные итоги через reset).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.digest_builder import _top_ads_and_total_spend


@pytest_asyncio.fixture
async def _seed_two_days(pg_engine):
    """Один ad, 2 snapshot в разных UTC-днях: вчера 23:00 spend=80 + сегодня 01:00 spend=30.

    Эмулирует cabinet-полночь: spend сбросился в UTC-полночь → новый кумулятив с нуля.
    Наивный latest-per-ad вернёт 30 (последний snapshot). Правильный per-day вернёт 80+30=110.
    """
    ad_id = uuid.uuid4()
    # «Сейчас» = 02:00 UTC → вчера 23:00 и сегодня 01:00 попадают в разные UTC-дни
    now = datetime.now(timezone.utc).replace(hour=2, minute=0, second=0, microsecond=0)

    async with pg_engine.begin() as conn:
        # Чистим таблицы в правильном порядке (FK)
        for t in ("ad_metrics", "fb_ads", "fb_adsets", "fb_campaigns"):
            await conn.execute(text(f"DELETE FROM {t}"))

        cid = uuid.uuid4()
        sid = uuid.uuid4()

        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, campaign_name, last_seen_at)"
                " VALUES (:i, 'CR2|KE', NOW())"
            ),
            {"i": cid},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, campaign_id, adset_name, last_seen_at)"
                " VALUES (:i, :c, 'EQ', NOW())"
            ),
            {"i": sid, "c": cid},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at)"
                " VALUES (:i, :s, '900test', 'Ad', NOW())"
            ),
            {"i": ad_id, "s": sid},
        )

        # День N-1 (вчера 23:00): кумулятив дня N-1 = 80
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)"
                " VALUES (gen_random_uuid(), :a, :ts, 80)"
            ),
            {"a": ad_id, "ts": now - timedelta(hours=3)},
        )
        # День N (сегодня 01:00): кумулятив после cabinet-сброса = 30
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend)"
                " VALUES (gen_random_uuid(), :a, :ts, 30)"
            ),
            {"a": ad_id, "ts": now - timedelta(hours=1)},
        )

    return {"window_start": now - timedelta(hours=24), "window_end": now}


# Дайджест-total должен суммировать per-day итоги: 80 (день N-1) + 30 (день N) = 110.
# Наивный latest-per-ad вернёт только 30 (последний snapshot) → CRIT-1 money-bug.
@pytest.mark.asyncio
async def test_total_spend_sums_per_day(pg_engine, _seed_two_days):
    """per-day CTE: total = 80+30 = 110, а не 30 (наивный latest)."""
    _top, total = await _top_ads_and_total_spend(
        pg_engine,
        window_start=_seed_two_days["window_start"],
        window_end=_seed_two_days["window_end"],
        limit=5,
    )
    assert total == Decimal("110"), f"ожидалось 110 (80+30 per-day), получено {total}"
