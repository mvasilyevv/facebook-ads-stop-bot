# -*- coding: utf-8 -*-
"""Тесты runtime-логики enable worker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest


# Проверяем, что enable worker включает видимое объявление через основной путь без worker-level fallback.
@pytest.mark.asyncio
async def test_execute_enable_single_enables_visible_toggle(monkeypatch):
    import run_enable_worker

    client = AsyncMock()
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 120,
            "cell_y": 240,
            "aria_checked": "false",
        }
    )
    client.toggle_ad = AsyncMock(return_value={"success": True, "final_state": "true"})
    client.wait_for_toggle_confirmation = AsyncMock(
        return_value={
            "success": True,
            "message": "Переключатель подтверждён: true",
            "final_aria_checked": "true",
            "reads_matched": 2,
        }
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(run_enable_worker.asyncio, "sleep", _no_sleep)

    success, message = await run_enable_worker._execute_enable_single(
        client,
        "120246283878900334",
    )

    assert success is True
    assert message == "Объявление включено: переключатель подтвердил состояние ON"
    client.find_toggle_cell.assert_awaited_once_with("120246283878900334", reset_to_top=True)
    client.toggle_ad.assert_awaited_once_with("120246283878900334", target_state=True)


# Проверяем, что enable-flow удерживает общую блокировку браузера.
@pytest.mark.asyncio
async def test_execute_enable_single_locked_uses_common_browser_lock(monkeypatch):
    import run_enable_worker

    lock_calls = []

    @asynccontextmanager
    async def fake_lock(**kwargs):
        lock_calls.append(kwargs)
        yield

    execute = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(run_enable_worker, "acquire_browser_lock", fake_lock)
    monkeypatch.setattr(run_enable_worker, "_execute_enable_single", execute)

    result = await run_enable_worker._execute_enable_single_locked(
        AsyncMock(),
        "120246283878900334",
    )

    assert result == (True, "ok")
    assert lock_calls[0]["owner"] == "enable-worker"
    assert lock_calls[0]["timeout_seconds"] == run_enable_worker.ENABLE_BROWSER_LOCK_TIMEOUT_SECONDS
    execute.assert_awaited_once()


# Проверяем, что enable worker не кликает по toggle если объявление уже включено
@pytest.mark.asyncio
async def test_execute_enable_single_skips_already_enabled(monkeypatch):
    import run_enable_worker

    client = AsyncMock()
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 120,
            "cell_y": 240,
            "aria_checked": "true",
        }
    )
    monkeypatch.setattr(run_enable_worker.asyncio, "sleep", AsyncMock())

    success, message = await run_enable_worker._execute_enable_single(client, "120246283878900334")

    assert success is True
    assert message == "Объявление уже включено"
    client.toggle_ad.assert_not_awaited()


# Проверяем, что задача помечается FAILED когда исчерпаны все попытки
@pytest.mark.asyncio
async def test_process_enable_task_result_marks_failed_on_max_attempts(monkeypatch):
    import run_enable_worker

    task = MagicMock()
    task.id = 1
    task.attempt_count = 5
    task.max_attempts = 5
    task.fb_ad = MagicMock(fb_ad_id="120246283878900334", ad_name="Test Ad")
    task.requested_by_username = "user"
    task.recommendation_event_id = None

    mark_failed_calls = []

    async def _mark_failed(task_id, error):
        mark_failed_calls.append((task_id, error))

    monkeypatch.setattr(run_enable_worker, "mark_failed", _mark_failed)
    monkeypatch.setattr(run_enable_worker, "mark_succeeded", AsyncMock())
    monkeypatch.setattr(run_enable_worker, "mark_retrying", AsyncMock())

    await run_enable_worker._process_enable_task_result(
        task=task,
        success=False,
        message="Ошибка toggle",
        tg_client=None,
        tg_chat_id="",
        send_completion_callback=None,
    )

    assert len(mark_failed_calls) == 1
    assert mark_failed_calls[0][0] == 1


# Проверяем, что неудачный toggle_ad приводит к (False, message) без исключения
@pytest.mark.asyncio
async def test_execute_enable_single_returns_false_on_toggle_failure(monkeypatch):
    import run_enable_worker

    client = AsyncMock()
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 120,
            "cell_y": 240,
            "aria_checked": "false",
        }
    )
    client.toggle_ad = AsyncMock(return_value={"success": False, "final_state": "false"})
    monkeypatch.setattr(run_enable_worker.asyncio, "sleep", AsyncMock())

    success, message = await run_enable_worker._execute_enable_single(client, "120246283878900334")

    assert success is False
    assert "toggle" in message.lower() or "final_state" in message.lower()
