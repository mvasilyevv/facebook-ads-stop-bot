# -*- coding: utf-8 -*-
"""Endpoint POST /api/observer/start-new-cabinet-day сдвигает границу суток."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient


def _override_db(app, db):
    """Заменяет зависимость get_db в FastAPI-приложении на заглушку."""
    from apps.api.deps import get_db

    async def _provide():
        yield db

    app.dependency_overrides[get_db] = _provide


# Endpoint должен сдвинуть cabinet_day_started_at и вернуть archived_ads.
def test_start_new_cabinet_day_returns_ok():
    from apps.api.main import app

    settings = SimpleNamespace(cabinet_day_started_at=datetime(2026, 5, 21, tzinfo=UTC))
    db = AsyncMock()
    db.commit = AsyncMock()
    db.add = MagicMock()

    scalars_result = MagicMock()
    scalars_result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=scalars_result)

    from apps.api.deps import require_api_key_or_tma

    async def _allow() -> None:
        return None

    app.dependency_overrides[require_api_key_or_tma] = _allow
    with (
        patch(
            "apps.api.routers.observer.get_or_create_observer_settings",
            new=AsyncMock(return_value=settings),
        ),
    ):
        _override_db(app, db)
        try:
            with TestClient(app) as client:
                resp = client.post("/api/observer/start-new-cabinet-day")
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert "new_day_started_at" in body
    assert isinstance(body["archived_ads"], int)
    db.commit.assert_awaited_once()


# Endpoint GET /api/observer/status возвращает поля для UI-плитки.
def test_get_observer_status_returns_fields():
    from apps.api.main import app

    settings = SimpleNamespace(
        cabinet_day_started_at=datetime(2026, 5, 21, tzinfo=UTC),
        is_scanning_enabled=True,
        worker_status="RUNNING",
        worker_message=None,
        worker_heartbeat_at=datetime(2026, 5, 21, 12, tzinfo=UTC),
        worker_last_error=None,
        worker_last_error_at=None,
        current_scan_id=7,
        next_scan_at=datetime(2026, 5, 21, 12, 1, tzinfo=UTC),
    )
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=[42, 50])
    # Запрос последнего ScanRun возвращает пустой результат
    scan_run_result = MagicMock()
    scan_run_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=scan_run_result)

    from apps.api.deps import require_api_key_or_tma

    async def _allow() -> None:
        return None

    app.dependency_overrides[require_api_key_or_tma] = _allow
    with (
        patch(
            "apps.api.routers.observer.get_or_create_observer_settings",
            new=AsyncMock(return_value=settings),
        ),
    ):
        _override_db(app, db)
        try:
            with TestClient(app) as client:
                resp = client.get("/api/observer/status")
        finally:
            app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_scanning_enabled"] is True
    assert body["worker_status"] == "RUNNING"
    assert body["current_scan_id"] == 7
    assert body["last_batch_size"] == 42
    assert body["active_total"] == 50
    assert body["cabinet_day_started_at"] == "2026-05-21T00:00:00+00:00"
