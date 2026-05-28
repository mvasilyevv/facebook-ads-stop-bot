# -*- coding: utf-8 -*-
"""Контрактный тест writer↔reader для observer:runtime.

Верифицирует что писатель (apps/observer_worker/main.py::_publish_runtime_status)
и читатель (core/observer/runtime.py::read_observer_runtime) согласованы:
нормализация scanning/idle→running, paused→paused, отсутствие ключа→unknown.

Покрывает:
    1. Писатель пишет scanning → читатель видит status="running"
    2. Писатель пишет idle → читатель видит status="running"
    3. Писатель пишет paused → читатель видит status="paused"
    4. Ключ отсутствует → status="unknown" (graceful fallback)
    5. Битый JSON → status="unknown" (не падает)
    6. /observer/status endpoint возвращает реальный статус (не unknown)
    7. /dashboard/stats endpoint — observer_status не "unknown" когда ключ записан
    8. active_phase и last_successful_scan_at пробрасываются читателем
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.deps import get_redis
from apps.api.main import create_app
from apps.observer_worker.main import _publish_runtime_status
from core.observer.runtime import read_observer_runtime

# ───────────────────────── helpers ─────────────────────────────────────────


def _make_app(redis=None):
    """FastAPI с подменённым Redis."""
    app = create_app()
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


# ────────────────────── unit: read_observer_runtime ─────────────────────────


# Читатель возвращает unknown при отсутствии ключа
@pytest.mark.asyncio
async def test_runtime_no_key_returns_unknown(fake_redis_client) -> None:
    """Ключ отсутствует → status='unknown', все поля None, raw пустой."""
    result = await read_observer_runtime(fake_redis_client)
    assert result["status"] == "unknown"
    assert result["active_phase"] is None
    assert result["next_scan_at"] is None
    assert result["raw"] == {}


# Читатель возвращает unknown при битом JSON
@pytest.mark.asyncio
async def test_runtime_invalid_json_returns_unknown(fake_redis_client) -> None:
    """Невалидный JSON в ключе → status='unknown', не падает с исключением."""
    await fake_redis_client.set("observer:runtime", "{ not json }")
    result = await read_observer_runtime(fake_redis_client)
    assert result["status"] == "unknown"


# Читатель возвращает unknown при None (redis=None)
@pytest.mark.asyncio
async def test_runtime_none_redis_returns_unknown() -> None:
    """redis=None → status='unknown', не падает."""
    result = await read_observer_runtime(None)
    assert result["status"] == "unknown"


# ─────────────────── E2E: writer → reader (контракт) ─────────────────────────


# writer(scanning) → reader видит running
@pytest.mark.asyncio
async def test_contract_scanning_maps_to_running(fake_redis_client) -> None:
    """Писатель пишет status='scanning' → читатель получает status='running'."""
    await _publish_runtime_status(fake_redis_client, status="scanning", active_phase="scan")
    result = await read_observer_runtime(fake_redis_client)
    assert result["status"] == "running", (
        f"Ожидали 'running', получили '{result['status']}'. "
        f"Контракт нарушен: scanning должен нормализоваться в running."
    )


# writer(idle) → reader видит running
@pytest.mark.asyncio
async def test_contract_idle_maps_to_running(fake_redis_client) -> None:
    """Писатель пишет status='idle' → читатель получает status='running'."""
    await _publish_runtime_status(fake_redis_client, status="idle")
    result = await read_observer_runtime(fake_redis_client)
    assert result["status"] == "running", (
        f"Ожидали 'running', получили '{result['status']}'. "
        f"Контракт нарушен: idle должен нормализоваться в running."
    )


# writer(dispatch) → reader видит running
@pytest.mark.asyncio
async def test_contract_dispatch_maps_to_running(fake_redis_client) -> None:
    """Писатель пишет status='dispatch' → читатель получает status='running'."""
    await _publish_runtime_status(fake_redis_client, status="dispatch")
    result = await read_observer_runtime(fake_redis_client)
    assert result["status"] == "running"


# writer(paused) → reader видит paused
@pytest.mark.asyncio
async def test_contract_paused_maps_to_paused(fake_redis_client) -> None:
    """Писатель пишет status='paused' → читатель получает status='paused'."""
    await _publish_runtime_status(fake_redis_client, status="paused")
    result = await read_observer_runtime(fake_redis_client)
    assert result["status"] == "paused", f"Ожидали 'paused', получили '{result['status']}'."


# writer сохраняет active_phase и last_successful_scan_at
@pytest.mark.asyncio
async def test_contract_extra_fields_preserved(fake_redis_client) -> None:
    """Поля active_phase и last_successful_scan_at пробрасываются через контракт."""
    now = datetime.now(timezone.utc)
    await _publish_runtime_status(
        fake_redis_client,
        status="scanning",
        active_phase="dispatch",
        last_successful_scan_at=now,
    )
    result = await read_observer_runtime(fake_redis_client)
    assert result["active_phase"] == "dispatch"
    assert result["last_successful_scan_at"] == now.isoformat()


# writer пишет и worker_status (детальный) и status (нормализованный)
@pytest.mark.asyncio
async def test_contract_writer_writes_both_fields(fake_redis_client) -> None:
    """Писатель должен писать оба поля: worker_status (детальный) и status (нормализованный)."""
    await _publish_runtime_status(fake_redis_client, status="scanning")
    raw_bytes = await fake_redis_client.get("observer:runtime")
    payload = json.loads(raw_bytes)
    # worker_status — детальное значение
    assert payload.get("worker_status") == "scanning"
    # status — нормализованное значение
    assert payload.get("status") == "running"


# ─────────────────── E2E endpoint: /observer/status ──────────────────────────


# /observer/status возвращает running (не unknown) когда воркер пишет scanning
@pytest.mark.asyncio
async def test_endpoint_observer_status_shows_running(fake_redis_client) -> None:
    """GET /observer/status возвращает status='running' когда воркер написал 'scanning'."""
    await _publish_runtime_status(fake_redis_client, status="scanning", active_phase="scan")
    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/observer/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running", (
        f"Endpoint вернул '{data['status']}' вместо 'running'. CRIT-2 не исправлен."
    )


# /observer/status возвращает paused когда воркер пишет paused
@pytest.mark.asyncio
async def test_endpoint_observer_status_shows_paused(fake_redis_client) -> None:
    """GET /observer/status возвращает status='paused' когда воркер написал 'paused'."""
    await _publish_runtime_status(fake_redis_client, status="paused")
    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/observer/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "paused"


# /observer/status возвращает unknown при отсутствии ключа
@pytest.mark.asyncio
async def test_endpoint_observer_status_unknown_on_missing_key(fake_redis_client) -> None:
    """GET /observer/status → status='unknown' если ключ в Redis отсутствует."""
    app = _make_app(redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/observer/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "unknown"


# ─────────────────── E2E endpoint: /dashboard/stats ──────────────────────────


# /dashboard/stats.observer_status не unknown когда воркер активен
@pytest.mark.asyncio
async def test_endpoint_dashboard_stats_observer_not_unknown(pg_engine, fake_redis_client) -> None:
    """GET /dashboard/stats → observer_status='running' когда воркер написал 'idle'."""
    await _publish_runtime_status(fake_redis_client, status="idle")
    app = _make_app(redis=fake_redis_client)
    # Подменяем engine тоже чтобы stats не падал на SQL
    from apps.api.deps import get_engine

    app.dependency_overrides[get_engine] = lambda: pg_engine

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("observer_status") == "running", (
        f"observer_status='{data.get('observer_status')}' вместо 'running'. "
        f"Путь dashboard_stats→_read_observer_status не использует контракт."
    )
