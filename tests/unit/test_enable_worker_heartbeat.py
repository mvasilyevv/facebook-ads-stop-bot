# -*- coding: utf-8 -*-
"""Тесты heartbeat enable worker."""

from __future__ import annotations

import asyncio

import pytest


# Проверяем, что _heartbeat_loop вызывает update_worker_heartbeat с именем "enable".
@pytest.mark.asyncio
async def test_heartbeat_loop_calls_update_with_enable(monkeypatch):
    import run_enable_worker

    calls: list[tuple] = []

    async def fake_heartbeat(worker_name, *, status="running", message=None):
        calls.append((worker_name, status, message))

    monkeypatch.setattr(run_enable_worker, "update_worker_heartbeat", fake_heartbeat)

    # Запускаем _heartbeat_loop с очень коротким интервалом и отменяем после первого вызова
    sleep_called = asyncio.Event()

    async def fake_sleep(seconds):
        sleep_called.set()
        # Блокируемся до отмены задачи
        await asyncio.shield(asyncio.get_event_loop().create_future())

    monkeypatch.setattr(run_enable_worker.asyncio, "sleep", fake_sleep)

    status_ref: list[str] = ["idle"]
    message_ref: list[str | None] = [None]

    task = asyncio.create_task(run_enable_worker._heartbeat_loop(status_ref, message_ref))
    try:
        await asyncio.wait_for(sleep_called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert len(calls) >= 1
    worker_name, status, message = calls[0]
    assert worker_name == "enable"


# Проверяем, что status_ref и message_ref передаются в heartbeat.
@pytest.mark.asyncio
async def test_heartbeat_loop_passes_status_and_message(monkeypatch):
    import run_enable_worker

    calls: list[tuple] = []

    async def fake_heartbeat(worker_name, *, status="running", message=None):
        calls.append((worker_name, status, message))

    monkeypatch.setattr(run_enable_worker, "update_worker_heartbeat", fake_heartbeat)

    sleep_called = asyncio.Event()

    async def fake_sleep(seconds):
        sleep_called.set()
        await asyncio.shield(asyncio.get_event_loop().create_future())

    monkeypatch.setattr(run_enable_worker.asyncio, "sleep", fake_sleep)

    status_ref: list[str] = ["busy"]
    message_ref: list[str | None] = ["Задача 42"]

    task = asyncio.create_task(run_enable_worker._heartbeat_loop(status_ref, message_ref))
    try:
        await asyncio.wait_for(sleep_called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert calls[0] == ("enable", "busy", "Задача 42")
