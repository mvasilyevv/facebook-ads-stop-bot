# -*- coding: utf-8 -*-
"""Тесты runtime-статуса сканирования и Vision."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException


# Проверяем, что dashboard отдаёт структурированные поля расписания сканирования.
def test_dashboard_runtime_fields_include_scan_schedule():
    from apps.api.routers.dashboard import _serialize_observer_runtime_fields

    next_scan_at = datetime(2026, 4, 24, 12, 1, 30, tzinfo=UTC)
    row = SimpleNamespace(
        worker_status="RUNNING",
        worker_message="Ожидаем следующий цикл сканирования.",
        worker_heartbeat_at=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
        worker_last_error=None,
        worker_last_error_at=None,
        current_scan_interval_seconds=90,
        current_scan_jitter_seconds=9,
        current_scan_threat_level="CALM",
        next_scan_at=next_scan_at,
    )

    result = _serialize_observer_runtime_fields(row)

    assert result["current_scan_interval_seconds"] == 90
    assert result["current_scan_jitter_seconds"] == 9
    assert result["current_scan_threat_level"] == "CALM"
    assert result["next_scan_at"] == next_scan_at.isoformat()


# Проверяем, что сон observer сохраняет фактическое время следующего скана с jitter.
@pytest.mark.asyncio
async def test_wait_for_next_cycle_records_jittered_next_scan():
    from apps.observer_worker import main as observer_main

    update_status = AsyncMock()
    before_call = datetime.now(UTC)

    with (
        patch("apps.observer_worker.main.compute_jitter", return_value=0.01),
        patch(
            "apps.observer_worker.main.peek_scan_requested_flag",
            new=AsyncMock(return_value=False),
        ),
        patch("apps.observer_worker.main.update_observer_runtime_status", new=update_status),
    ):
        result = await observer_main._wait_for_next_cycle(
            shutdown_event=None,
            cycle_completed=True,
            adaptive_interval=20,
            threat_level="ELEVATED",
        )

    assert result is True
    assert update_status.await_args_list
    kwargs = update_status.await_args_list[0].kwargs
    assert kwargs["current_scan_interval_seconds"] == 20
    assert kwargs["current_scan_jitter_seconds"] == 2
    assert kwargs["current_scan_threat_level"] == "ELEVATED"
    assert before_call <= kwargs["next_scan_at"] <= before_call + timedelta(seconds=1)


# Проверяем, что runtime Vision отличает остановленный профиль от профиля без CDP.
@pytest.mark.asyncio
async def test_vision_runtime_reports_not_running(monkeypatch):
    from apps.api.routers import vision_telegram

    async def fake_vision_request(api_url: str, x_token: str, path: str):
        return {"profiles": []}

    monkeypatch.setattr(vision_telegram, "_vision_request", fake_vision_request)

    result = await vision_telegram._build_vision_runtime_status(
        api_url="http://vision.local",
        x_token="token",
        profile_id="profile-1",
    )

    assert result["runtime_status"] == "NOT_RUNNING"
    assert result["profile_running"] is False
    assert result["cdp_ready"] is False


# Проверяем, что runtime Vision показывает профиль, который запущен без CDP-порта.
@pytest.mark.asyncio
async def test_vision_runtime_reports_missing_cdp(monkeypatch):
    from apps.api.routers import vision_telegram

    async def fake_vision_request(api_url: str, x_token: str, path: str):
        return {
            "profiles": [
                {
                    "folder_id": "folder-1",
                    "profile_id": "profile-1",
                    "port": None,
                }
            ]
        }

    monkeypatch.setattr(vision_telegram, "_vision_request", fake_vision_request)

    result = await vision_telegram._build_vision_runtime_status(
        api_url="http://vision.local",
        x_token="token",
        profile_id="profile-1",
    )

    assert result["runtime_status"] == "MISSING_CDP"
    assert result["profile_running"] is True
    assert result["folder_id"] == "folder-1"


# Проверяем, что протухший Vision X-Token показывается отдельным runtime-статусом.
@pytest.mark.asyncio
async def test_vision_runtime_reports_invalid_token(monkeypatch):
    from apps.api.routers import vision_telegram

    async def fake_vision_request(api_url: str, x_token: str, path: str):
        raise HTTPException(
            status_code=401,
            detail="Vision X-Token недействителен или истёк.",
        )

    monkeypatch.setattr(vision_telegram, "_vision_request", fake_vision_request)

    result = await vision_telegram._build_vision_runtime_status(
        api_url="http://vision.local",
        x_token="token",
        profile_id="profile-1",
    )

    assert result["runtime_status"] == "INVALID_TOKEN"
    assert "истёк" in result["runtime_status_message"]
