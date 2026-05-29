# -*- coding: utf-8 -*-
"""Integration: data-driven порог частоты по реальной ad_metrics (#37).

Засеваем offer→campaign→adset→ad + ad_metrics с паттерном «дёшево на низкой частоте,
дорого на высокой» и проверяем analyze_offer_frequency + apply_recommended_threshold
(dry_run, запись только в NULL, защита ручного значения). Требует Postgres (pg_engine).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.rules.frequency_analyzer import (
    analyze_offer_frequency,
    apply_recommended_threshold,
)


@pytest_asyncio.fixture
async def offer_with_metrics(pg_engine: AsyncEngine):
    """offer(+offer_rules NULL порог)→campaign→adset→ad + ad_metrics с деградацией.

    Возвращает offer_id (str). Teardown — явные DELETE в порядке FK.
    """
    suffix = uuid.uuid4().hex[:8]
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"FRQ_{suffix}", "n": f"freq analyzer {suffix}"},
        )
        await conn.execute(
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:o, :cpa)"),
            {"o": offer_id, "cpa": Decimal("5")},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": f"2390{suffix}", "n": f"AD_{suffix}"},
        )

        # Паттерн: baseline (freq 1.5, cost 10) ×20, mid (2.2, 11) ×10, high (3.2, 15) ×10.
        # high деградировал на 50% от baseline 10 → порог 3.0. Уникальные cycle_ts.
        plan = [
            (Decimal("1.5"), Decimal("10"), 20),
            (Decimal("2.2"), Decimal("11"), 10),
            (Decimal("3.2"), Decimal("15"), 10),
        ]
        minute = 0
        for freq, cost, count in plan:
            for _ in range(count):
                minute += 1
                await conn.execute(
                    text(
                        """
                        INSERT INTO ad_metrics
                            (ad_id, cycle_ts, spend, frequency, cost_per_result)
                        VALUES
                            (:aid, NOW() - make_interval(mins => :m), 1, :f, :c)
                        """
                    ),
                    {"aid": ad_id, "m": minute, "f": freq, "c": cost},
                )

    yield str(offer_id)

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM ad_metrics WHERE ad_id = :a"), {"a": ad_id})
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


async def _read_threshold(pg_engine: AsyncEngine, offer_id: str) -> Decimal | None:
    async with pg_engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT frequency_threshold FROM offer_rules WHERE offer_id = :o"),
                {"o": offer_id},
            )
        ).scalar()


# analyze по реальным метрикам находит порог деградации (≈3.0)
@pytest.mark.asyncio
async def test_analyze_returns_threshold(pg_engine, offer_with_metrics) -> None:
    res = await analyze_offer_frequency(pg_engine, offer_id=offer_with_metrics, days=14)
    assert res.threshold == Decimal("3.00")
    assert res.total_samples == 40


# dry_run=True (дефолт): порог посчитан, но в БД НЕ записан
@pytest.mark.asyncio
async def test_apply_dry_run_does_not_write(pg_engine, offer_with_metrics) -> None:
    res, applied = await apply_recommended_threshold(
        pg_engine, offer_id=offer_with_metrics, dry_run=True
    )
    assert res.threshold == Decimal("3.00")
    assert applied is False
    assert await _read_threshold(pg_engine, offer_with_metrics) is None


# dry_run=False + порог был NULL → записываем
@pytest.mark.asyncio
async def test_apply_writes_when_null(pg_engine, offer_with_metrics) -> None:
    res, applied = await apply_recommended_threshold(
        pg_engine, offer_id=offer_with_metrics, dry_run=False
    )
    assert applied is True
    assert await _read_threshold(pg_engine, offer_with_metrics) == Decimal("3.00")


# Ручной порог НЕ затирается авто-расчётом (money-защита)
@pytest.mark.asyncio
async def test_apply_does_not_overwrite_manual(pg_engine, offer_with_metrics) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE offer_rules SET frequency_threshold = :v WHERE offer_id = :o"),
            {"v": Decimal("5.0"), "o": offer_with_metrics},
        )
    res, applied = await apply_recommended_threshold(
        pg_engine, offer_id=offer_with_metrics, dry_run=False
    )
    assert applied is False  # не трогаем заданное вручную
    assert await _read_threshold(pg_engine, offer_with_metrics) == Decimal("5.0")
