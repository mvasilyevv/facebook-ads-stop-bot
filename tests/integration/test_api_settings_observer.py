# -*- coding: utf-8 -*-
"""Интеграционные тесты роутера settings_observer.

Тесты используют AsyncClient + ASGITransport (как test_api_health.py), чтобы
async pg_engine fixture из conftest работала в том же event loop, что и app.

Паттерн:
    app = _make_app(engine=pg_engine, redis=fake_redis)
    async with AsyncClient(...) as ac:
        resp = await ac.get("/api/settings/observer")
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(*, engine=None, redis=None):
    """Собирает FastAPI с явными override engine/redis."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_observer_config(pg_engine):
    """Сбрасывает singleton observer_config до server-defaults перед и после теста."""
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM observer_config"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM observer_config"))


# GET возвращает дефолтный singleton (is_scanning_enabled=true).
@pytest.mark.asyncio
async def test_get_observer_settings_returns_defaults(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/settings/observer")
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_scanning_enabled"] is True
    assert isinstance(data["default_interval_seconds"], int)
    assert data["auto_enable_recommendations"] is False
    # Поля перенесены в OfferRule — возвращаем null для стабильного shape.
    assert data["warning_percent_of_stop"] is None


# PUT обновляет поля, последующий GET отражает изменения.
@pytest.mark.asyncio
async def test_put_observer_settings_persists(pg_engine, fake_redis_client, clean_observer_config):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "is_scanning_enabled": False,
        "default_interval_seconds": 120,
        "auto_enable_recommendations": True,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        put_resp = await ac.put("/api/settings/observer", json=body)
        assert put_resp.status_code == 200

        get_resp = await ac.get("/api/settings/observer")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["is_scanning_enabled"] is False
    assert data["default_interval_seconds"] == 120
    assert data["auto_enable_recommendations"] is True


# PUT с interval_seconds=10 (меньше допустимого минимума 30) → 422.
@pytest.mark.asyncio
async def test_put_observer_settings_validates_interval(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "is_scanning_enabled": True,
        "default_interval_seconds": 10,
        "auto_enable_recommendations": False,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/settings/observer", json=body)
    assert resp.status_code == 422


# PUT с interval_seconds=700 (больше максимума 600) → 422.
@pytest.mark.asyncio
async def test_put_observer_settings_validates_interval_max(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "is_scanning_enabled": True,
        "default_interval_seconds": 700,
        "auto_enable_recommendations": False,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put("/api/settings/observer", json=body)
    assert resp.status_code == 422


# PATCH /scanning меняет только is_scanning_enabled.
@pytest.mark.asyncio
async def test_patch_scanning_changes_only_scanning_flag(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Сначала убеждаемся что scanning включён.
        get_before = await ac.get("/api/settings/observer")
        assert get_before.json()["is_scanning_enabled"] is True

        # Отключаем.
        patch_resp = await ac.patch("/api/settings/observer/scanning", json={"enabled": False})
        assert patch_resp.status_code == 200
        assert patch_resp.json()["is_scanning_enabled"] is False

        # Проверяем что get отражает изменение, остальные поля — нетронуты.
        get_after = await ac.get("/api/settings/observer")
    data_after = get_after.json()
    assert data_after["is_scanning_enabled"] is False
    # auto_enable_recommendations не должно измениться.
    assert data_after["auto_enable_recommendations"] is False


# PATCH /auto-enable меняет колонку auto_enable_recommendations (проверка миграции 0003).
@pytest.mark.asyncio
async def test_patch_auto_enable_toggles_column(
    pg_engine, fake_redis_client, clean_observer_config
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Включаем auto-enable.
        resp_on = await ac.patch("/api/settings/observer/auto-enable", json={"enabled": True})
        assert resp_on.status_code == 200
        assert resp_on.json()["auto_enable_recommendations"] is True

        # Выключаем.
        resp_off = await ac.patch("/api/settings/observer/auto-enable", json={"enabled": False})
        assert resp_off.status_code == 200
        assert resp_off.json()["auto_enable_recommendations"] is False

        # GET должен видеть последнее значение.
        get_resp = await ac.get("/api/settings/observer")
    assert get_resp.json()["auto_enable_recommendations"] is False


# POST /scan-now публикует сообщение в Redis-канал fb_agent:observer:trigger.
@pytest.mark.asyncio
async def test_scan_now_publishes_to_redis(fake_redis_client):
    app = _make_app(redis=fake_redis_client)

    # Подписываемся на канал до публикации.
    pubsub = fake_redis_client.pubsub()
    await pubsub.subscribe("fb_agent:observer:trigger")
    # Пропускаем subscribe-confirmation сообщение.
    await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/settings/observer/scan-now")

    assert resp.status_code == 200
    assert resp.json()["status"] == "triggered"

    # Читаем сообщение из pubsub.
    msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
    assert msg is not None
    assert msg["type"] == "message"
    data = json.loads(msg["data"])
    assert data["requested_by"] == "api"
    assert "ts" in data

    await pubsub.unsubscribe("fb_agent:observer:trigger")
    await pubsub.aclose()


# POST /scan-now без Redis → 503 (Redis недоступен).
@pytest.mark.asyncio
async def test_scan_now_returns_503_when_redis_unavailable():
    """Подменяем redis на объект чей publish бросает RuntimeError."""
    from unittest.mock import AsyncMock, MagicMock

    broken_redis = MagicMock()
    broken_redis.publish = AsyncMock(side_effect=RuntimeError("Redis недоступен"))

    app = _make_app(redis=broken_redis)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/settings/observer/scan-now")

    assert resp.status_code == 503
    assert "Redis" in resp.json()["detail"]
