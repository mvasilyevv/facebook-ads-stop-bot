"""Durable tracker drain behavior without a secondary aggregate projection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from apps.tracker_reconciliation_worker import main as worker
from core.adset_pro.processing import ProcessResult, TrackerTaskClaim


@pytest.mark.asyncio
async def test_successful_projection_needs_no_secondary_write(monkeypatch) -> None:
    occurred_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    claim = TrackerTaskClaim(
        task_id=77,
        lease_owner=uuid.UUID("11111111-1111-4111-8111-111111111111"),
        lease_token=2,
        lease_expires_at=datetime(2026, 6, 1, 12, 2, tzinfo=UTC),
        deadline_at=datetime(2026, 6, 1, 12, 2, tzinfo=UTC),
    )
    result = ProcessResult(
        task_id=77,
        event_id=12,
        processed=True,
        attribution_status="matched_direct",
        fb_ad_id="238001",
        occurred_at=occurred_at,
    )
    monkeypatch.setattr(worker, "claim_event_tasks", AsyncMock(return_value=[claim]))
    monkeypatch.setattr(worker, "process_event_task", AsyncMock(return_value=result))
    monkeypatch.setattr(worker, "_refresh_queue_metrics", AsyncMock())
    monkeypatch.setattr(worker, "enqueue_observer_scan", AsyncMock())

    engine = object()
    drained = await worker.drain_event_tasks(engine)

    assert drained == 1
