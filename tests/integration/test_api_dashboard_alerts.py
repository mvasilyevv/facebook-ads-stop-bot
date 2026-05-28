# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/alerts.

Партиционная таблица alert_events — обязательный WHERE по created_at.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
async def clean_alerts(pg_engine):
    """Очистка alert_events и связанных таблиц per тест."""
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM alert_events WHERE created_at >= NOW() - INTERVAL '30 days'")
        )
        await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '88%'"))
        await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'ALR_%'"))
        await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'ALR_%'"))
        await conn.execute(text("DELETE FROM offers WHERE code LIKE 'ALR_%'"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM alert_events WHERE created_at >= NOW() - INTERVAL '30 days'")
        )
        await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '88%'"))
        await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'ALR_%'"))
        await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'ALR_%'"))
        await conn.execute(text("DELETE FROM offers WHERE code LIKE 'ALR_%'"))


async def _seed_ad_with_alert(
    conn,
    *,
    suffix: str,
    stage: str = "warning",
    matched: list[str] | None = None,
    created_at: datetime | None = None,
) -> tuple[uuid.UUID, str]:
    """Создаёт offer→campaign→adset→ad + одну запись в alert_events."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"88{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"ALR_{suffix}", "n": f"Alr offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"ALR_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"ALR_ADS_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"ALR_AD_{suffix}"},
    )

    matched_codes = matched or ["CPC"]
    state = "warning_sent" if stage == "warning" else "stop_sent"
    ts = created_at or datetime.now(UTC)
    import json as _json

    await conn.execute(
        text(
            """
            INSERT INTO alert_events (
                id, ad_id, stage, state, matched_rule_codes,
                metrics_json, created_at
            )
            VALUES (
                gen_random_uuid(), :a, :st, :state,
                CAST(:codes AS jsonb), CAST(:mj AS jsonb), :ts
            )
            """
        ),
        {
            "a": ad_id,
            "st": stage,
            "state": state,
            "codes": _json.dumps(matched_codes),
            "mj": _json.dumps({"spend": "10.00"}),
            "ts": ts,
        },
    )
    return ad_id, fb_ad_id


# Пустая БД → []
@pytest.mark.asyncio
async def test_dashboard_alerts_empty(pg_engine, fake_redis_client, clean_alerts) -> None:
    """Нет alert_events за окно — пустой ответ."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/alerts")
    assert resp.status_code == 200
    assert resp.json() == []


# Default last 24h окно — события в окне попадают, вне окна — нет.
@pytest.mark.asyncio
async def test_dashboard_alerts_default_24h_window(
    pg_engine, fake_redis_client, clean_alerts
) -> None:
    """Дефолтное окно — last 24h. События старше 24h не попадают."""
    async with pg_engine.begin() as conn:
        now = datetime.now(UTC)
        await _seed_ad_with_alert(
            conn, suffix="W1", stage="warning", created_at=now - timedelta(hours=1)
        )
        await _seed_ad_with_alert(
            conn, suffix="W2", stage="warning", created_at=now - timedelta(hours=48)
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/alerts")
    assert resp.status_code == 200
    data = resp.json()
    # Только событие из последнего часа
    assert len(data) == 1


# Фильтр stage=stop → только stop.
@pytest.mark.asyncio
async def test_dashboard_alerts_filter_stage_stop(
    pg_engine, fake_redis_client, clean_alerts
) -> None:
    """stage=stop возвращает только stop-события."""
    async with pg_engine.begin() as conn:
        await _seed_ad_with_alert(conn, suffix="S1", stage="warning")
        await _seed_ad_with_alert(conn, suffix="S2", stage="stop")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/alerts", params={"stage": "stop"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["stage"] == "stop"


# Фильтр fb_ad_id → только этот ad.
@pytest.mark.asyncio
async def test_dashboard_alerts_filter_fb_ad_id(pg_engine, fake_redis_client, clean_alerts) -> None:
    """Фильтр по конкретному fb_ad_id — только события этого объявления."""
    async with pg_engine.begin() as conn:
        _, fb_a = await _seed_ad_with_alert(conn, suffix="P1")
        await _seed_ad_with_alert(conn, suffix="P2")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/alerts", params={"fb_ad_id": fb_a})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["fb_ad_id"] == fb_a


# Имена полей: stage, matched_rule_codes, ad_name через JOIN — должны быть.
@pytest.mark.asyncio
async def test_dashboard_alerts_field_names(pg_engine, fake_redis_client, clean_alerts) -> None:
    """Поля ответа: stage, matched_rule_codes, ad_name (через JOIN fb_ads) — корректные."""
    async with pg_engine.begin() as conn:
        await _seed_ad_with_alert(conn, suffix="N1", stage="warning", matched=["CPC", "CPL"])

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    ev = data[0]
    assert ev["stage"] == "warning"
    assert ev["matched_rule_codes"] == ["CPC", "CPL"]
    assert ev["ad_name"].startswith("ALR_AD_")
    # Старые имена НЕ должны присутствовать
    assert "event_type" not in ev
    assert "rule_codes" not in ev
    assert "name" not in ev or ev.get("name") is None


# limit ограничивает количество.
@pytest.mark.asyncio
async def test_dashboard_alerts_limit(pg_engine, fake_redis_client, clean_alerts) -> None:
    """limit=2 → не более 2 событий."""
    async with pg_engine.begin() as conn:
        for i in range(5):
            await _seed_ad_with_alert(conn, suffix=f"L{i}")
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/alerts", params={"limit": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


# from_iso > to_iso → 422.
@pytest.mark.asyncio
async def test_dashboard_alerts_from_after_to_returns_422(
    pg_engine, fake_redis_client, clean_alerts
) -> None:
    """from_iso позже to_iso → 422 Validation Error."""
    now = datetime.now(UTC)
    to_iso = (now - timedelta(hours=2)).isoformat()
    from_iso = now.isoformat()
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/dashboard/alerts", params={"from_iso": from_iso, "to_iso": to_iso}
        )
    assert resp.status_code == 422


# Партиционный запрос с from_iso=now-30d работает.
@pytest.mark.asyncio
async def test_dashboard_alerts_partitioned_query_30d(
    pg_engine, fake_redis_client, clean_alerts
) -> None:
    """Запрос с from_iso=now()-30d не падает и применяется к партициям."""
    async with pg_engine.begin() as conn:
        now = datetime.now(UTC)
        await _seed_ad_with_alert(
            conn, suffix="Q1", stage="warning", created_at=now - timedelta(days=15)
        )

    from_iso = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    to_iso = datetime.now(UTC).isoformat()
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/dashboard/alerts", params={"from_iso": from_iso, "to_iso": to_iso}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
