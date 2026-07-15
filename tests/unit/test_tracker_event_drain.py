"""Durable tracker drain behavior around targeted aggregate failures."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from apps.tracker_aggregator_worker import main as worker
from core.adset_pro.processing import ProcessResult


@pytest.mark.asyncio
async def test_targeted_aggregation_failure_is_durably_requeued(monkeypatch) -> None:
    occurred_at = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    result = ProcessResult(
        task_id=77,
        event_id=12,
        processed=True,
        attribution_status="matched_direct",
        fb_ad_id="238001",
        occurred_at=occurred_at,
    )
    monkeypatch.setattr(worker, "claim_event_tasks", AsyncMock(return_value=[77]))
    monkeypatch.setattr(worker, "process_event_task", AsyncMock(return_value=result))
    aggregate = AsyncMock(side_effect=RuntimeError("aggregate boom"))
    repair = AsyncMock()
    monkeypatch.setattr(worker, "aggregate_affected_event", aggregate)
    monkeypatch.setattr(worker, "requeue_aggregation_repair", repair)
    monkeypatch.setattr(worker, "_refresh_queue_metrics", AsyncMock())
    monkeypatch.setattr(worker, "_publish", AsyncMock())

    engine = object()
    drained = await worker.drain_event_tasks(engine, object())

    assert drained == 1
    aggregate.assert_awaited_once_with(
        engine,
        occurred_at=occurred_at,
        fb_ad_id="238001",
    )
    repair.assert_awaited_once_with(
        engine,
        task_id=77,
        error="aggregate boom",
    )
