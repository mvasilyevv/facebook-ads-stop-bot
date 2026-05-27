# -*- coding: utf-8 -*-
"""Интеграционный: health-endpoints FastAPI (healthz / readyz / metrics).

- /healthz и /metrics — без зависимостей от Postgres/Redis, через sync TestClient.
- /readyz — через httpx.AsyncClient + ASGITransport, чтобы pg_engine fixture
  (созданная в event loop pytest-asyncio) работала в том же loop, что и app.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app
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


# X-Request-Id из запроса проксируется обратно в ответ — для трассировки.
def test_request_id_is_echoed_back() -> None:
    app = _make_app_with_overrides()
    client = TestClient(app)
    resp = client.get("/healthz", headers={"X-Request-Id": "trace-123"})
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id") == "trace-123"
