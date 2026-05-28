# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/ads.

Требуется реальный Postgres v2 из docker-compose. Перед каждым тестом
очищаем тестовые offers/campaigns/ads/metrics — изолируем тесты друг от друга.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine=None, redis=None):
    """Создаём FastAPI с подменёнными engine/redis для теста."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_dashboard(pg_engine):
    """Очистка таблиц перед/после теста. Cascade от offers."""
    # Очищаем перед тестом — другой тест мог оставить мусор.
    async with pg_engine.begin() as conn:
        # ad_metrics (partitioned) — нужно явно по cycle_ts
        await conn.execute(
            text("DELETE FROM ad_metrics WHERE cycle_ts >= NOW() - INTERVAL '30 days'")
        )
        await conn.execute(text("DELETE FROM ad_alert_state"))
        await conn.execute(text("DELETE FROM meta_api_observation"))
        await conn.execute(text("DELETE FROM fb_ads"))
        await conn.execute(text("DELETE FROM fb_adsets"))
        await conn.execute(text("DELETE FROM fb_campaigns"))
        await conn.execute(text("DELETE FROM offers WHERE code LIKE 'DASH_%'"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM ad_metrics WHERE cycle_ts >= NOW() - INTERVAL '30 days'")
        )
        await conn.execute(text("DELETE FROM ad_alert_state"))
        await conn.execute(text("DELETE FROM meta_api_observation"))
        await conn.execute(text("DELETE FROM fb_ads"))
        await conn.execute(text("DELETE FROM fb_adsets"))
        await conn.execute(text("DELETE FROM fb_campaigns"))
        await conn.execute(text("DELETE FROM offers WHERE code LIKE 'DASH_%'"))


async def _seed_ad(
    conn,
    *,
    suffix: str,
    is_active: bool = True,
    alert_state: str | None = None,
    insert_metrics: bool = True,
    insert_meta_observation: bool = False,
) -> tuple[uuid.UUID, str]:
    """Создаёт offer→campaign→adset→ad. Возвращает (ad_internal_id, fb_ad_id)."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"99{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"DASH_{suffix}", "n": f"Dash offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"DASH_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"DASH_ADS_{suffix}"},
    )
    await conn.execute(
        text(
            """
            INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active)
            VALUES (:i, :a, :f, :n, :act)
            """
        ),
        {
            "i": ad_id,
            "a": adset_id,
            "f": fb_ad_id,
            "n": f"DASH_AD_{suffix}",
            "act": is_active,
        },
    )
    if alert_state:
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state (ad_id, alert_state, current_stage)
                VALUES (:a, :s, :cs)
                """
            ),
            {
                "a": ad_id,
                "s": alert_state,
                "cs": "warning"
                if alert_state == "warning_sent"
                else ("stop" if alert_state == "stop_sent" else None),
            },
        )
    if insert_metrics:
        await conn.execute(
            text(
                """
                INSERT INTO ad_metrics (
                    id, ad_id, cycle_ts, spend, leads, registrations, deposits,
                    impressions, clicks, ctr, cpc, cpm, reach, frequency,
                    cost_per_lead, cost_per_registration
                )
                VALUES (
                    gen_random_uuid(), :a, NOW() - INTERVAL '5 minutes',
                    :s, :l, :r, :d,
                    1000, 50, 0.0500, 0.2500, 12.34, 800, 1.2500,
                    2.47, 4.11
                )
                """
            ),
            {
                "a": ad_id,
                "s": Decimal("12.34"),
                "l": 5,
                "r": 3,
                "d": 1,
            },
        )
    if insert_meta_observation:
        await conn.execute(
            text(
                """
                INSERT INTO meta_api_observation (
                    ad_id, last_api_observed_at, meta_ad_status, account_id
                )
                VALUES (:a, NOW(), 'ACTIVE', 'act_1234567890')
                """
            ),
            {"a": ad_id},
        )
    return ad_id, fb_ad_id


# Пустая БД → []
@pytest.mark.asyncio
async def test_dashboard_ads_empty_db(pg_engine, fake_redis_client, clean_dashboard) -> None:
    """Пустой набор данных → возвращается пустой массив, без ошибок."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads")
    assert resp.status_code == 200
    assert resp.json() == []


# 3 ads в разных alert_state → все 3 в ответе.
@pytest.mark.asyncio
async def test_dashboard_ads_three_with_states(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """3 ad'а с разными alert_state — все попадают в ответ."""
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, suffix="A1", alert_state="normal")
        await _seed_ad(conn, suffix="A2", alert_state="warning_sent")
        await _seed_ad(conn, suffix="A3", alert_state="stop_sent")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    states = sorted(r["alert_state"] for r in data)
    assert states == ["normal", "stop_sent", "warning_sent"]


# Фильтр alert_state=stop_sent → только один.
@pytest.mark.asyncio
async def test_dashboard_ads_filter_by_alert_state(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """Фильтр по alert_state CSV — оставляет только указанные."""
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, suffix="B1", alert_state="normal")
        await _seed_ad(conn, suffix="B2", alert_state="stop_sent")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads", params={"alert_state": "stop_sent"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["alert_state"] == "stop_sent"


