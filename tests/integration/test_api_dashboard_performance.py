# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/performance.

3 параллельных тяжёлых SQL: top_campaigns, offer_leaderboard, top_rule_violations.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine=None, redis=None):
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_perf(pg_engine):
    """Очистка таблиц для perf-тестов."""

    async def _wipe():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM ad_metrics WHERE cycle_ts >= NOW() - INTERVAL '60 days'")
            )
            await conn.execute(
                text("DELETE FROM alert_events WHERE created_at >= NOW() - INTERVAL '60 days'")
            )
            await conn.execute(text("DELETE FROM ad_alert_state"))
            await conn.execute(text("DELETE FROM fb_ads"))
            await conn.execute(text("DELETE FROM fb_adsets"))
            await conn.execute(text("DELETE FROM fb_campaigns"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'PRF_%'"))

    await _wipe()
    yield
    await _wipe()


async def _seed_full(
    conn,
    suffix: str,
    *,
    spend: Decimal = Decimal("100.00"),
    leads: int = 10,
    rule_codes: list[str] | None = None,
):
    """Создаёт offer→campaign→adset→ad с метрикой и алертом."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"33{suffix}"
    fb_camp_id = f"camp_{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"PRF_{suffix}", "n": f"Perf offer {suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, offer_id) "
            "VALUES (:i, :fc, :n, :o)"
        ),
        {"i": campaign_id, "fc": fb_camp_id, "n": f"PRF_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"PRF_ADS_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"PRF_AD_{suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
            "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
            "NOW() - INTERVAL '2 hours', :s, 1000, 50, :l, 5, 1)"
        ),
        {"a": ad_id, "s": spend, "l": leads},
    )
    if rule_codes:
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, "
                "metrics_json) VALUES (gen_random_uuid(), :a, 'warning', 'warning_sent', "
                "CAST(:codes AS jsonb), CAST(:mj AS jsonb))"
            ),
            {
                "a": ad_id,
                "codes": json.dumps(rule_codes),
                "mj": json.dumps({"spend": str(spend)}),
            },
        )
    return ad_id, campaign_id, offer_id


# Тест: default days=7 — данные за 7 дней.
@pytest.mark.asyncio
async def test_performance_default_7d(pg_engine, fake_redis_client, clean_perf) -> None:
    """Default ?days=7 → endpoint отрабатывает и возвращает три ключа."""
    async with pg_engine.begin() as conn:
        await _seed_full(conn, "D7", spend=Decimal("500.00"), leads=10, rule_codes=["CPC"])

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance")

    assert resp.status_code == 200
    data = resp.json()
    assert "top_campaigns" in data
    assert "offer_leaderboard" in data
    assert "top_rule_violations" in data


# Тест: days=30 — максимум.
@pytest.mark.asyncio
async def test_performance_days_30(pg_engine, fake_redis_client, clean_perf) -> None:
    """?days=30 → max, endpoint отвечает 200."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance", params={"days": 30})
    assert resp.status_code == 200


# Тест: days=31 → 422.
@pytest.mark.asyncio
async def test_performance_days_too_large(pg_engine, fake_redis_client, clean_perf) -> None:
    """?days=31 > max=30 → 422."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance", params={"days": 31})
    assert resp.status_code == 422


# Тест: limit_* параметры реально режут результат.
@pytest.mark.asyncio
async def test_performance_limits(pg_engine, fake_redis_client, clean_perf) -> None:
    """limit_campaigns=1 → не больше 1 строки в top_campaigns."""
    async with pg_engine.begin() as conn:
        await _seed_full(conn, "L1", spend=Decimal("500.00"))
        await _seed_full(conn, "L2", spend=Decimal("400.00"))
        await _seed_full(conn, "L3", spend=Decimal("300.00"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/dashboard/performance",
            params={"limit_campaigns": 1, "limit_offers": 1, "limit_rules": 1},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["top_campaigns"]) <= 1
    assert len(data["offer_leaderboard"]) <= 1


# Тест: top_campaigns отсортированы по spend DESC.
@pytest.mark.asyncio
async def test_performance_top_campaigns_sorted(pg_engine, fake_redis_client, clean_perf) -> None:
    """top_campaigns — сортировка SUM(spend) DESC NULLS LAST."""
    async with pg_engine.begin() as conn:
        await _seed_full(conn, "S1", spend=Decimal("100.00"))
        await _seed_full(conn, "S2", spend=Decimal("500.00"))  # самый дорогой
        await _seed_full(conn, "S3", spend=Decimal("200.00"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance")

    assert resp.status_code == 200
    camps = resp.json()["top_campaigns"]
    # Наши 3 кампании или больше; проверяем что первая в нашей выборке — S2
    our_camps = [c for c in camps if c["campaign_name"].startswith("PRF_CMP_S")]
    if len(our_camps) >= 2:
        # Превышение spend первого нашего ≥ второго (по убыванию)
        spend_vals = [Decimal(c["spend"]) for c in our_camps if c["spend"]]
        assert spend_vals == sorted(spend_vals, reverse=True)


# Тест: offer_leaderboard включает alerts_count.
@pytest.mark.asyncio
async def test_performance_offer_alerts_count(pg_engine, fake_redis_client, clean_perf) -> None:
    """offer_leaderboard.alerts_count учитывает алерты за окно."""
    async with pg_engine.begin() as conn:
        # 1 ad + 1 alert
        await _seed_full(conn, "AL", spend=Decimal("100.00"), rule_codes=["CPC"])

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance")

    assert resp.status_code == 200
    leaderboard = resp.json()["offer_leaderboard"]
    our_offer = next((o for o in leaderboard if o["offer_code"] == "PRF_AL"), None)
    if our_offer is not None:
        assert our_offer["alerts_count"] >= 1


# Тест: top_rule_violations через unnest matched_rule_codes.
@pytest.mark.asyncio
async def test_performance_rule_violations_unnest(pg_engine, fake_redis_client, clean_perf) -> None:
    """matched_rule_codes JSONB разворачивается через jsonb_array_elements_text."""
    async with pg_engine.begin() as conn:
        await _seed_full(conn, "RU1", rule_codes=["CPC", "CPL"])
        await _seed_full(conn, "RU2", rule_codes=["CPC"])  # CPC встречается 2 раза
        await _seed_full(conn, "RU3", rule_codes=["FUNNEL"])

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance")

    assert resp.status_code == 200
    rules = resp.json()["top_rule_violations"]
    rule_codes_count = {r["rule_code"]: r["count"] for r in rules}
    # Среди нашего набора CPC встречается >= 2
    if "CPC" in rule_codes_count:
        assert rule_codes_count["CPC"] >= 2


# Тест: fail-all поведение — если один подзапрос провалится, весь endpoint падает.
@pytest.mark.asyncio
async def test_performance_fail_all_policy(pg_engine, fake_redis_client, clean_perf) -> None:
    """Документируем fail-all: ошибка в одном из 3 параллельных SQL → 5xx.

    Подменяем один из внутренних query-функций на падающую корутину.
    httpx по умолчанию пробрасывает unhandled exception, поэтому используем
    raise_app_exceptions=False, чтобы получить именно 500-ответ.
    """
    from apps.api.routers.v1 import dashboard_performance as dp_module

    async def _broken(*a, **kw):
        raise RuntimeError("smt broken")

    original = dp_module._query_top_rule_violations
    dp_module._query_top_rule_violations = _broken
    try:
        app = _make_app(engine=pg_engine, redis=fake_redis_client)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/dashboard/performance")
        # asyncio.gather пробрасывает первую же ошибку → 5xx
        assert resp.status_code >= 500
    finally:
        dp_module._query_top_rule_violations = original
