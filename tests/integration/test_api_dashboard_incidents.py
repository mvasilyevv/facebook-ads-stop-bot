# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/incidents.

Инцидент = ad_alert_state.alert_state IN ('warning_sent', 'stop_sent')
И НЕ snoozed (snoozed_until IS NULL OR snoozed_until < NOW()).
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
async def clean_incidents(pg_engine):
    """Очистка ad_alert_state и связанных таблиц."""
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM alert_events WHERE created_at >= NOW() - INTERVAL '30 days'")
        )
        await conn.execute(text("DELETE FROM ad_alert_state"))
        await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '77%'"))
        await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'INC_%'"))
        await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'INC_%'"))
        await conn.execute(text("DELETE FROM offers WHERE code LIKE 'INC_%'"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM alert_events WHERE created_at >= NOW() - INTERVAL '30 days'")
        )
        await conn.execute(text("DELETE FROM ad_alert_state"))
        await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '77%'"))
        await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'INC_%'"))
        await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'INC_%'"))
        await conn.execute(text("DELETE FROM offers WHERE code LIKE 'INC_%'"))


async def _seed_incident(
    conn,
    *,
    suffix: str,
    alert_state: str,
    snoozed_until: datetime | None = None,
    last_transition_at: datetime | None = None,
    add_alert_events: int = 0,
) -> tuple[uuid.UUID, str]:
    """Создаёт ad + alert_state + (опционально) N alert_events после last_transition_at."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"77{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"INC_{suffix}", "n": f"Inc offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"INC_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"INC_ADS_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"INC_AD_{suffix}"},
    )

    stage = (
        "warning"
        if alert_state == "warning_sent"
        else ("stop" if alert_state == "stop_sent" else None)
    )
    transition_ts = last_transition_at or datetime.now(UTC)
    await conn.execute(
        text(
            """
            INSERT INTO ad_alert_state (
                ad_id, alert_state, current_stage, snoozed_until, last_transition_at
            )
            VALUES (:a, :s, :cs, :sn, :lt)
            """
        ),
        {
            "a": ad_id,
            "s": alert_state,
            "cs": stage,
            "sn": snoozed_until,
            "lt": transition_ts,
        },
    )

    if add_alert_events:
        import json as _json

        for i in range(add_alert_events):
            ts = transition_ts + timedelta(seconds=i * 10)
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
                    "st": stage or "warning",
                    "state": alert_state,
                    "codes": _json.dumps(["CPC"]),
                    "mj": _json.dumps({"i": i}),
                    "ts": ts,
                },
            )
    return ad_id, fb_ad_id


# Только warning_sent/stop_sent попадают.
@pytest.mark.asyncio
async def test_incidents_only_active_states(pg_engine, fake_redis_client, clean_incidents) -> None:
    """В ответе только ad'ы с alert_state IN ('warning_sent','stop_sent')."""
    async with pg_engine.begin() as conn:
        await _seed_incident(conn, suffix="A1", alert_state="warning_sent")
        await _seed_incident(conn, suffix="A2", alert_state="stop_sent")
        await _seed_incident(conn, suffix="A3", alert_state="normal")
        await _seed_incident(conn, suffix="A4", alert_state="claimed")
        await _seed_incident(conn, suffix="A5", alert_state="disabled")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/incidents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    states = sorted(r["alert_state"] for r in data)
    assert states == ["stop_sent", "warning_sent"]


# stage=warning → только warning_sent.
@pytest.mark.asyncio
async def test_incidents_stage_warning(pg_engine, fake_redis_client, clean_incidents) -> None:
    """stage=warning сужает выборку до warning_sent."""
    async with pg_engine.begin() as conn:
        await _seed_incident(conn, suffix="B1", alert_state="warning_sent")
        await _seed_incident(conn, suffix="B2", alert_state="stop_sent")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/incidents", params={"stage": "warning"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["alert_state"] == "warning_sent"


# Snoozed (snoozed_until > NOW()) исключаются.
@pytest.mark.asyncio
async def test_incidents_snoozed_excluded(pg_engine, fake_redis_client, clean_incidents) -> None:
    """Если snoozed_until в будущем — инцидент не возвращается."""
    async with pg_engine.begin() as conn:
        future = datetime.now(UTC) + timedelta(hours=2)
        past = datetime.now(UTC) - timedelta(hours=1)
        # активный (snoozed в прошлом — фактически НЕ snoozed)
        await _seed_incident(conn, suffix="C1", alert_state="warning_sent", snoozed_until=past)
        # snoozed в будущем — исключаем
        await _seed_incident(conn, suffix="C2", alert_state="warning_sent", snoozed_until=future)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/incidents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    # Возвращается только не-snoozed
    assert data[0]["fb_ad_id"] == "77C1"


# incident_duration_seconds корректный (примерно).
@pytest.mark.asyncio
async def test_incidents_duration_seconds(pg_engine, fake_redis_client, clean_incidents) -> None:
    """incident_duration_seconds ≈ NOW - last_transition_at."""
    async with pg_engine.begin() as conn:
        ten_min_ago = datetime.now(UTC) - timedelta(minutes=10)
        await _seed_incident(
            conn,
            suffix="D1",
            alert_state="stop_sent",
            last_transition_at=ten_min_ago,
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/incidents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    ds = data[0]["incident_duration_seconds"]
    # Должно быть около 600 (10 минут * 60), с допуском на runtime теста
    assert ds is not None
    assert 590 <= ds <= 700, f"unexpected duration: {ds}"
    assert data[0]["incident_open_since"] is not None


# transitions_count из alert_events.
@pytest.mark.asyncio
async def test_incidents_transitions_count(pg_engine, fake_redis_client, clean_incidents) -> None:
    """transitions_count считает alert_events с момента last_transition_at."""
    async with pg_engine.begin() as conn:
        start = datetime.now(UTC) - timedelta(hours=1)
        # 3 alert_events после start
        await _seed_incident(
            conn,
            suffix="E1",
            alert_state="stop_sent",
            last_transition_at=start,
            add_alert_events=3,
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/incidents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["transitions_count"] == 3


# normal/claimed/disabled НЕ попадают (повтор assert логики, но явный).
@pytest.mark.asyncio
async def test_incidents_excludes_terminal_states(
    pg_engine, fake_redis_client, clean_incidents
) -> None:
    """normal, claimed, disabled — НЕ инциденты, не должны возвращаться."""
    async with pg_engine.begin() as conn:
        await _seed_incident(conn, suffix="F1", alert_state="normal")
        await _seed_incident(conn, suffix="F2", alert_state="claimed")
        await _seed_incident(conn, suffix="F3", alert_state="disabled")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/incidents")
    assert resp.status_code == 200
    assert resp.json() == []
