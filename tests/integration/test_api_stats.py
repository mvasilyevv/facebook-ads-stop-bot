# -*- coding: utf-8 -*-
"""Семантические тесты /api/stats/* (страница «Статистика залива»).

Класс CRIT-1: ad_metrics кумулятивны — endpoint'ы обязаны брать latest-снимки,
а не суммировать все строки. Проверяем фактические ответы на кумулятивном
наборе. Изоляция в shared-БД: точные значения — через breakdown по СВОЕМУ
офферу; глобальные серии — через инварианты (телескопирование дельт:
sum(series) == totals) и вклады «не меньше наших».

Запуск ТОЛЬКО на изолированной БД (правило Never pytest on live DB).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app

_SUFFIX = "STATS"


def _make_app(*, engine, redis):
    """FastAPI с подменой engine/redis (как в остальных integration-тестах)."""
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_redis] = lambda: redis
    app.state.redis = redis
    return app


async def _seed_chain(conn) -> dict:
    """offer→campaign→adset→2 ads с префиксом STATS_ (см. clean_stats)."""
    ids = {
        "offer_id": uuid.uuid4(),
        "campaign_id": uuid.uuid4(),
        "adset_id": uuid.uuid4(),
        "ad1_id": uuid.uuid4(),
        "ad2_id": uuid.uuid4(),
    }
    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": ids["offer_id"], "c": f"{_SUFFIX}_OF", "n": f"{_SUFFIX} offer"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": ids["campaign_id"], "n": f"{_SUFFIX}_CMP", "o": ids["offer_id"]},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": ids["adset_id"], "c": ids["campaign_id"], "n": f"{_SUFFIX}_ADS"},
    )
    for key, fb in (("ad1_id", f"{_SUFFIX.lower()}1"), ("ad2_id", f"{_SUFFIX.lower()}2")):
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                "VALUES (:i, :a, :f, :n, NOW())"
            ),
            {"i": ids[key], "a": ids["adset_id"], "f": fb, "n": f"{_SUFFIX}_AD_{fb}"},
        )
    return ids


async def _insert_metric(conn, *, ad_id, cycle_ts_sql: str, spend: Decimal, leads: int):
    """Кумулятивный snapshot с явным cycle_ts (SQL-выражение) + клики=лиды×2."""
    await conn.execute(
        text(
            "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, clicks, leads, deposits) "
            f"VALUES (gen_random_uuid(), :a, {cycle_ts_sql}, :s, :c, :l, 0)"
        ),
        {"a": ad_id, "s": spend, "c": leads * 2, "l": leads},
    )


@pytest_asyncio.fixture
async def clean_stats(pg_engine):
    """Чистит STATS_-цепочку до и после теста (порядок FK, как clean_semantics)."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM tracker_aggregate WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'STATS\\_AD\\_%')"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'STATS\\_AD\\_%')"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE 'STATS\\_AD\\_%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name = 'STATS_ADS'"))
            await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name = 'STATS_CMP'"))
            await conn.execute(text("DELETE FROM offers WHERE code = 'STATS_OF'"))

    await _cleanup()
    yield
    await _cleanup()


# /stats/today?breakdown=offer: 5 кумулятивных циклов на 2 ада (50 и 25 последние)
# → строка нашего оффера ровно spend=75/leads=75, НЕ 375 (сумма снимков).
@pytest.mark.asyncio
async def test_stats_today_breakdown_not_inflated(pg_engine, fake_redis_client, clean_stats):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn)
        for n, (s1, s2) in enumerate([(10, 5), (20, 10), (30, 15), (40, 20), (50, 25)]):
            ts = f"date_trunc('hour', NOW()) + INTERVAL '5 minutes' + INTERVAL '{n} minutes'"
            await _insert_metric(
                conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal(s1), leads=s1
            )
            await _insert_metric(
                conn, ad_id=ids["ad2_id"], cycle_ts_sql=ts, spend=Decimal(s2), leads=s2
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stats/today", params={"breakdown": "offer"})

    assert resp.status_code == 200
    body = resp.json()
    row = next(r for r in body["breakdown"] if r["key"] == "STATS_OF")
    assert Decimal(row["spend"]) == Decimal("75")
    assert row["leads"] == 75
    assert row["clicks"] == 150
    # CPL строки = 75/75 = 1.00 (производная считается на бэке)
    assert Decimal(row["cpl"]) == Decimal("1.00")


# Инвариант телескопирования: сумма почасовых ДЕЛЬТ == тоталам за окно
# (обе стороны считаются из одного набора latest-снимков).
@pytest.mark.asyncio
async def test_stats_today_series_telescopes_to_totals(pg_engine, fake_redis_client, clean_stats):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn)
        for n, spend in enumerate([Decimal("7"), Decimal("19")]):
            ts = f"date_trunc('hour', NOW()) + INTERVAL '{5 + n} minutes'"
            await _insert_metric(conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=spend, leads=n)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stats/today")

    assert resp.status_code == 200
    meta = resp.json()["meta"]
    series_sum = sum(Decimal(p["spend"] or "0") for p in meta["series_hourly"])
    assert series_sum == Decimal(meta["totals"]["spend"])


