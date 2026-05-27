# -*- coding: utf-8 -*-
"""Интеграционные тесты: роутер GET /health/details (v1).

Проверяет агрегацию worker:heartbeat:* ключей и
вычисление overall (HEALTHY / DEGRADED / CRITICAL).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.deps import get_redis
from apps.api.main import create_app

_DEFAULT_WORKERS = [
    "observer",
    "disable_worker",
    "enable_worker",
    "telegram_poller",
    "meta_api_worker",
    "health_watchdog",
    "cleanup_worker",
    "reconciler_worker",
    "enable_recommendation_worker",
    "digest_scheduler",
    "creator_worker",
    "creator_recorder",
]


def _make_app(redis=None):
    """Собрать FastAPI с подменённым Redis."""
    app = create_app()
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


async def _set_heartbeat(redis, worker_name: str, ttl: int = 60) -> None:
    """Записать heartbeat-ключ с TTL."""
    payload = {"ts": datetime.now(UTC).isoformat(), "worker": worker_name}
    await redis.set(f"worker:heartbeat:{worker_name}", json.dumps(payload), ex=ttl)


# Все воркеры ONLINE → overall = HEALTHY
@pytest.mark.asyncio
async def test_health_details_all_online(fake_redis_client, monkeypatch) -> None:
    """Все 12 ожидаемых воркеров ONLINE → overall=HEALTHY."""
    monkeypatch.setenv("EXPECTED_WORKERS", ",".join(_DEFAULT_WORKERS))
    for w in _DEFAULT_WORKERS:
        await _set_heartbeat(fake_redis_client, w)

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["overall"] == "HEALTHY"
    statuses = {w["name"]: w["status"] for w in payload["workers"]}
    for w in _DEFAULT_WORKERS:
        assert statuses[w] == "ONLINE", f"{w} должен быть ONLINE"


# 1 воркер OFFLINE (не observer) → DEGRADED
@pytest.mark.asyncio
async def test_health_details_one_offline_degraded(fake_redis_client, monkeypatch) -> None:
    """Один ненаблюдаемый воркер OFFLINE → overall=DEGRADED."""
    monkeypatch.setenv("EXPECTED_WORKERS", ",".join(_DEFAULT_WORKERS))
    # Все кроме digest_scheduler
    for w in _DEFAULT_WORKERS:
        if w != "digest_scheduler":
            await _set_heartbeat(fake_redis_client, w)
    # digest_scheduler — не пишем ключ

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["overall"] == "DEGRADED"
    statuses = {w["name"]: w["status"] for w in payload["workers"]}
    assert statuses["digest_scheduler"] == "OFFLINE"


# observer OFFLINE → CRITICAL
@pytest.mark.asyncio
async def test_health_details_observer_offline_critical(fake_redis_client, monkeypatch) -> None:
    """observer OFFLINE → overall=CRITICAL независимо от остальных."""
    monkeypatch.setenv("EXPECTED_WORKERS", "observer,disable_worker")
    # Только disable_worker ONLINE, observer — нет
    await _set_heartbeat(fake_redis_client, "disable_worker")

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["overall"] == "CRITICAL"
    statuses = {w["name"]: w["status"] for w in payload["workers"]}
    assert statuses["observer"] == "OFFLINE"


# observer:runtime подхватывается
@pytest.mark.asyncio
async def test_health_details_observer_runtime_present(fake_redis_client, monkeypatch) -> None:
    """Ключ observer:runtime присутствует → возвращается в поле observer_runtime."""
    monkeypatch.setenv("EXPECTED_WORKERS", "observer")
    await _set_heartbeat(fake_redis_client, "observer")
    runtime = {"status": "running", "last_scan_at": datetime.now(UTC).isoformat()}
    await fake_redis_client.set("observer:runtime", json.dumps(runtime))

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["observer_runtime"] is not None
    assert payload["observer_runtime"]["status"] == "running"


# Нет ключа observer:runtime → поле null
@pytest.mark.asyncio
async def test_health_details_no_observer_runtime(fake_redis_client, monkeypatch) -> None:
    """Нет ключа observer:runtime → observer_runtime=null."""
    monkeypatch.setenv("EXPECTED_WORKERS", "observer")
    await _set_heartbeat(fake_redis_client, "observer")

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["observer_runtime"] is None
