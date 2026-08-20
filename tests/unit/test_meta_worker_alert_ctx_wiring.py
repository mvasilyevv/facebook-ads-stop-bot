# -*- coding: utf-8 -*-
"""Анти-регресс: task_loop пробрасывает alert_ctx в process_one_task.

Без проброса CRITICAL-алерт auto-stop никогда не сработает в проде (тихий money-баг).
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

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
                browser_session_id="vision-session-1",
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
    monkeypatch.setattr(meta, "record_worker_heartbeat", AsyncMock())
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
    mark_db_poll_success = Mock()
    monkeypatch.setattr(meta, "mark_worker_db_poll_success", mark_db_poll_success)
    durable_heartbeat = AsyncMock()
    monkeypatch.setattr(meta, "record_worker_heartbeat", durable_heartbeat)

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
    engine_stub = object()

    await meta.task_loop(engine_stub, stop, client=client, alert_ctx=None)

    claim.assert_awaited_once()
    mark_db_poll_success.assert_called_once_with(meta.WORKER_NAME)
    durable_heartbeat.assert_awaited_once_with(engine_stub, meta.WORKER_NAME, poll_success=True)
    process.assert_not_awaited()
    client.operation_authority.assert_not_called()
    timezone_refresh.assert_awaited_once()
    escalation.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_loop_does_not_mark_db_poll_when_claim_fails(monkeypatch) -> None:
    stop = asyncio.Event()
    mark_db_poll_success = Mock()
    monkeypatch.setattr(meta, "mark_worker_db_poll_success", mark_db_poll_success)
    durable_heartbeat = AsyncMock()
    monkeypatch.setattr(meta, "record_worker_heartbeat", durable_heartbeat)

    async def _failed_claim(*_args, **_kwargs):
        stop.set()
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        meta,
        "claim_browser_ready_mutation_task",
        AsyncMock(side_effect=_failed_claim),
    )

    await meta.task_loop(object(), stop, client=MagicMock(), alert_ctx=None)

    mark_db_poll_success.assert_not_called()
    durable_heartbeat.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_loop_survives_a_liveness_write_failure_on_the_money_lane(
    monkeypatch,
) -> None:
    """Review issue #176 Б1: on autopause (money lane), an unhandled exception
    here means auto-stop is claimed but never executed while the ad keeps
    spending. A raw connection failure from record_worker_heartbeat must not
    stop task_loop from running an already-claimed, leased task. Fails on
    8fe0696e, where this call sat outside any try/except.
    """
    stop = asyncio.Event()
    task = SimpleNamespace(
        id=7,
        task_type="meta_api_mutation",
        requested_by="bot_auto_stop",
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000007"),
        lease_token=1,
    )
    monkeypatch.setattr(
        meta,
        "claim_browser_ready_mutation_task",
        AsyncMock(
            return_value=SimpleNamespace(
                task=task,
                queue_empty=False,
                browser_profile_id="vision-profile-1",
                browser_session_id="vision-session-1",
                browser_readiness_generation=1,
            )
        ),
    )
    monkeypatch.setattr(
        meta,
        "record_worker_heartbeat",
        AsyncMock(side_effect=ConnectionRefusedError("connection refused")),
    )
    processed: list[int] = []

    async def _process(*_args, **_kwargs) -> None:
        processed.append(task.id)
        stop.set()

    monkeypatch.setattr(meta, "process_one_task", _process)
    client = MagicMock()
    client.operation_authority.return_value = nullcontext()

    await meta.task_loop(object(), stop, client=client, alert_ctx=None)

    assert processed == [task.id]