# /stats/period: подневная серия складывает ДНЕВНЫЕ итоги (latest per день),
# и сумма серии == тоталам периода (тот же CTE-набор).
@pytest.mark.asyncio
async def test_stats_period_daily_series_and_totals(pg_engine, fake_redis_client, clean_stats):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn)
        # Вчера: кумулятив 10→40 (дневной итог 40). Сегодня: 5→30 (итог 30).
        for n, spend in enumerate([Decimal("10"), Decimal("40")]):
            ts = f"date_trunc('day', NOW()) - INTERVAL '1 day' + INTERVAL '{10 + n} minutes'"
            await _insert_metric(conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=spend, leads=n)
        for n, spend in enumerate([Decimal("5"), Decimal("30")]):
            ts = f"date_trunc('day', NOW()) + INTERVAL '{10 + n} minutes'"
            await _insert_metric(conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=spend, leads=n)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    from_iso = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stats/period", params={"from_iso": from_iso})

    assert resp.status_code == 200
    meta = resp.json()["meta"]
    series_sum = sum(Decimal(p["spend"] or "0") for p in meta["series_daily"])
    assert series_sum == Decimal(meta["totals"]["spend"])
    # Наш вклад — дневные итоги 40 и 30, не 50 (10+40) и не 35 (5+30):
    # глобальные серии могут содержать чужие данные, поэтому проверяем «не меньше».
    days = {p["day"]: Decimal(p["spend"] or "0") for p in meta["series_daily"]}
    today = datetime.now(UTC).date().isoformat()
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    assert days.get(yesterday, Decimal("0")) >= Decimal("40")
    assert days.get(today, Decimal("0")) >= Decimal("30")


# Блок трекера: строки tracker_aggregate за сегодня попадают в totals (не кумулятив,
# простой SUM), available=true, ROI-строка присутствует при spend>0.
@pytest.mark.asyncio
async def test_stats_today_tracker_block(pg_engine, fake_redis_client, clean_stats):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn)
        ts = "date_trunc('hour', NOW()) + INTERVAL '5 minutes'"
        await _insert_metric(
            conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal("10"), leads=2
        )
        await conn.execute(
            text(
                "INSERT INTO tracker_aggregate "
                "(id, ad_id, country, day, installs, registrations, deposits, revenue, last_postback_at) "
                "VALUES (gen_random_uuid(), :a, 'BD', CURRENT_DATE, 3, 2, 1, 25.00, NOW())"
            ),
            {"a": ids["ad1_id"]},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stats/today")

    assert resp.status_code == 200
    tracker = resp.json()["tracker"]
    assert tracker["available"] is True
    assert tracker["totals"]["deposits"] >= 1
    assert tracker["totals"]["registrations"] >= 2
    assert Decimal(tracker["totals"]["revenue"]) >= Decimal("25.00")
    assert tracker["attribution_note"]


# Пустое окно (будущее) → нули в totals, производные None, серии пустые — без 500.
@pytest.mark.asyncio
async def test_stats_period_empty_window(pg_engine, fake_redis_client, clean_stats):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    from_iso = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    to_iso = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stats/period", params={"from_iso": from_iso, "to_iso": to_iso})

    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert Decimal(meta["totals"]["spend"]) == Decimal("0")
    assert meta["totals"]["leads"] == 0
    assert all(v is None for v in meta["derived"].values())
    assert meta["series_daily"] == []


# Окно >90 дней → 422 (валидация как в history)
@pytest.mark.asyncio
async def test_stats_period_range_validation(pg_engine, fake_redis_client):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    from_iso = (datetime.now(UTC) - timedelta(days=120)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/stats/period", params={"from_iso": from_iso})
    assert resp.status_code == 422
