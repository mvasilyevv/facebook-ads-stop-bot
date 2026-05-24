# -*- coding: utf-8 -*-
"""Проверяет, что disable_worker подтверждает фактическое выключение тумблера
после клика, чтобы поймать откат до следующего скана observer'а.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


# disable_worker подтверждает фактическое выключение тумблера после клика:
# wait_for_toggle_confirmation отрабатывает и его success влияет на итог
@pytest.mark.asyncio
async def test_execute_disable_single_marks_failure_when_toggle_reverts(monkeypatch):
    import run_disable_worker

    client = AsyncMock()
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 100,
            "cell_y": 200,
            "aria_checked": "true",
        }
    )
    client.toggle_ad = AsyncMock(return_value={"success": True, "final_state": "false"})
    # Тумблер откатился: повторное чтение показывает true.
    client.wait_for_toggle_confirmation = AsyncMock(
        return_value={
            "success": False,
            "message": "Тумблер откатился обратно в ON",
            "final_aria_checked": "true",
            "reads_matched": 0,
        }
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(run_disable_worker.asyncio, "sleep", _no_sleep)

    success, message = await run_disable_worker._execute_disable_single(
        client,
        "120246285103310334",
    )

    assert success is False
    assert "откатился" in message or "ON" in message or "не подтвердил" in message
    client.wait_for_toggle_confirmation.assert_awaited_once()


# Успешное подтверждение → mark_succeeded возможно (флоу возвращает success=True)
@pytest.mark.asyncio
async def test_execute_disable_single_returns_success_on_confirmed_off(monkeypatch):
    import run_disable_worker

    client = AsyncMock()
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 100,
            "cell_y": 200,
            "aria_checked": "true",
        }
    )
    client.toggle_ad = AsyncMock(return_value={"success": True, "final_state": "false"})
    client.wait_for_toggle_confirmation = AsyncMock(
        return_value={
            "success": True,
            "message": "Тумблер OFF подтверждён",
            "final_aria_checked": "false",
            "reads_matched": 2,
        }
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(run_disable_worker.asyncio, "sleep", _no_sleep)

    success, message = await run_disable_worker._execute_disable_single(
        client,
        "120246285103310334",
    )

    assert success is True
    assert "OFF" in message
    client.wait_for_toggle_confirmation.assert_awaited_once()
    # Параметры вызова — expected_checked="false", required_reads >= 2
    call_kwargs = client.wait_for_toggle_confirmation.call_args.kwargs
    assert call_kwargs.get("expected_checked") == "false"
    assert call_kwargs.get("required_reads") >= 1


# Batch-flow НЕ вызывает wait_for_toggle_confirmation (verify_after_click=False)
@pytest.mark.asyncio
async def test_execute_disable_single_skips_verify_when_batch_flag(monkeypatch):
    import run_disable_worker

    client = AsyncMock()
    client.find_toggle_cell = AsyncMock(
        return_value={
            "found": True,
            "cell_x": 100,
            "cell_y": 200,
            "aria_checked": "true",
        }
    )
    client.toggle_ad = AsyncMock(return_value={"success": True, "final_state": "false"})
    client.wait_for_toggle_confirmation = AsyncMock()

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(run_disable_worker.asyncio, "sleep", _no_sleep)

    success, message = await run_disable_worker._execute_disable_single(
        client,
        "120246285103310334",
        verify_after_click=False,
    )

    assert success is True
    assert message == "Клик по выключению выполнен, toggle показал OFF"
    client.wait_for_toggle_confirmation.assert_not_awaited()
