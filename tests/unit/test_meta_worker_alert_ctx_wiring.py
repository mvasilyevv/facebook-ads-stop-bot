# -*- coding: utf-8 -*-
"""Анти-регресс: task_loop пробрасывает alert_ctx в process_one_task.

Без проброса CRITICAL-алерт auto-stop никогда не сработает в проде (тихий money-баг).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.meta_api_worker.main as meta
import core.meta_api.account_tz as account_tz
import core.meta_api.autostop_alert as autostop_alert


# task_loop передаёт alert_ctx дальше в process_one_task
@pytest.mark.asyncio
async def test_task_loop_forwards_alert_ctx(monkeypatch) -> None:
    stop = asyncio.Event()
    ctx = meta.AutostopAlertContext(engine=object())
    task = SimpleNamespace(
        id=1,
        task_type="meta_api_mutation",
        requested_by="bot_auto_stop",
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        lease_token=3,
    )

    monkeypatch.setattr(
        meta,
        "claim_browser_ready_mutation_task",
        AsyncMock(
            return_value=SimpleNamespace(
                task=task,
                queue_empty=False,
                browser_profile_id="vision-profile-1",
                browser_readiness_generation=8,
            )
        ),
    )

    async def _fake_process(engine, t, *, client, alert_ctx=None):
        # фиксируем переданный alert_ctx и останавливаем цикл
        _fake_process.seen = alert_ctx
        stop.set()

    _fake_process.seen = "NOTSET"
    monkeypatch.setattr(meta, "process_one_task", _fake_process)
    client = MagicMock()
    client.operation_authority.return_value = nullcontext()

    await meta.task_loop(object(), stop, client=client, alert_ctx=ctx)

    assert _fake_process.seen is ctx
    client.operation_authority.assert_called_once_with(
        caller="meta_api",
        task_id=1,
        lease_owner=task.lease_owner,
        lease_token=3,
        vision_profile_id="vision-profile-1",
        browser_readiness_generation=8,
    )


@pytest.mark.asyncio
async def test_task_loop_uses_durable_gate_without_blocking_housekeeping(
    monkeypatch,
) -> None:
    stop = asyncio.Event()

    async def _blocked_claim(*_args, **_kwargs):
        stop.set()
        return SimpleNamespace(
            task=None,
            queue_empty=True,
            browser_profile_id=None,
        )

    claim = AsyncMock(side_effect=_blocked_claim)
    monkeypatch.setattr(
        meta,
        "claim_browser_ready_mutation_task",
        claim,
    )
    timezone_refresh = AsyncMock(return_value=0)
    escalation = AsyncMock()
    monkeypatch.setattr(account_tz, "refresh_account_timezones", timezone_refresh)
    monkeypatch.setattr(
        autostop_alert,
        "escalate_undelivered_autostop_pauses",
        escalation,
    )

    process = AsyncMock()
    monkeypatch.setattr(meta, "process_one_task", process)
    client = MagicMock()

    await meta.task_loop(object(), stop, client=client, alert_ctx=None)

    claim.assert_awaited_once()
    process.assert_not_awaited()
    client.operation_authority.assert_not_called()
    timezone_refresh.assert_awaited_once()
    escalation.assert_awaited_once()
