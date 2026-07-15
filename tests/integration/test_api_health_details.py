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
    """Все 10 ожидаемых воркеров ONLINE → overall=HEALTHY."""
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
    monkeypatch.setenv("EXPECTED_WORKERS", "observer,meta_api")
    # Только meta_api ONLINE, observer — нет
    await _set_heartbeat(fake_redis_client, "meta_api")

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


# ====================== meta_api_channel (probe канала auto-stop) ======================

_META_KEY = "meta_api:channel:health"


# Ключ health probe = ONLINE и overall не понижается (канал жив)
@pytest.mark.asyncio
async def test_health_details_meta_channel_online(fake_redis_client, monkeypatch) -> None:
    """meta_api:channel:health healthy → meta_api_channel.status=ONLINE, overall=HEALTHY."""
    monkeypatch.setenv("EXPECTED_WORKERS", "observer")
    await _set_heartbeat(fake_redis_client, "observer")
    await fake_redis_client.set(
        _META_KEY,
        json.dumps(
            {
                "healthy": True,
                "probe_ok": True,
                "detail": "ok",
                "reason": "ok",
                "checked_at": datetime.now(UTC).isoformat(),
            }
        ),
        ex=600,
    )

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    payload = resp.json()
    assert payload["meta_api_channel"]["status"] == "ONLINE"
    assert payload["meta_api_channel"]["healthy"] is True
    assert payload["overall"] == "HEALTHY"


# Канал мёртв (probe down) → DEGRADED даже при всех воркерах ONLINE
@pytest.mark.asyncio
async def test_health_details_meta_channel_down_degrades(fake_redis_client, monkeypatch) -> None:
    """meta-канал down → meta_api_channel.status=DEGRADED, overall=DEGRADED."""
    monkeypatch.setenv("EXPECTED_WORKERS", "observer")
    await _set_heartbeat(fake_redis_client, "observer")
    await fake_redis_client.set(
        _META_KEY,
        json.dumps(
            {
                "healthy": False,
                "probe_ok": False,
                "detail": "probe_network_down",
                "reason": "probe_network_down",
                "checked_at": datetime.now(UTC).isoformat(),
            }
        ),
        ex=600,
    )

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    payload = resp.json()
    assert payload["meta_api_channel"]["status"] == "DEGRADED"
    assert payload["overall"] == "DEGRADED"


# Probe намеренно пропущен на паузе → UNKNOWN, а не ложный DEGRADED
@pytest.mark.asyncio
async def test_health_details_skipped_meta_probe_is_unknown(fake_redis_client, monkeypatch) -> None:
    """Выключенное сканирование не должно выглядеть как отказ Meta-канала."""
    monkeypatch.setenv("EXPECTED_WORKERS", "observer")
    await _set_heartbeat(fake_redis_client, "observer")
    await fake_redis_client.set(
        _META_KEY,
        json.dumps(
            {
                "healthy": None,
                "probe_performed": False,
                "probe_ok": False,
                "probe_detail": "scanning_disabled",
                "detail": "сканирование выключено — канал не проверяется",
                "reason": "сканирование выключено",
                "checked_at": datetime.now(UTC).isoformat(),
            }
        ),
        ex=600,
    )

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    payload = resp.json()
    assert payload["meta_api_channel"]["status"] == "UNKNOWN"
    assert payload["meta_api_channel"]["healthy"] is None
    assert payload["overall"] == "HEALTHY"


# Нет ключа (прободер не писал/протух) → UNKNOWN, overall НЕ понижается
@pytest.mark.asyncio
async def test_health_details_meta_channel_unknown(fake_redis_client, monkeypatch) -> None:
    """Нет meta-ключа → status=UNKNOWN, overall остаётся HEALTHY (нет прободера ≠ отказ)."""
    monkeypatch.setenv("EXPECTED_WORKERS", "observer")
    await _set_heartbeat(fake_redis_client, "observer")

    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/health/details")

    payload = resp.json()
    assert payload["meta_api_channel"]["status"] == "UNKNOWN"
    assert payload["overall"] == "HEALTHY"


# Контракт: ключ Redis в health_details совпадает с тем, что пишет health_watchdog
def test_meta_channel_key_contract() -> None:
    from apps.api.routers.v1.health_details import META_CHANNEL_HEALTH_KEY as reader_key
    from apps.health_watchdog.main import META_CHANNEL_HEALTH_KEY as writer_key

    assert reader_key == writer_key, "рассинхрон ключа meta-канала writer↔reader"
