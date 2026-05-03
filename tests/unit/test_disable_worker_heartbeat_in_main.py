# -*- coding: utf-8 -*-
"""Тесты heartbeat disable worker в run_disable_worker.main()."""

from __future__ import annotations

import asyncio

import pytest


# Проверяем, что _heartbeat_loop вызывает update_worker_heartbeat с именем "disable".
@pytest.mark.asyncio
async def test_heartbeat_loop_calls_update_with_disable(monkeypatch):
    from apps.disable_worker import main as disable_main

    calls: list[tuple] = []

    async def fake_heartbeat(worker_name, *, status="running", message=None):
        calls.append((worker_name, status, message))

    monkeypatch.setattr(disable_main, "update_worker_heartbeat", fake_heartbeat)

    # Запускаем _heartbeat_loop с очень коротким интервалом и отменяем после первого вызова
    sleep_called = asyncio.Event()

    async def fake_sleep(seconds):
        sleep_called.set()
        # Блокируемся до отмены задачи
        await asyncio.shield(asyncio.get_event_loop().create_future())

    monkeypatch.setattr(disable_main.asyncio, "sleep", fake_sleep)

    status_ref: list[str] = ["idle"]
    message_ref: list[str | None] = [None]

    task = asyncio.create_task(disable_main._heartbeat_loop(status_ref, message_ref))
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
    assert worker_name == "disable"


# Проверяем, что status_ref и message_ref передаются в heartbeat.
@pytest.mark.asyncio
async def test_heartbeat_loop_passes_status_and_message(monkeypatch):
    from apps.disable_worker import main as disable_main

    calls: list[tuple] = []

    async def fake_heartbeat(worker_name, *, status="running", message=None):
        calls.append((worker_name, status, message))

    monkeypatch.setattr(disable_main, "update_worker_heartbeat", fake_heartbeat)

    sleep_called = asyncio.Event()

    async def fake_sleep(seconds):
        sleep_called.set()
        await asyncio.shield(asyncio.get_event_loop().create_future())

    monkeypatch.setattr(disable_main.asyncio, "sleep", fake_sleep)

    status_ref: list[str] = ["busy"]
    message_ref: list[str | None] = ["Отключаю объявление 123"]

    task = asyncio.create_task(disable_main._heartbeat_loop(status_ref, message_ref))
    try:
        await asyncio.wait_for(sleep_called.wait(), timeout=2.0)
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert calls[0] == ("disable", "busy", "Отключаю объявление 123")


# Проверяем, что при пустой очереди disable_worker_loop НЕ создаёт собственный heartbeat,
# когда внешние status_ref/message_ref уже переданы.
@pytest.mark.asyncio
async def test_disable_worker_loop_uses_external_refs(monkeypatch):
    """Если status_ref и message_ref переданы снаружи — внутренний heartbeat не запускается."""
    from apps.disable_worker import main as disable_main

    heartbeat_create_count = 0
    original_create_task = asyncio.create_task

    def counting_create_task(coro, **kw):
        nonlocal heartbeat_create_count
        # Считаем только задачи с именем _heartbeat_loop
        if hasattr(coro, "cr_code") and coro.cr_code.co_name == "_heartbeat_loop":
            heartbeat_create_count += 1
        return original_create_task(coro, **kw)

    monkeypatch.setattr(disable_main.asyncio, "create_task", counting_create_task)

    # Патчим heartbeat, чтобы не ходить в БД
    async def fake_heartbeat(worker_name, *, status="running", message=None):
        pass

    monkeypatch.setattr(disable_main, "update_worker_heartbeat", fake_heartbeat)

    # Очередь всегда пустая
    async def empty_claim():
        return None

    shutdown = asyncio.Event()
    status_ref: list[str] = ["idle"]
    message_ref: list[str | None] = [None]

    # Запускаем loop на очень короткое время
    async def run_loop():
        await disable_main.disable_worker_loop(
            poll_interval_seconds=0,
            claim_next_task=empty_claim,
            execute_disable=None,
            mark_succeeded=None,
            mark_retrying=None,
            shutdown_event=shutdown,
            status_ref=status_ref,
            message_ref=message_ref,
        )

    shutdown.set()
    await run_loop()

    # Внешние refs переданы — собственный heartbeat создаваться не должен
    assert heartbeat_create_count == 0
