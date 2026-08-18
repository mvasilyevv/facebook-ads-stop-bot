# -*- coding: utf-8 -*-
"""Интеграционный: health-endpoints FastAPI (healthz / readyz / metrics).

- /healthz и /metrics — без зависимостей от Postgres/Redis, через sync TestClient.
- /readyz — через httpx.AsyncClient + ASGITransport, чтобы pg_engine fixture
  (созданная в event loop pytest-asyncio) работала в том же loop, что и app.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app
from apps.api.routers import health as health_router
from apps.api.routers.health import reset_readyz_cache


def _make_app_with_overrides(*, engine=None, redis=None):
    """Сборка FastAPI с проброшенными PG/Redis через dependency_overrides."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        # Также кладём в app.state, чтобы lifespan не создавал реальный Redis,
        # если тест когда-нибудь зайдёт под `with TestClient(app)`.
        app.state.redis = redis
    reset_readyz_cache()
    return app


# /healthz всегда возвращает 200 и не лезет в БД.
def test_healthz_returns_ok() -> None:
    app = _make_app_with_overrides()
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    # X-Request-Id добавляется middleware'ом.
    assert "x-request-id" in {k.lower() for k in resp.headers.keys()}


# /metrics отдаёт Prometheus exposition с content-type и не пустым телом.
def test_metrics_returns_prometheus_format() -> None:
    app = _make_app_with_overrides()
    client = TestClient(app)
    # Делаем запрос чтобы middleware успел инкрементнуть метрики хотя бы раз.
    client.get("/healthz")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    # prometheus_client возвращает text/plain с version=0.0.4 в content-type.
    assert "text/plain" in resp.headers["content-type"]
    body = resp.text
    assert "app_requests_total" in body
    assert "app_request_duration_seconds" in body


# /readyz возвращает 200 при живом Postgres + Redis (fakeredis).
@pytest.mark.asyncio
async def test_readyz_returns_200_when_pg_and_redis_ok(pg_engine, fake_redis_client) -> None:
    app = _make_app_with_overrides(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/readyz")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ready"] is True
    assert payload["postgres"] is True
    assert payload["redis"] is True
    assert payload["degraded"] == []


