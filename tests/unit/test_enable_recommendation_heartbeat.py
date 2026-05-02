# -*- coding: utf-8 -*-
"""Тесты heartbeat enable_recommendation worker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


# Проверяем, что _heartbeat_loop вызывает update_worker_heartbeat с именем "enable_recommendation".
@pytest.mark.asyncio
async def test_heartbeat_loop_calls_update_with_enable_recommendation(monkeypatch):
    from apps.enable_recommendation_worker import main as worker_main

    calls: list[tuple] = []

    async def fake_heartbeat(worker_name, *, status="running", message=None):
        calls.append((worker_name, status, message))

    monkeypatch.setattr(worker_main, "update_worker_heartbeat", fake_heartbeat)

    sleep_called = asyncio.Event()

    async def fake_sleep(seconds):
        sleep_called.set()
        await asyncio.shield(asyncio.get_event_loop().create_future())

    monkeypatch.setattr(worker_main.asyncio, "sleep", fake_sleep)

    task = asyncio.create_task(worker_main._heartbeat_loop())
    try:
        await asyncio.wait_for(sleep_called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(calls) >= 1
    worker_name, status, _ = calls[0]
    assert worker_name == "enable_recommendation"


# Проверяем, что recommendation_worker_loop запускает heartbeat-задачу и отменяет её при выходе.
@pytest.mark.asyncio
async def test_recommendation_worker_loop_starts_and_cancels_heartbeat(monkeypatch):
    from apps.enable_recommendation_worker import main as worker_main

    heartbeat_tasks: list = []
    original_create_task = asyncio.create_task

    def fake_create_task(coro, **kwargs):
        t = original_create_task(coro, **kwargs)
        heartbeat_tasks.append(t)
        return t

    monkeypatch.setattr(worker_main.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(worker_main, "update_worker_heartbeat", AsyncMock())

    cycle_count = 0

    async def fake_cycle():
        nonlocal cycle_count
        cycle_count += 1
        return 0

    shutdown = asyncio.Event()
    shutdown.set()  # Немедленно останавливаем

    await worker_main.recommendation_worker_loop(
        shutdown_event=shutdown,
        process_cycle=fake_cycle,
    )

    # Heartbeat-задача должна быть отменена (ждём завершения отмены)
    try:
        await heartbeat_tasks[0]
    except asyncio.CancelledError:
        pass
    assert heartbeat_tasks[0].done()
