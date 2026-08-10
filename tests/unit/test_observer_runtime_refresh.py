"""Observer idle wait reconciles PostgreSQL work without a side channel."""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.observer_worker.main as worker


@pytest.mark.asyncio
async def test_sleep_returns_durable_scan_as_soon_as_claimed(monkeypatch) -> None:
    task = SimpleNamespace(id=42)
    claim = AsyncMock(side_effect=[None, None, task])
    monkeypatch.setattr(worker, "claim_observer_scan", claim)
    monkeypatch.setattr(worker, "OBSERVER_SCAN_POLL_SECONDS", 0.001)

    result = await worker._wait_for_durable_scan(
        object(),
        asyncio.Event(),
        worker_id=uuid.uuid4(),
        seconds=1,
    )

    assert result is task
    assert claim.await_count == 3


@pytest.mark.asyncio
async def test_sleep_stops_without_claim_after_shutdown(monkeypatch) -> None:
    claim = AsyncMock()
    monkeypatch.setattr(worker, "claim_observer_scan", claim)
    stop = asyncio.Event()
    stop.set()

    result = await worker._wait_for_durable_scan(
        object(),
        stop,
        worker_id=uuid.uuid4(),
        seconds=60,
    )

    assert result is None
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_transient_database_poll_error_does_not_lose_later_work(
    monkeypatch,
) -> None:
    task = SimpleNamespace(id=77)
    claim = AsyncMock(side_effect=[RuntimeError("postgres restart"), task])
    monkeypatch.setattr(worker, "claim_observer_scan", claim)
    monkeypatch.setattr(worker, "OBSERVER_SCAN_POLL_SECONDS", 0.001)

    result = await worker._wait_for_durable_scan(
        object(),
        asyncio.Event(),
        worker_id=uuid.uuid4(),
        seconds=1,
    )

    assert result is task
    assert claim.await_count == 2
