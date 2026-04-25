# -*- coding: utf-8 -*-
"""Тесты runtime-логики disable worker."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


# Проверяем, что disable worker делает fallback-чтение toggle, если find_toggle_cell вернул unknown.
@pytest.mark.asyncio
async def test_execute_disable_single_falls_back_to_read_toggle_state(monkeypatch):
    import run_disable_worker

    client = AsyncMock()
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 100,
            "cell_y": 200,
            "aria_checked": "unknown",
        }
    )
    client.read_toggle_state = AsyncMock(return_value="true")
    client.toggle_ad = AsyncMock(return_value={"success": True, "final_state": "false"})
    client.wait_for_toggle_confirmation = AsyncMock(
        return_value={
            "success": True,
            "message": "Переключатель подтверждён: false",
            "final_aria_checked": "false",
            "reads_matched": 1,
        }
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(run_disable_worker.asyncio, "sleep", _no_sleep)

    success, message = await run_disable_worker._execute_disable_single(
        client,
        "120246285103310334",
        reset_table_before_search=False,
    )

    assert success is True
    assert message == "Клик по выключению выполнен, toggle показал OFF"
    client.read_toggle_state.assert_awaited_once_with("120246285103310334")
    client.toggle_ad.assert_awaited_once_with("120246285103310334", target_state=False)


# Проверяем, что disable-flow удерживает общую блокировку браузера.
@pytest.mark.asyncio
async def test_execute_disable_single_locked_uses_common_browser_lock(monkeypatch):
    import run_disable_worker

    lock_calls = []

    @asynccontextmanager
    async def fake_lock(**kwargs):
        lock_calls.append(kwargs)
        yield

    execute = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(run_disable_worker, "acquire_browser_lock", fake_lock)
    monkeypatch.setattr(run_disable_worker, "_execute_disable_single", execute)

    result = await run_disable_worker._execute_disable_single_locked(
        AsyncMock(),
        "120246285103310334",
    )

    assert result == (True, "ok")
    assert lock_calls[0]["owner"] == "disable-worker"
    assert (
        lock_calls[0]["timeout_seconds"] == run_disable_worker.DISABLE_BROWSER_LOCK_TIMEOUT_SECONDS
    )
    execute.assert_awaited_once()


# Проверяем, что cleanup disable worker не останавливает общий Vision-профиль.
@pytest.mark.asyncio
async def test_close_disable_runtime_disconnects_without_stopping_profile():
    import run_disable_worker

    client = SimpleNamespace(
        disconnect_browser=AsyncMock(),
        stop_browser=AsyncMock(),
        close=AsyncMock(),
    )

    await run_disable_worker._close_disable_runtime_resources(client)

    client.disconnect_browser.assert_awaited_once()
    client.stop_browser.assert_not_awaited()
    client.close.assert_awaited_once()


# Проверяем, что batch не ждёт повторный scan, если toggle уже OFF.
@pytest.mark.asyncio
async def test_execute_disable_batch_accepts_already_off_toggle_without_delivery_scan(monkeypatch):
    import run_disable_worker

    client = AsyncMock()
    client.reset_scroll = AsyncMock()
    client.get_visible_row_ids = AsyncMock(return_value=["120246605325150334"])
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 113,
            "cell_y": 353,
            "aria_checked": "false",
        }
    )
    client.wait_for_toggle_confirmation = AsyncMock(
        return_value={"success": True, "final_aria_checked": "false"}
    )
    client.run_scan_cycle = AsyncMock()

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(run_disable_worker.asyncio, "sleep", _no_sleep)

    task = SimpleNamespace(
        id="task-001",
        fb_ad=SimpleNamespace(fb_ad_id="120246605325150334"),
    )

    result = await run_disable_worker._execute_disable_batch(client, [task])

    assert result == {
        "task-001": (
            True,
            "Объявление уже отключено (aria-checked=false). "
            "Финальный delivery_status проверит следующий скан.",
        )
    }
    client.wait_for_toggle_confirmation.assert_not_awaited()
    client.run_scan_cycle.assert_not_called()


# Проверяем, что batch считает задачу выполненной сразу после подтверждённого OFF toggle.
@pytest.mark.asyncio
async def test_execute_disable_batch_succeeds_after_toggle_off_without_delivery_scan(
    monkeypatch,
):
    import run_disable_worker

    client = AsyncMock()
    client.reset_scroll = AsyncMock()
    client.get_visible_row_ids = AsyncMock(return_value=["120246605325150334"])
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 113,
            "cell_y": 353,
            "aria_checked": "true",
        }
    )
    client.toggle_ad = AsyncMock(return_value={"success": True, "final_state": "false"})
    client.wait_for_toggle_confirmation = AsyncMock()
    client.run_scan_cycle = AsyncMock()

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(run_disable_worker.asyncio, "sleep", _no_sleep)

    task = SimpleNamespace(
        id="task-001",
        fb_ad=SimpleNamespace(fb_ad_id="120246605325150334"),
    )

    result = await run_disable_worker._execute_disable_batch(client, [task])

    assert result == {
        "task-001": (
            True,
            "Клик по выключению выполнен, toggle показал OFF",
        )
    }
    client.toggle_ad.assert_awaited_once_with("120246605325150334", target_state=False)
    client.wait_for_toggle_confirmation.assert_not_awaited()
    client.run_scan_cycle.assert_not_called()


# Проверяем, что ошибка execute_disable не теряет задачу — mark_retrying вызывается, задача остаётся в очереди для reconcile
@pytest.mark.asyncio
async def test_disable_worker_loop_execute_error_calls_mark_retrying():
    from apps.disable_worker.main import disable_worker_loop

    shutdown = asyncio.Event()
    call_count = 0

    task = MagicMock()
    task.id = "task-001"
    task.fb_ad_id = "120246283878900334"
    task.attempt_count = 1
    task.max_attempts = 10

    async def fake_claim():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return task
        shutdown.set()
        return None

    async def fake_execute(_fb_ad_id):
        return False, "connection refused"

    mark_retrying = AsyncMock()
    mark_succeeded = AsyncMock()

    await disable_worker_loop(
        claim_next_task=fake_claim,
        execute_disable=fake_execute,
        mark_succeeded=mark_succeeded,
        mark_retrying=mark_retrying,
        poll_interval_seconds=0,
        shutdown_event=shutdown,
    )

    # Неуспешный execute_disable должен вызвать mark_retrying, а не потерять задачу
    mark_retrying.assert_awaited_once()
    retried_task_id = mark_retrying.call_args[0][0]
    assert retried_task_id == "task-001"
    mark_succeeded.assert_not_awaited()


# Проверяем, что runtime-ошибка batch переводит задачи в retry и пробрасывает ошибку для переподключения
@pytest.mark.asyncio
async def test_disable_worker_loop_batch_runtime_error_retries_and_reconnects():
    from apps.disable_worker.main import BrowserOperationRuntimeError, disable_worker_loop

    task = MagicMock()
    task.id = "task-batch-001"
    task.fb_ad_id = "120246606041550334"
    task.attempt_count = 1
    task.max_attempts = 10

    mark_retrying = AsyncMock()
    mark_succeeded = AsyncMock()

    async def claim_task_batch(_limit):
        return [task]

    async def execute_disable_batch(_tasks):
        raise RuntimeError("page.evaluate: Target page, context or browser has been closed")

    with pytest.raises(BrowserOperationRuntimeError):
        await disable_worker_loop(
            claim_next_task=AsyncMock(return_value=None),
            claim_task_batch=claim_task_batch,
            execute_disable=AsyncMock(),
            execute_disable_batch=execute_disable_batch,
            mark_succeeded=mark_succeeded,
            mark_retrying=mark_retrying,
            poll_interval_seconds=0,
            shutdown_event=asyncio.Event(),
        )

    mark_retrying.assert_awaited_once()
    assert mark_retrying.call_args.args[0] == "task-batch-001"
    assert "Браузерная операция завершилась ошибкой" in mark_retrying.call_args.args[1]
    mark_succeeded.assert_not_awaited()
