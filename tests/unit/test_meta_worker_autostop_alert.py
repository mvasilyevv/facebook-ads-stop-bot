# -*- coding: utf-8 -*-
"""Unit-тесты подключения autostop-алерта в meta_api_worker.process_one_task.

Сценарий: auto-stop pause_ad ловит «канал мёртв» (code=-2) → воркер фиксирует
durable CRITICAL event даже без Redis; не-autostop фейл алерт НЕ триггерит.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.meta_api_worker.main as meta
import core.meta_api.autostop_alert as autostop_alert
import core.tasks.queue as task_queue
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
                scan_id=None,
                decision_confirmed=False,
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
async def test_safety_compensation_bypasses_snapshot_freshness_gate(
    monkeypatch,
) -> None:
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    freshness = AsyncMock(
        return_value=SimpleNamespace(
            fresh=False,
            latest_cycle_at=None,
            scan_id=None,
            decision_confirmed=False,
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
                    "safety_compensation": "activation_without_grace",
                    "supersedes_activation_task_id": 41,
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
            scan_id=None,
            decision_confirmed=False,
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
                    "safety_compensation": "forged_compensation",
                    "supersedes_activation_task_id": 41,
                },
            }
        ),
        client=AsyncMock(),
    )

    freshness.assert_awaited_once()
    defer.assert_awaited_once()
    execute.assert_not_awaited()


class _AlertRowResult:
    def __init__(self, row) -> None:
        self._row = row

    def first(self):
        return self._row


@pytest.mark.asyncio
async def test_terminal_autostop_with_active_ad_gets_non_resolving_escalation(
    monkeypatch,
) -> None:
    candidate_source = inspect.getsource(autostop_alert._find_undelivered_candidate_ids)

    async def execute(statement, _params):
        sql = str(statement)
        terminal_active_guard = (
            "t.status IN ('failed', 'cancelled')" in sql
            and "UPPER(COALESCE(ad.delivery_status, '')) = 'ACTIVE'" in sql
            and "FROM incidents AS terminal_incident" in sql
        )
        if not terminal_active_guard:
            return _AlertRowResult(None)
        return _AlertRowResult(
            SimpleNamespace(
                id=42,
                fb_ad_id="230011223344",
                status="failed",
                delivery_status="ACTIVE",
                attempt_count=15,
                last_error="attempts exhausted",
                age_minutes=12,
                ad_name="Ad",
                spend="91.25",
                currency="USD",
            )
        )

    connection = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = context
    monkeypatch.setattr(
        autostop_alert,
        "_find_undelivered_candidate_ids",
        AsyncMock(return_value=[42]),
    )
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(
        autostop_alert,
        "notify_recurring_incident_in_transaction",
        notify,
    )

    accepted = await autostop_alert.escalate_undelivered_autostop_pauses(
        engine,
        stuck_after_seconds=600,
    )

    assert accepted == 1
    assert "status IN ('failed', 'cancelled')" in candidate_source
    assert "UPPER(COALESCE(ad.delivery_status, '')) = 'ACTIVE'" in candidate_source
    assert "FROM incidents AS terminal_incident" in candidate_source
    assert "terminal_prefix" in candidate_source
    assert "WHEN task.status IN ('failed', 'cancelled') THEN 0" in candidate_source
    assert notify.await_args.kwargs["incident_key"] == (
        f"{autostop_alert.TERMINAL_UNDELIVERED_INCIDENT_KEY_PREFIX}230011223344"
    )
    assert "TERMINAL_UNDELIVERED_INCIDENT_KEY_PREFIX" not in inspect.getsource(
        task_queue._resolve_confirmed_autostop_incidents_in_transaction
    )
