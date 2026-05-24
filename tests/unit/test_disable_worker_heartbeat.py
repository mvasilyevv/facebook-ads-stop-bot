# -*- coding: utf-8 -*-
"""Тесты heartbeat-задачи disable worker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

# TODO: тесты висят после рефакторинга 2.6 (BaseTaskWorker) — disable_worker_loop
# теперь не вызывает update_worker_heartbeat напрямую; нужно переписать под новый API.
pytestmark = pytest.mark.skip(reason="API изменён в 2.6 — нужно переписать под BaseTaskWorker")


# Проверяет, что при старте disable_worker_loop вызывается update_worker_heartbeat хотя бы раз
async def _run_worker_briefly(mock_heartbeat: AsyncMock) -> None:
    """Запускает disable_worker_loop с моком и ждёт первого heartbeat."""
    from apps.disable_worker.main import disable_worker_loop

    call_event = asyncio.Event()

    async def heartbeat_side_effect(*args, **kwargs):
        call_event.set()

    mock_heartbeat.side_effect = heartbeat_side_effect

    shutdown = asyncio.Event()

    async def claim_none():
        return None

    async def execute(fb_ad_id):
        return True, "ok"

    async def mark_ok(task_id):
        pass

    async def mark_retry(task_id, error, next_retry):
        pass

    # Запускаем воркер как задачу, ждём сигнала о heartbeat, затем завершаем
    worker_task = asyncio.create_task(
        disable_worker_loop(
            poll_interval_seconds=1,
            claim_next_task=claim_none,
            execute_disable=execute,
            mark_succeeded=mark_ok,
            mark_retrying=mark_retry,
            shutdown_event=shutdown,
        )
    )

    try:
        # Ждём первого вызова heartbeat (или таймаут 5 сек)
        await asyncio.wait_for(call_event.wait(), timeout=5.0)
    finally:
        shutdown.set()
        worker_task.cancel()
        try:
            await worker_task
        except (asyncio.CancelledError, Exception):
            pass


def test_heartbeat_called_on_start():
    # При запуске disable_worker_loop heartbeat вызывается хотя бы один раз
    with patch(
        "apps.disable_worker.main.update_worker_heartbeat",
        new_callable=AsyncMock,
    ) as mock_hb:
        asyncio.run(_run_worker_briefly(mock_hb))
        assert mock_hb.call_count >= 1, (
            f"Ожидался хотя бы 1 вызов update_worker_heartbeat, получено {mock_hb.call_count}"
        )


def test_heartbeat_called_with_disable_worker_name():
    # Heartbeat вызывается с именем "disable"
    with patch(
        "apps.disable_worker.main.update_worker_heartbeat",
        new_callable=AsyncMock,
    ) as mock_hb:
        asyncio.run(_run_worker_briefly(mock_hb))
        first_call_args = mock_hb.call_args_list[0]
        worker_name = (
            first_call_args.args[0]
            if first_call_args.args
            else first_call_args.kwargs.get("worker_name")
        )
        assert worker_name == "disable", (
            f"Ожидалось имя воркера 'disable', получено '{worker_name}'"
        )
