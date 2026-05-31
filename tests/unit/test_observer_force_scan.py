# -*- coding: utf-8 -*-
"""Unit-тесты прерываемого sleep observer'а (_wait_interruptible).

Контекст: scan-now публикует fb_agent:observer:trigger; observer должен прервать
sleep между циклами и сканировать немедленно, а не ждать полный интервал (~90с).
Раньше sleep ждал только shutdown_event — trigger во время сна не будил его.
"""

from __future__ import annotations

import asyncio

import pytest

from apps.observer_worker.main import _wait_interruptible


# trigger во время сна будит _wait_interruptible задолго до timeout
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_wait_interruptible_wakes_on_trigger() -> None:
    shutdown = asyncio.Event()
    trigger = asyncio.Event()

    async def _fire() -> None:
        await asyncio.sleep(0.05)
        trigger.set()

    loop = asyncio.get_running_loop()
    asyncio.create_task(_fire())
    t0 = loop.time()
    await _wait_interruptible(shutdown, trigger, seconds=5.0)
    elapsed = loop.time() - t0

    assert trigger.is_set()
    # Проснулись по триггеру (~0.05с), а не отсидели весь timeout 5с.
    assert elapsed < 1.0, f"sleep не прервался по trigger: {elapsed:.2f}s"


# shutdown тоже будит sleep раньше timeout
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_wait_interruptible_wakes_on_shutdown() -> None:
    shutdown = asyncio.Event()
    trigger = asyncio.Event()

    async def _fire() -> None:
        await asyncio.sleep(0.05)
        shutdown.set()

    loop = asyncio.get_running_loop()
    asyncio.create_task(_fire())
    t0 = loop.time()
    await _wait_interruptible(shutdown, trigger, seconds=5.0)
    elapsed = loop.time() - t0

    assert shutdown.is_set()
    assert elapsed < 1.0, f"sleep не прервался по shutdown: {elapsed:.2f}s"


# без событий — спим весь timeout и не падаем
@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_wait_interruptible_times_out_cleanly() -> None:
    shutdown = asyncio.Event()
    trigger = asyncio.Event()

    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await _wait_interruptible(shutdown, trigger, seconds=0.2)
    elapsed = loop.time() - t0

    assert not shutdown.is_set()
    assert not trigger.is_set()
    # Отсидели близко к полному timeout (без раннего выхода).
    assert elapsed >= 0.18, f"timeout сработал слишком рано: {elapsed:.2f}s"
