# -*- coding: utf-8 -*-
"""Анти-регресс: task_loop пробрасывает alert_ctx в process_one_task.

Без проброса CRITICAL-алерт auto-stop никогда не сработает в проде (тихий money-баг).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta


# task_loop передаёт alert_ctx дальше в process_one_task
@pytest.mark.asyncio
async def test_task_loop_forwards_alert_ctx(monkeypatch) -> None:
    stop = asyncio.Event()
    ctx = meta.AutostopAlertContext(engine=object())
    task = SimpleNamespace(id=1, task_type="meta_api_mutation", requested_by="bot_auto_stop")

    monkeypatch.setattr(
        meta,
        "claim_pending_task",
        AsyncMock(return_value=SimpleNamespace(task=task, queue_empty=False)),
    )

    async def _fake_process(engine, t, *, client, redis_client=None, alert_ctx=None):
        # фиксируем переданный alert_ctx и останавливаем цикл
        _fake_process.seen = alert_ctx
        stop.set()

    _fake_process.seen = "NOTSET"
    monkeypatch.setattr(meta, "process_one_task", _fake_process)

    await meta.task_loop(
        object(), stop, client=AsyncMock(), redis_client=AsyncMock(), alert_ctx=ctx
    )

    assert _fake_process.seen is ctx
