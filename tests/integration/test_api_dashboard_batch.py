# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/batch.

Композитный endpoint — проверяем что все 5 ключей возвращаются, параметры
лимитов работают, partial-failure не валит весь ответ.
"""

from __future__ import annotations

import time
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine=None, redis=None):
    """FastAPI с подменёнными PG/Redis."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_batch(pg_engine):
    """Очистка всех затрагиваемых таблиц."""

    async def _wipe():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM ad_metrics WHERE cycle_ts >= NOW() - INTERVAL '30 days'")
            )
            await conn.execute(
                text("DELETE FROM alert_events WHERE created_at >= NOW() - INTERVAL '30 days'")
            )
            await conn.execute(text("DELETE FROM enable_recommendations"))
            await conn.execute(text("DELETE FROM ad_alert_state"))
            await conn.execute(text("DELETE FROM fb_ads"))
            await conn.execute(text("DELETE FROM fb_adsets"))
            await conn.execute(text("DELETE FROM fb_campaigns"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'BAT_%'"))
            await conn.execute(text("DELETE FROM task_queue WHERE requested_by LIKE 'batch_%'"))

    await _wipe()
    yield
    await _wipe()


async def _seed_ad_with_alert(conn, suffix: str, stage: str = "warning"):
    """Создаёт offer→campaign→adset→ad + ad_alert_state + alert_event."""
    import json as _json

    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"66{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"BAT_{suffix}", "n": f"Batch offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"BAT_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"BAT_ADS_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"BAT_AD_{suffix}"},
    )
    # ad_alert_state для incidents
    state = "warning_sent" if stage == "warning" else "stop_sent"
    await conn.execute(
        text("INSERT INTO ad_alert_state (ad_id, alert_state, current_stage) VALUES (:a, :s, :cs)"),
        {"a": ad_id, "s": state, "cs": stage},
    )
    # alert_event
    await conn.execute(
        text(
            "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, "
            "metrics_json) VALUES (gen_random_uuid(), :a, :st, :state, "
            "CAST(:codes AS jsonb), CAST(:mj AS jsonb))"
        ),
        {
            "a": ad_id,
            "st": stage,
            "state": state,
            "codes": _json.dumps(["CPC"]),
            "mj": _json.dumps({"spend": "10.0"}),
        },
    )
    return ad_id, fb_ad_id


# Тест: все 5 ключей в ответе.
@pytest.mark.asyncio
async def test_batch_all_keys_present(pg_engine, fake_redis_client, clean_batch) -> None:
    """Ответ /batch обязан содержать все 5 ключей даже при пустой БД."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/batch")

    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) >= {
        "stats",
        "recent_incidents",
        "recent_alerts",
        "recent_disable_tasks",
        "enable_recommendations_pending",
    }
    assert isinstance(data["stats"], dict)
    assert isinstance(data["recent_incidents"], list)


# Тест: limits применяются.
@pytest.mark.asyncio
async def test_batch_limits_work(pg_engine, fake_redis_client, clean_batch) -> None:
    """Параметр incidents_limit=2 ограничивает recent_incidents."""
    async with pg_engine.begin() as conn:
        await _seed_ad_with_alert(conn, "L1", stage="warning")
        await _seed_ad_with_alert(conn, "L2", stage="warning")
        await _seed_ad_with_alert(conn, "L3", stage="stop")
        await _seed_ad_with_alert(conn, "L4", stage="stop")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/batch", params={"incidents_limit": 2})

    assert resp.status_code == 200
    data = resp.json()
    # У нас 4 инцидента, ожидаем не более 2.
    assert len(data["recent_incidents"]) <= 2


# Тест: пустая БД → списки пустые, stats нули по нашим срезам.
@pytest.mark.asyncio
async def test_batch_empty_db(pg_engine, fake_redis_client, clean_batch) -> None:
    """Пустая БД → recent_* массивы пустые (для наших данных)."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/batch")

    assert resp.status_code == 200
    data = resp.json()
    # stats — словарь с правильными ключами
    assert "observer_status" in data["stats"]
    # enable_recommendations — должны быть пустыми после wipe
    assert data["enable_recommendations_pending"] == []


# Тест: partial-failure — при недоступном Redis stats всё равно вернётся (с unknown).
@pytest.mark.asyncio
async def test_batch_partial_failure_redis_down(pg_engine, clean_batch) -> None:
    """Redis недоступен → stats.observer_status='unknown', остальные секции возвращаются.

    Документируем поведение: подмена redis на «битый» клиент не валит endpoint
    благодаря _safe_call внутри /batch.
    """

    class _BrokenRedis:
        async def get(self, key):
            raise RuntimeError("Redis down test")

    app = _make_app(engine=pg_engine, redis=_BrokenRedis())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/batch")

    assert resp.status_code == 200
    data = resp.json()
    # observer_status принимает 'unknown' при ошибке Redis
    assert data["stats"]["observer_status"] == "unknown"
    # Остальные секции — пустые массивы, но они есть
    assert "recent_incidents" in data


# Тест: производительность.
@pytest.mark.asyncio
async def test_batch_performance(pg_engine, fake_redis_client, clean_batch) -> None:
    """Sanity: /batch отвечает быстро даже с защитной нагрузкой."""
    async with pg_engine.begin() as conn:
        for s in ("P1", "P2", "P3"):
            await _seed_ad_with_alert(conn, s, stage="warning")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        t0 = time.perf_counter()
        resp = await ac.get("/api/dashboard/batch")
        dt = time.perf_counter() - t0

    assert resp.status_code == 200
    assert dt < 2.5
