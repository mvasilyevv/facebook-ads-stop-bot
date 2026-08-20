from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.campaign_creator_worker import main as campaign_worker


@pytest.mark.asyncio
async def test_campaign_confirmed_is_counted_after_atomic_finalize(monkeypatch) -> None:
    atomic_finalize = AsyncMock(return_value=True)
    outcome = Mock()
    monkeypatch.setattr(campaign_worker, "_finalize_run_succeeded", atomic_finalize)
    monkeypatch.setattr(campaign_worker, "record_irreversible_task_outcome", outcome)
    campaign_worker._begin_process_metrics()
    task = SimpleNamespace(id=9)

    applied = await campaign_worker.finalize_run_succeeded(
        object(),
        "run-1",
        task=task,
        created_meta_ids={"campaigns": ["123"]},
        progress={"stage": "succeeded"},
    )

    assert applied is True
    atomic_finalize.assert_awaited_once()
    assert outcome.call_args.args[:3] == (
        "campaign_creator",
        "campaign_create",
        "CONFIRMED",
    )


@pytest.mark.asyncio
async def test_campaign_boundary_metric_precedes_external_rpc(monkeypatch) -> None:
    order: list[str] = []

    class _Control:
        async def begin_external(self, _operation: str) -> None:
            order.append("persisted-boundary")

    class _Delegate:
        async def execute_graph_call(self, **_kwargs: object) -> dict[str, bool]:
            order.append("external-rpc")
            return {"ok": True}

    monkeypatch.setattr(
        campaign_worker,
        "record_irreversible_safety_event",
        lambda *_args: order.append("boundary-metric"),
    )

    result = await campaign_worker._FencedGraphClient(  # noqa: SLF001
        _Delegate(),
        _Control(),
    ).execute_graph_call(method="POST", endpoint="/campaigns")

    assert result == {"ok": True}
    assert order == ["persisted-boundary", "boundary-metric", "external-rpc"]


@pytest.mark.asyncio
async def test_prometheus_liveness_is_refreshed_without_redis(monkeypatch) -> None:
    stop = asyncio.Event()
    metric = Mock(side_effect=lambda _worker_name: stop.set())
    monkeypatch.setattr(campaign_worker, "mark_worker_heartbeat", metric)
    durable_heartbeat = AsyncMock()
    monkeypatch.setattr(campaign_worker, "record_worker_heartbeat", durable_heartbeat)
    await campaign_worker.metrics_loop(stop, object())

    metric.assert_called_once_with(campaign_worker.WORKER_NAME)
    durable_heartbeat.assert_awaited_once()
