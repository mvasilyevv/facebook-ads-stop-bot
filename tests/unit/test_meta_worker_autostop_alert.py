# -*- coding: utf-8 -*-
"""Unit-тесты подключения autostop-алерта в meta_api_worker.process_one_task.

Сценарий: auto-stop pause_ad ловит «канал мёртв» (code=-2) → воркер фиксирует
durable CRITICAL event даже без Redis; не-autostop фейл алерт НЕ триггерит.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
from core.meta_api.errors import TemporaryError
from core.tasks.queue import Task


def _autostop_task(**over) -> Task:
    now = datetime.now(UTC)
    base = dict(
        id=42,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key="meta:pause_ad:42",
        payload={"mutation_kind": "pause_ad", "target_id": "123", "ad_account_id": "456"},
        attempt_count=5,
        max_attempts=72,
        requested_by="bot_auto_stop",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="money",
        priority=100,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        lease_token=2,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )
    base.update(over)
    return Task(**base)


@pytest.fixture(autouse=True)
def _fenced_external_boundary(monkeypatch) -> None:
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))


def _alert_ctx():
    return meta.AutostopAlertContext(engine=object())


# auto-stop pause_ad + code=-2 (канал мёртв) → CRITICAL-детектор вызван с ad_id и ошибкой
@pytest.mark.asyncio
async def test_autostop_channel_down_triggers_alert(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta,
        "load_meta_snapshot_freshness",
        AsyncMock(return_value=SimpleNamespace(fresh=True)),
    )
    err = TemporaryError("Failed to fetch", code=-2)
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=err))
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", AsyncMock(return_value=True))
    spy_alert = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "maybe_alert_autostop_channel_down", spy_alert)
    await meta.process_one_task(
        object(), _autostop_task(), client=AsyncMock(), alert_ctx=_alert_ctx()
    )

    spy_alert.assert_awaited_once()
    kwargs = spy_alert.await_args.kwargs
    assert kwargs["fb_ad_id"] == "123"
    assert kwargs["exc"] is err
    assert "redis_client" not in kwargs


# Fenced terminal success owns the atomic incident projection in core.tasks.queue.
@pytest.mark.asyncio
async def test_autostop_confirmed_success_uses_transactional_finalizer(monkeypatch) -> None:
    engine = object()
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta,
        "load_meta_snapshot_freshness",
        AsyncMock(return_value=SimpleNamespace(fresh=True)),
    )
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(return_value={"success": True, "modified_ids": ["123"]}),
    )
    succeeded = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_succeeded", succeeded)
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())
    await meta.process_one_task(
        engine, _autostop_task(), client=AsyncMock(), alert_ctx=_alert_ctx()
    )

    assert succeeded.await_args.kwargs["result"]["outcome"] == "CONFIRMED"
    assert callable(succeeded.await_args.kwargs["transactional_effect"])


@pytest.mark.asyncio
async def test_autostop_lost_terminal_race_does_not_resolve_incidents(monkeypatch) -> None:
    engine = object()
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta,
        "load_meta_snapshot_freshness",
        AsyncMock(return_value=SimpleNamespace(fresh=True)),
    )
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(return_value={"success": True, "modified_ids": ["123"]}),
    )
    monkeypatch.setattr(meta, "mark_task_succeeded", AsyncMock(return_value=False))
    sync = AsyncMock()
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", sync)

    await meta.process_one_task(engine, _autostop_task(), client=AsyncMock())

    sync.assert_not_awaited()


# Не-autostop pause_ad (ручной) с тем же отказом канала → CRITICAL-детектор НЕ вызывается
@pytest.mark.asyncio
async def test_non_autostop_failure_does_not_alert(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("Failed to fetch", code=-2))
    )
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", AsyncMock(return_value=True))
    spy_alert = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "maybe_alert_autostop_channel_down", spy_alert)
    await meta.process_one_task(
        object(),
        _autostop_task(requested_by="user"),
        client=AsyncMock(),
        alert_ctx=_alert_ctx(),
    )

    spy_alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_autostop_defers_without_external_call(monkeypatch) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta,
        "load_meta_snapshot_freshness",
        AsyncMock(
            return_value=SimpleNamespace(
                fresh=False,
                latest_cycle_at=None,
                interval_seconds=90,
            )
        ),
    )
    defer = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "defer_auto_stop_for_fresh_snapshot", defer)
    execute = AsyncMock(return_value={"success": True, "modified_ids": ["123"]})
    monkeypatch.setattr(meta, "execute_mutation", execute)
    engine = object()

    await meta.process_one_task(
        engine,
        _autostop_task(),
        client=AsyncMock(),
    )

    defer.assert_awaited_once_with(
        engine,
        task_id=42,
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000102"),
        lease_token=2,
    )
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "source_key"),
    [
        ("autostart_reconciliation", "supersedes_autostart_task_id"),
        ("activation_without_grace", "supersedes_activation_task_id"),
    ],
)
async def test_safety_compensation_bypasses_snapshot_freshness_gate(
    monkeypatch,
    reason: str,
    source_key: str,
) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    freshness = AsyncMock(
        return_value=SimpleNamespace(
            fresh=False,
            latest_cycle_at=None,
            interval_seconds=90,
        )
    )
    monkeypatch.setattr(meta, "load_meta_snapshot_freshness", freshness)
    execute = AsyncMock(return_value={"success": True, "modified_ids": ["123"]})
    monkeypatch.setattr(meta, "execute_mutation", execute)
    succeeded = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_succeeded", succeeded)

    await meta.process_one_task(
        object(),
        _autostop_task(
            payload={
                "mutation_kind": "pause_ad",
                "target_id": "123",
                "ad_account_id": "456",
                "params": {
                    "safety_compensation": reason,
                    source_key: 41,
                },
            }
        ),
        client=AsyncMock(),
    )

    freshness.assert_not_awaited()
    execute.assert_awaited_once()
    assert succeeded.await_args.kwargs["result"]["outcome"] == "CONFIRMED"


@pytest.mark.asyncio
async def test_malformed_safety_compensation_cannot_bypass_freshness_gate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    freshness = AsyncMock(
        return_value=SimpleNamespace(
            fresh=False,
            latest_cycle_at=None,
            interval_seconds=90,
        )
    )
    monkeypatch.setattr(meta, "load_meta_snapshot_freshness", freshness)
    defer = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "defer_auto_stop_for_fresh_snapshot", defer)
    execute = AsyncMock(return_value={"success": True, "modified_ids": ["123"]})
    monkeypatch.setattr(meta, "execute_mutation", execute)

    await meta.process_one_task(
        object(),
        _autostop_task(
            payload={
                "mutation_kind": "pause_ad",
                "target_id": "123",
                "ad_account_id": "456",
                "params": {
                    "safety_compensation": "autostart_reconciliation",
                    "supersedes_autostart_task_id": "41",
                },
            }
        ),
        client=AsyncMock(),
    )

    freshness.assert_awaited_once()
    defer.assert_awaited_once()
    execute.assert_not_awaited()
