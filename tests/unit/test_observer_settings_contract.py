"""Observer settings expose only field-scoped writes."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Response

import apps.api.routers.v1.settings_observer as settings_observer
from apps.api.main import create_app


def test_observer_settings_forbid_full_snapshot_put() -> None:
    paths = create_app().openapi()["paths"]

    assert "put" not in paths["/api/settings/observer"]
    assert "patch" in paths["/api/settings/observer/interval"]
    assert "patch" in paths["/api/settings/observer/scanning"]
    assert "patch" in paths["/api/settings/observer/owner-tag"]
    assert "patch" in paths["/api/settings/observer/campaigns"]
    assert "200" in paths["/api/settings/observer/scan-now"]["post"]["responses"]


@pytest.mark.asyncio
async def test_legacy_scan_now_also_uses_command_service(monkeypatch) -> None:
    enqueue = AsyncMock(
        return_value=SimpleNamespace(
            task_id=1842,
            state="running",
            correlation_id=uuid.UUID("00000000-0000-0000-0000-000000001842"),
            created=False,
        )
    )
    monkeypatch.setattr(
        settings_observer,
        "CommandService",
        lambda _engine: SimpleNamespace(enqueue_scan_retry=enqueue),
    )
    response = Response()

    result = await settings_observer.post_scan_now(object(), response)

    assert response.status_code == 200
    assert result.status == "running"
    assert result.task_id == 1842
    assert result.created is False
    enqueue.assert_awaited_once()
