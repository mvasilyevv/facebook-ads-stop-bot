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
    assert "patch" in paths["/api/settings/observer/ads-manager-columns"]
    assert "200" in paths["/api/settings/observer/scan-now"]["post"]["responses"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "created", "expected_status"),
    [("queued", True, 202), ("queued", False, 202), ("running", False, 200)],
)
async def test_legacy_scan_now_preserves_queued_command_lifecycle(
    monkeypatch,
    state: str,
    created: bool,
    expected_status: int,
) -> None:
    enqueue = AsyncMock(
        return_value=SimpleNamespace(
            task_id=1842,
            state=state,
            correlation_id=uuid.UUID("00000000-0000-0000-0000-000000001842"),
            created=created,
        )
    )
    monkeypatch.setattr(
        settings_observer,
        "CommandService",
        lambda _engine: SimpleNamespace(enqueue_scan_retry=enqueue),
    )
    response = Response()

    result = await settings_observer.post_scan_now(object(), response)

    assert response.status_code == expected_status
    assert result.status == state
    assert result.task_id == 1842
    assert result.created is created
    enqueue.assert_awaited_once()
    # 202 не может маскировать незавершённую команду под готовый scan-result.
    if response.status_code == 202:
        assert result.status == "queued"
    # Оба пути ведут в один CommandService, но метка в очереди обязана остаться
    # честной: ручной скан из настроек — не повтор после разлогина.
    assert enqueue.await_args.kwargs["reason"] == "operator_scan_now"