# Фильтр fb_ad_ids=A,B → ровно 2 элемента.
@pytest.mark.asyncio
async def test_dashboard_ads_filter_by_fb_ad_ids(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """Фильтр по fb_ad_ids CSV — возвращает только указанные id."""
    async with pg_engine.begin() as conn:
        _, fb_a = await _seed_ad(conn, suffix="C1")
        _, fb_b = await _seed_ad(conn, suffix="C2")
        await _seed_ad(conn, suffix="C3")  # не должен попасть

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads", params={"fb_ad_ids": f"{fb_a},{fb_b}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    returned_ids = {r["fb_ad_id"] for r in data}
    assert returned_ids == {fb_a, fb_b}


# include_inactive=true → возвращает is_active=false тоже.
@pytest.mark.asyncio
async def test_dashboard_ads_include_inactive(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """include_inactive=true возвращает в т.ч. отключённые ad'ы."""
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, suffix="D1", is_active=True)
        await _seed_ad(conn, suffix="D2", is_active=False)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # По умолчанию — только active
        resp_default = await ac.get("/api/dashboard/ads")
        # include_inactive=true — оба
        resp_all = await ac.get("/api/dashboard/ads", params={"include_inactive": "true"})
    assert resp_default.status_code == 200
    assert resp_all.status_code == 200
    assert len(resp_default.json()) == 1
    assert len(resp_all.json()) == 2


# limit/offset — пагинация работает.
@pytest.mark.asyncio
async def test_dashboard_ads_limit_offset(pg_engine, fake_redis_client, clean_dashboard) -> None:
    """limit и offset работают для пагинации."""
    async with pg_engine.begin() as conn:
        for i in range(5):
            await _seed_ad(conn, suffix=f"E{i}")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.get("/api/dashboard/ads", params={"limit": 2, "offset": 0})
        resp2 = await ac.get("/api/dashboard/ads", params={"limit": 2, "offset": 2})
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    page1 = resp1.json()
    page2 = resp2.json()
    assert len(page1) == 2
    assert len(page2) == 2
    # Страницы не пересекаются по fb_ad_id
    ids1 = {r["fb_ad_id"] for r in page1}
    ids2 = {r["fb_ad_id"] for r in page2}
    assert ids1.isdisjoint(ids2)


# X-Total-Count — корректное число с учётом фильтров.
@pytest.mark.asyncio
async def test_dashboard_ads_x_total_count(pg_engine, fake_redis_client, clean_dashboard) -> None:
    """X-Total-Count в headers соответствует общему числу подходящих под фильтр ad'ов."""
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, suffix="F1", alert_state="normal")
        await _seed_ad(conn, suffix="F2", alert_state="warning_sent")
        await _seed_ad(conn, suffix="F3", alert_state="warning_sent")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # limit=1 — в payload одна, но X-Total-Count учитывает фильтр.
        resp = await ac.get(
            "/api/dashboard/ads", params={"alert_state": "warning_sent", "limit": 1}
        )
    assert resp.status_code == 200
    assert resp.headers.get("X-Total-Count") == "2"
    assert len(resp.json()) == 1


# Ad без AdMetrics → metrics: None, ответ не падает.
@pytest.mark.asyncio
async def test_dashboard_ads_no_metrics_returns_null_metrics(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """Ad без записей в ad_metrics → metrics=null, не падаем."""
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, suffix="G1", insert_metrics=False)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["metrics"] is None


# Ad без AdAlertState → alert_state дефолтит в "normal".
@pytest.mark.asyncio
async def test_dashboard_ads_no_alert_state_defaults_to_normal(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """Отсутствие ad_alert_state → alert_state="normal" (LEFT JOIN + COALESCE)."""
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, suffix="H1", alert_state=None)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["alert_state"] == "normal"


# Ad с MetaApiObservation → meta_ad_status вытащен через LEFT JOIN.
@pytest.mark.asyncio
async def test_dashboard_ads_meta_observation_join(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """Если есть запись meta_api_observation — meta_ad_status в ответе."""
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, suffix="I1", insert_meta_observation=True)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["meta_ad_status"] == "ACTIVE"


# offer_code корректно вытащен через JOIN offers.
@pytest.mark.asyncio
async def test_dashboard_ads_offer_code_join(pg_engine, fake_redis_client, clean_dashboard) -> None:
    """JOIN offers подставляет offer_code в каждый snapshot."""
    async with pg_engine.begin() as conn:
        await _seed_ad(conn, suffix="J1")
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["offer_code"] == "DASH_J1"


# Sanity: 100 ads + 100 metrics → запрос укладывается в 500мс.
@pytest.mark.asyncio
async def test_dashboard_ads_performance_100_ads(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """100 ad'ов + 100 метрик — запрос должен выполниться за < 500мс."""
    async with pg_engine.begin() as conn:
        for i in range(100):
            await _seed_ad(conn, suffix=f"K{i:03d}")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        start = time.perf_counter()
        resp = await ac.get("/api/dashboard/ads", params={"limit": 200})
        elapsed_ms = (time.perf_counter() - start) * 1000

    assert resp.status_code == 200
    assert len(resp.json()) == 100
    # Sanity: должно быть быстро. На CI поднимаем потолок до 2000ms на всякий
    # случай — у локальных Postgres'ов в Docker оверхед.
    assert elapsed_ms < 2000, f"slow query: {elapsed_ms:.0f}ms"


# Неверный alert_state в фильтре → 422.
@pytest.mark.asyncio
async def test_dashboard_ads_invalid_alert_state_returns_422(
    pg_engine, fake_redis_client, clean_dashboard
) -> None:
    """Неизвестное alert_state в CSV-фильтре → 422."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/ads", params={"alert_state": "bogus_state"})
    assert resp.status_code == 422
