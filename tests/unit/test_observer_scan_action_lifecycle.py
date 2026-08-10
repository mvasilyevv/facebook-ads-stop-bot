from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.observer_worker.main as observer


@pytest.mark.asyncio
@pytest.mark.parametrize("scan_outcome", ["success", "empty"])
async def test_claimed_scan_persists_confirmed_action_outcome(
    monkeypatch,
    scan_outcome: str,
) -> None:
    engine = object()
    task = SimpleNamespace(
        id=1842,
        lease_owner=uuid.uuid4(),
        lease_token=7,
    )
    summary = {"outcome": scan_outcome, "rows_total": 3}
    run_scan = AsyncMock(return_value=summary)
    mark_succeeded = AsyncMock(return_value=True)
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(observer, "run_with_observer_scan_control", run_scan)
    monkeypatch.setattr(observer, "mark_succeeded", mark_succeeded)
    monkeypatch.setattr(observer, "mark_failed", mark_failed)

    returned = await observer._run_claimed_observer_scan(
        engine,
        task=task,
        gate=object(),
    )

    assert returned == summary
    mark_succeeded.assert_awaited_once_with(
        engine,
        task_id=1842,
        lease_owner=task.lease_owner,
        lease_token=7,
        result={
            "outcome": "CONFIRMED",
            "scan_outcome": scan_outcome,
            "rows_total": 3,
            "task_id": 1842,
        },
    )
    mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_incomplete_claimed_scan_persists_rejected_action_outcome(monkeypatch) -> None:
    engine = object()
    task = SimpleNamespace(
        id=1843,
        lease_owner=uuid.uuid4(),
        lease_token=8,
    )
    summary = {"outcome": "partial", "error": "row metrics incomplete"}
    monkeypatch.setattr(
        observer,
        "run_with_observer_scan_control",
        AsyncMock(return_value=summary),
    )
    mark_succeeded = AsyncMock(return_value=True)
    mark_failed = AsyncMock(return_value=True)
    monkeypatch.setattr(observer, "mark_succeeded", mark_succeeded)
    monkeypatch.setattr(observer, "mark_failed", mark_failed)

    returned = await observer._run_claimed_observer_scan(
        engine,
        task=task,
        gate=object(),
    )

    assert returned == summary
    mark_failed.assert_awaited_once_with(
        engine,
        task_id=1843,
        lease_owner=task.lease_owner,
        lease_token=8,
        error="observer scan finished without a complete snapshot: partial",
        result={
            "outcome": "REJECTED",
            "scan_outcome": "partial",
            "error": "row metrics incomplete",
            "task_id": 1843,
        },
    )
    mark_succeeded.assert_not_awaited()