@pytest.mark.asyncio
async def test_readyz_keeps_postgres_control_plane_ready_when_redis_is_down(
    pg_engine,
) -> None:
    unavailable_redis = AsyncMock()
    unavailable_redis.ping.side_effect = ConnectionError("redis unavailable")
    app = _make_app_with_overrides(engine=pg_engine, redis=unavailable_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.get("/readyz")
        cached = await ac.get("/readyz")

    assert first.status_code == 200
    assert first.json() == {
        "ready": True,
        "postgres": True,
        "redis": False,
        "degraded": ["redis_unavailable"],
        "cached": False,
    }
    assert cached.status_code == 200
    assert cached.json()["postgres"] is True
    assert cached.json()["redis"] is False
    assert cached.json()["degraded"] == ["redis_unavailable"]


# Повторный вызов /readyz в пределах TTL отдаёт результат из кэша.
@pytest.mark.asyncio
async def test_readyz_uses_ttl_cache(pg_engine, fake_redis_client) -> None:
    app = _make_app_with_overrides(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.get("/readyz")
        second = await ac.get("/readyz")
    assert first.status_code == 200
    assert first.json()["cached"] is False
    assert second.status_code == 200
    # Второй ответ — из кэша (5-секундный TTL).
    assert second.json()["cached"] is True
    assert second.json()["ready"] is True


def _durable_scan_state(*, enabled: bool, activity_at: datetime | None) -> dict:
    return {
        "enabled": enabled,
        "last_scan_at": activity_at,
        "last_scan_outcome": "success" if activity_at else None,
        "next_scan_at": None,
        "actors": (
            [
                {
                    "ad_account_id": "123456",
                    "owner_instance": None,
                    "lease_expires_at": None,
                    "stage": "idle",
                    "last_progress_at": activity_at,
                    "last_snapshot_at": activity_at,
                    "error": None,
                }
            ]
            if activity_at
            else []
        ),
    }


@pytest.mark.asyncio
async def test_system_readyz_returns_200_only_for_live_business_contour(
    pg_engine,
    fake_redis_client,
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        health_router,
        "fetch_operator_scan_state",
        AsyncMock(return_value=_durable_scan_state(enabled=True, activity_at=now)),
    )
    monkeypatch.setattr(
        health_router, "resolve_configured_ad_account_ids", AsyncMock(return_value=["123456"])
    )
    monkeypatch.setattr(health_router, "_load_money_task_failures", AsyncMock(return_value=(0, 0)))

    app = _make_app_with_overrides(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/system-readyz")

    assert resp.status_code == 200
    assert resp.json() == {
        "ready": True,
        "infrastructure_ready": True,
        "overall": "HEALTHY",
        "actors_active": 1,
        "actors_expected": 1,
        "scanning_enabled": True,
        "last_scan_at": now.isoformat().replace("+00:00", "Z"),
        "last_activity_at": now.isoformat().replace("+00:00", "Z"),
        "stale_money_tasks": 0,
        "expired_money_tasks": 0,
        "blockers": [],
        "degraded": [],
    }


@pytest.mark.asyncio
async def test_system_readyz_does_not_consult_optional_redis(
    pg_engine,
    monkeypatch,
) -> None:
    unavailable_redis = AsyncMock()
    unavailable_redis.ping.side_effect = ConnectionError("redis unavailable")
    now = datetime.now(UTC)
    monkeypatch.setattr(
        health_router,
        "fetch_operator_scan_state",
        AsyncMock(return_value=_durable_scan_state(enabled=True, activity_at=now)),
    )
    monkeypatch.setattr(
        health_router, "resolve_configured_ad_account_ids", AsyncMock(return_value=["123456"])
    )
    monkeypatch.setattr(health_router, "_load_money_task_failures", AsyncMock(return_value=(0, 0)))
    app = _make_app_with_overrides(engine=pg_engine, redis=unavailable_redis)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/system-readyz")

    assert resp.status_code == 200
    assert resp.json()["ready"] is True
    unavailable_redis.ping.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_readyz_reports_offline_business_contour(
    pg_engine,
    fake_redis_client,
    monkeypatch,
) -> None:
    stale_at = datetime.now(UTC) - timedelta(minutes=5)
    monkeypatch.setattr(
        health_router,
        "fetch_operator_scan_state",
        AsyncMock(return_value=_durable_scan_state(enabled=True, activity_at=stale_at)),
    )
    monkeypatch.setattr(
        health_router, "resolve_configured_ad_account_ids", AsyncMock(return_value=["123456"])
    )
    monkeypatch.setattr(health_router, "_load_money_task_failures", AsyncMock(return_value=(0, 0)))

    app = _make_app_with_overrides(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/system-readyz")

    payload = resp.json()
    assert resp.status_code == 503
    assert payload["infrastructure_ready"] is True
    assert payload["overall"] == "CRITICAL"
    assert "stale_cabinet_actors:123456" in payload["blockers"]
    assert any(item.startswith("scan_snapshot_stale:") for item in payload["blockers"])


@pytest.mark.asyncio
async def test_system_readyz_treats_operator_pause_as_not_business_ready(
    pg_engine,
    fake_redis_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        health_router,
        "fetch_operator_scan_state",
        AsyncMock(return_value=_durable_scan_state(enabled=False, activity_at=None)),
    )
    monkeypatch.setattr(
        health_router, "resolve_configured_ad_account_ids", AsyncMock(return_value=["123456"])
    )
    monkeypatch.setattr(health_router, "_load_money_task_failures", AsyncMock(return_value=(0, 0)))

    app = _make_app_with_overrides(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/system-readyz")

    assert resp.status_code == 503
    assert resp.json()["blockers"] == ["scanning_paused"]


# X-Request-Id из запроса проксируется обратно в ответ — для трассировки.
def test_request_id_is_echoed_back() -> None:
    app = _make_app_with_overrides()
    client = TestClient(app)
    resp = client.get("/healthz", headers={"X-Request-Id": "trace-123"})
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == "trace-123"
