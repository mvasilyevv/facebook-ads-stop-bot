"""LISTEN/NOTIFY queue acceleration remains reconnectable and advisory-only."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from core.tasks.wakeup import TASK_QUEUE_NOTIFY_CHANNEL, TaskQueueWakeup


class _Connection:
    def __init__(self) -> None:
        self.listener: Callable[..., None] | None = None
        self.termination_listener: Callable[..., None] | None = None
        self.closed = False

    async def add_listener(self, channel: str, callback: Callable[..., None]) -> None:
        assert channel == TASK_QUEUE_NOTIFY_CHANNEL
        self.listener = callback

    async def remove_listener(self, channel: str, callback: Callable[..., None]) -> None:
        assert channel == TASK_QUEUE_NOTIFY_CHANNEL
        if self.listener == callback:
            self.listener = None

    def add_termination_listener(self, callback: Callable[..., None]) -> None:
        self.termination_listener = callback

    def remove_termination_listener(self, callback: Callable[..., None]) -> None:
        if self.termination_listener == callback:
            self.termination_listener = None

    async def close(self) -> None:
        self.closed = True

    def notify(self, payload: dict[str, Any]) -> None:
        assert self.listener is not None
        self.listener(
            self,
            123,
            TASK_QUEUE_NOTIFY_CHANNEL,
            json.dumps(payload),
        )

    def terminate(self) -> None:
        assert self.termination_listener is not None
        self.termination_listener(self)


@pytest.mark.asyncio
async def test_relevant_notification_wakes_without_waiting_for_poll_timeout() -> None:
    connection = _Connection()

    async def connect(**_kwargs):
        return connection

    wakeup = TaskQueueWakeup(
        "postgresql+asyncpg://user:password@db/test",
        task_type="meta_api_mutation",
        lanes=("money",),
        reconcile_seconds=10,
        connect=connect,
    )
    stop = asyncio.Event()
    listener_task = asyncio.create_task(wakeup.run(stop))
    await asyncio.wait_for(wakeup.ready.wait(), timeout=1)

    waiter = asyncio.create_task(wakeup.wait_for_work(stop))
    connection.notify({"task_type": "meta_api_mutation", "lane": "money", "task_id": 7})
    assert await asyncio.wait_for(waiter, timeout=0.2) is True

    stop.set()
    await asyncio.wait_for(listener_task, timeout=1)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_terminated_listener_reconnects_while_polling_stays_available() -> None:
    connections = [_Connection(), _Connection()]
    second_connected = asyncio.Event()
    calls = 0

    async def connect(**_kwargs):
        nonlocal calls
        connection = connections[calls]
        calls += 1
        if calls == 2:
            second_connected.set()
        return connection

    wakeup = TaskQueueWakeup(
        "postgresql+asyncpg://user:password@db/test",
        task_type="meta_api_mutation",
        lanes=("money",),
        reconcile_seconds=0.02,
        connect=connect,
    )
    stop = asyncio.Event()
    listener_task = asyncio.create_task(wakeup.run(stop))
    await asyncio.wait_for(wakeup.ready.wait(), timeout=1)

    connections[0].terminate()
    # Termination also wakes a DB reconciliation immediately.
    assert await asyncio.wait_for(wakeup.wait_for_work(stop), timeout=0.2) is True
    await asyncio.wait_for(second_connected.wait(), timeout=1)
    assert calls == 2

    # With no NOTIFY at all, timeout still returns "query PostgreSQL now".
    assert await asyncio.wait_for(wakeup.wait_for_work(stop), timeout=0.2) is True

    stop.set()
    await asyncio.wait_for(listener_task, timeout=1)


@pytest.mark.asyncio
async def test_shutdown_interrupts_waiter() -> None:
    async def unused_connect(**_kwargs):
        raise AssertionError("listener loop is not started in this test")

    wakeup = TaskQueueWakeup(
        "postgresql+asyncpg://user:password@db/test",
        task_type="meta_api_mutation",
        lanes=("money",),
        reconcile_seconds=10,
        connect=unused_connect,
    )
    stop = asyncio.Event()
    waiter = asyncio.create_task(wakeup.wait_for_work(stop))
    stop.set()
    assert await asyncio.wait_for(waiter, timeout=0.2) is False
