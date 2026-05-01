# -*- coding: utf-8 -*-
"""Тесты health-check эндпоинтов /api/health и /api/health/details."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# --- Вспомогательные фабрики ---


def _make_scalar_result(value):
    """Создаёт мок scalar-результата SQLAlchemy."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _make_execute_result(rows: list):
    """Создаёт мок execute-результата SQLAlchemy с .all()."""
    r = MagicMock()
    r.all.return_value = rows
    return r


def _make_db(
    *,
    exec_ok: bool = True,
    observer_heartbeat: datetime | None = None,
    poller_heartbeat: datetime | None = None,
    disable_counts: list | None = None,
    enable_counts: list | None = None,
    max_observed: datetime | None = None,
):
    """Создаёт мок AsyncSession с настраиваемыми ответами."""
    db = AsyncMock()

    if not exec_ok:
        db.execute = AsyncMock(side_effect=Exception("БД недоступна"))
        db.scalar = AsyncMock(side_effect=Exception("БД недоступна"))
        return db

    # Порядок вызовов scalar/execute в health_details:
    # 1. text("SELECT 1") через execute
    # 2. scalar для ObserverSettings
    # 3. scalar для TelegramSettings
    # 4. execute для DisableTask counts
    # 5. execute для EnableTask counts
    # 6. scalar для max(AdSnapshot.last_observed_at)

    obs_row = SimpleNamespace(worker_heartbeat_at=observer_heartbeat)
    tg_row = SimpleNamespace(poller_heartbeat_at=poller_heartbeat)

    select_1_result = MagicMock()

    call_count = [0]

    async def _execute(stmt, *args, **kwargs):
        call_count[0] += 1
        # Первый вызов — SELECT 1
        if call_count[0] == 1:
            return select_1_result
        # Второй — disable task counts
        if call_count[0] == 2:
            return _make_execute_result(disable_counts or [])
        # Третий — enable task counts
        if call_count[0] == 3:
            return _make_execute_result(enable_counts or [])
        return _make_execute_result([])

    scalar_call_count = [0]

    async def _scalar(stmt, *args, **kwargs):
        scalar_call_count[0] += 1
        if scalar_call_count[0] == 1:
            return obs_row
        if scalar_call_count[0] == 2:
            return tg_row
        if scalar_call_count[0] == 3:
            return max_observed
        return None

    db.execute = _execute
    db.scalar = _scalar
    return db


def _make_app_with_db(db):
    """Создаёт FastAPI приложение с переопределённой зависимостью get_db."""
    from apps.api.deps import get_db
    from apps.api.main import app

    async def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    return app


# --- Тесты ---


# Сценарий 1: /api/health возвращает 200 при живой БД.
@pytest.mark.asyncio
async def test_health_liveness_ok():
    db = _make_db(exec_ok=True)
    app = _make_app_with_db(db)

    with patch("apps.api.routers.health._check_browser_agent", new_callable=AsyncMock) as ba:
        ba.return_value = MagicMock(healthy=True, error=None)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/api/health")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# Сценарий 2: /api/health/details — все воркеры без heartbeat → overall_healthy=false.
@pytest.mark.asyncio
async def test_health_details_no_heartbeats_overall_false():
    db = _make_db(
        observer_heartbeat=None,
        poller_heartbeat=None,
    )
    app = _make_app_with_db(db)

    with (
        patch("apps.api.routers.health._check_browser_agent", new_callable=AsyncMock) as ba,
        patch("apps.api.routers.health._check_vision", new_callable=AsyncMock) as vis,
    ):
        ba.return_value = MagicMock(healthy=True, error=None)
        vis.return_value = MagicMock(healthy=True, error=None)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/api/health/details")

    assert resp.status_code == 200
    data = resp.json()
    assert data["overall_healthy"] is False
    assert data["workers"]["observer"]["healthy"] is False
    assert data["workers"]["telegram_poller"]["healthy"] is False
    assert data["workers"]["observer"]["last_heartbeat_at"] is None


# Сценарий 3: observer heartbeat свежий, остальные — нет → observer.healthy=true, overall=false.
@pytest.mark.asyncio
async def test_health_details_observer_fresh_overall_false():
    fresh = datetime.now(UTC) - timedelta(seconds=10)
    db = _make_db(
        observer_heartbeat=fresh,
        poller_heartbeat=None,  # telegram_poller без heartbeat → unhealthy
    )
    app = _make_app_with_db(db)

    with (
        patch("apps.api.routers.health._check_browser_agent", new_callable=AsyncMock) as ba,
        patch("apps.api.routers.health._check_vision", new_callable=AsyncMock) as vis,
    ):
        ba.return_value = MagicMock(healthy=True, error=None)
        vis.return_value = MagicMock(healthy=True, error=None)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/api/health/details")

    assert resp.status_code == 200
    data = resp.json()
    assert data["workers"]["observer"]["healthy"] is True
    assert data["workers"]["observer"]["heartbeat_age_seconds"] is not None
    assert data["workers"]["telegram_poller"]["healthy"] is False
    # telegram_poller тоже критичный — overall должен быть false
    assert data["overall_healthy"] is False


# Сценарий 4: размер очередей читается корректно.
@pytest.mark.asyncio
async def test_health_details_queue_counts():
    from core.domain import DisableTaskStatus, EnableTaskStatus

    disable_rows = [
        (DisableTaskStatus.PENDING, 3),
        (DisableTaskStatus.RUNNING, 1),
    ]
    enable_rows = [
        (EnableTaskStatus.PENDING, 5),
    ]
    fresh = datetime.now(UTC) - timedelta(seconds=5)
    db = _make_db(
        observer_heartbeat=fresh,
        poller_heartbeat=fresh,
        disable_counts=disable_rows,
        enable_counts=enable_rows,
    )
    app = _make_app_with_db(db)

    with (
        patch("apps.api.routers.health._check_browser_agent", new_callable=AsyncMock) as ba,
        patch("apps.api.routers.health._check_vision", new_callable=AsyncMock) as vis,
    ):
        ba.return_value = MagicMock(healthy=True, error=None)
        vis.return_value = MagicMock(healthy=True, error=None)

        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/api/health/details")

    assert resp.status_code == 200
    data = resp.json()
    assert data["queues"]["disable_pending"] == 3
    assert data["queues"]["disable_running"] == 1
    assert data["queues"]["enable_pending"] == 5
    assert data["queues"]["enable_running"] == 0
