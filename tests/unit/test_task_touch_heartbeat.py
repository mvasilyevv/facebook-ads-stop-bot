# -*- coding: utf-8 -*-
"""Unit: MID-10 heartbeat-touch долгих mutation против кражи reconciler'ом.

Долгий исполнитель (upload видео >30 мин) без освежения updated_at был бы украден
reconcile_stuck_running (running старше 30 мин → retrying по updated_at) → повторное
исполнение = дубль/двойной открут. touch_task_running освежает ИМЕННО updated_at
(поле, которое читает reconciler — writer↔reader контракт). _execute_with_touch
держит фоновый touch, пока идёт execute_mutation.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import apps.meta_api_worker.main as meta
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import (
    Task,
    create_task,
    defer_unknown_reconciliation,
    mark_external_call_started,
    reconcile_stuck_running,
    requeue_for_retry,
    requeue_unknown_for_reconciliation,
    touch_task_running,
)

_LEASE_OWNER = uuid.UUID("00000000-0000-0000-0000-000000000081")
_LEASE_TOKEN = 4


def _task_snapshot(
    task_id: int,
    *,
    payload: dict | None = None,
    attempt_count: int = 0,
    max_attempts: int = 5,
    requested_by: str = "test",
    result: dict | None = None,
    lease_token: int = _LEASE_TOKEN,
) -> Task:
    now = datetime.now(UTC)
    return Task(
        id=task_id,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key=f"task-{task_id}",
        payload=payload
        or {"mutation_kind": "pause_ad", "target_id": "123", "ad_account_id": "123"},
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        requested_by=requested_by,
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=result,
        lane="money",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=_LEASE_OWNER,
        lease_token=lease_token,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


def test_default_touch_interval_is_strictly_inside_lease() -> None:
    interval = meta._resolve_touch_interval(60, None)
    assert interval == 20
    assert 0 < interval < 60


@pytest.mark.parametrize("configured", ["0", "60", "61", "-1"])
def test_invalid_touch_interval_fails_worker_boot(configured: str) -> None:
    with pytest.raises(RuntimeError, match="strictly less"):
        meta._resolve_touch_interval(60, configured)


class _FakeConn:
    """Мок asyncpg conn: записывает SQL + params каждого execute."""

    def __init__(self, rowcount: int, sink: list) -> None:
        self._rowcount = rowcount
        self._sink = sink

    async def execute(self, stmt, params):
        statement = str(stmt)
        self._sink.append((statement, params))
        terminal_rows = (
            [
                SimpleNamespace(
                    _mapping={
                        "id": 123,
                        "correlation_id": None,
                        "payload": {},
                        "status": "retrying",
                        "result": {},
                        "requested_by": "test",
                        "lane": "background",
                        "task_type": "meta_api_mutation",
                    }
                )
            ]
            if self._rowcount and "RETURNING id, correlation_id" in statement
            else []
        )
        return SimpleNamespace(
            rowcount=self._rowcount,
            first=lambda: (123,),
            all=lambda: terminal_rows,
        )


class _FakeEngine:
    """Мок AsyncEngine.begin() как async context manager вокруг _FakeConn."""

    def __init__(self, rowcount: int = 1) -> None:
        self.calls: list = []
        self._rowcount = rowcount

    def begin(self):
        conn = _FakeConn(self._rowcount, self.calls)

        class _Ctx:
            async def __aenter__(self_inner):
                return conn

            async def __aexit__(self_inner, *a):
                return False

        return _Ctx()


# MID-10: touch_task_running обновляет РОВНО updated_at (поле-детектор reconciler'а),
# а не started_at/claimed_at — writer↔reader контракт с reconcile_stuck_running.
@pytest.mark.asyncio
async def test_touch_updates_updated_at_field() -> None:
    engine = _FakeEngine(rowcount=1)
    ok = await touch_task_running(
        engine,
        task_id=42,
        lease_owner=_LEASE_OWNER,
        lease_token=_LEASE_TOKEN,
    )

    assert ok is True, "строка 'running' обновлена → True"
    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    # Ключевой инвариант: touch пишет updated_at.
    assert "SET updated_at = NOW()" in sql
    # НЕ трогает started_at/claimed_at (их reconciler НЕ читает).
    assert "started_at" not in sql
    assert "claimed_at" not in sql
    # Guard: только пока задача ещё 'running' (не воскрешаем закрытую).
    assert "status = 'running'" in sql
    assert "lease_expires_at > clock_timestamp()" in sql
    assert params["id"] == 42


# touch не совпал (задача уже не 'running' — украдена/закрыта) → False, caller остановит цикл.
@pytest.mark.asyncio
async def test_touch_returns_false_when_not_running() -> None:
    engine = _FakeEngine(rowcount=0)
    ok = await touch_task_running(
        engine,
        task_id=7,
        lease_owner=_LEASE_OWNER,
        lease_token=_LEASE_TOKEN,
    )
    assert ok is False


@pytest.mark.asyncio
async def test_mark_external_started_locks_target_and_sets_boundary() -> None:
    engine = _FakeEngine(rowcount=1)

    ok = await mark_external_call_started(
        engine,
        task_id=81,
        target_lock_key="120248043699080390",
        lease_owner=_LEASE_OWNER,
        lease_token=_LEASE_TOKEN,
    )

    assert ok is True
    assert len(engine.calls) == 2
    lock_sql, lock_params = engine.calls[0]
    update_sql, update_params = engine.calls[1]
    assert "pg_advisory_xact_lock" in lock_sql
    assert lock_params["lock_key"] == "120248043699080390"
    assert "external_started_at = COALESCE(external_started_at, NOW())" in update_sql
    assert "status = 'running'" in update_sql
    assert "lease_expires_at > clock_timestamp()" in update_sql
    assert "external_started_at IS NULL" not in update_sql
    assert update_params["id"] == 81


@pytest.mark.asyncio
async def test_retry_preserves_external_started_boundary() -> None:
    """Unknown external outcome must remain non-cancellable across retries."""
    engine = _FakeEngine(rowcount=1)

    retried = await requeue_for_retry(
        engine,
        task_id=81,
        error="temporary transport error",
        attempt_count=0,
        max_attempts=5,
        lease_owner=_LEASE_OWNER,
        lease_token=_LEASE_TOKEN,
    )

    assert retried is True
    update_sql, _ = engine.calls[0]
    assert "status = 'retrying'" in update_sql
    assert "external_started_at = NULL" not in update_sql


@pytest.mark.asyncio
async def test_stuck_reconcile_preserves_external_started_boundary() -> None:
    """Crash recovery must not make an already-started call cancellable again."""
    engine = _FakeEngine(rowcount=1)

    reconciled = await reconcile_stuck_running(engine, stuck_after_seconds=60)

    assert reconciled == 1
    update_sql, _ = engine.calls[0]
    assert "SET status = CASE" in update_sql
    assert "'reconcile_required', true" in update_sql
    assert "'bulk_status_change'" in update_sql
    assert "deadline_at = CASE" in update_sql
    assert "external_started_at = NULL" not in update_sql


@pytest.mark.asyncio
async def test_create_task_takes_target_lock_before_insert() -> None:
    engine = _FakeEngine(rowcount=1)

    task_id = await create_task(
        engine,
        task_type="meta_api_mutation",
        idempotency_key="lock-order-test",
        payload={
            "mutation_kind": "pause_ad",
            "target_id": "120248043699080390",
            "ad_account_id": "123",
        },
        requested_by="test",
        target_lock_key="120248043699080390",
    )

    assert task_id == 123
    assert len(engine.calls) == 3
    lock_sql, lock_params = engine.calls[0]
    insert_sql, _ = engine.calls[1]
    notify_sql, notify_params = engine.calls[2]
    assert "pg_advisory_xact_lock" in lock_sql
    assert lock_params["lock_key"] == "120248043699080390"
    assert "INSERT INTO task_queue" in insert_sql
    assert "pg_notify" in notify_sql
    assert notify_params["channel"] == "fb_task_queue"


@pytest.mark.asyncio
async def test_create_task_takes_bulk_locks_in_sorted_unique_order() -> None:
    engine = _FakeEngine(rowcount=1)

    task_id = await create_task(
        engine,
        task_type="meta_api_mutation",
        idempotency_key="bulk-lock-order-test",
        payload={
            "mutation_kind": "bulk_status_change",
            "target_id": "bulk:3",
            "ad_account_id": "123",
        },
        requested_by="test",
        target_lock_keys=["ad-3", "ad-1", "ad-3", "ad-2"],
    )

    assert task_id == 123
    assert [params["lock_key"] for _, params in engine.calls[:-2]] == [
        "ad-1",
        "ad-2",
        "ad-3",
    ]
    assert "INSERT INTO task_queue" in engine.calls[-2][0]
    assert "pg_notify" in engine.calls[-1][0]


@pytest.mark.asyncio
async def test_last_mutation_attempt_still_schedules_read_reconciliation() -> None:
    engine = _FakeEngine(rowcount=1)
    task = _task_snapshot(
        91,
        payload={
            "mutation_kind": "bulk_status_change",
            "target_id": "bulk:last",
            "ad_account_id": "123",
            "params": {"action": "activate", "ad_ids": ["1"]},
        },
        attempt_count=0,
        max_attempts=1,
        requested_by="cabinet_autostart",
        result=None,
    )
    task.idempotency_key = "last-ambiguous-attempt"

    scheduled = await requeue_unknown_for_reconciliation(
        engine,
        task=task,
        error="response lost",
    )

    assert scheduled is True
    update_sql, params = engine.calls[0]
    assert "status = 'retrying'" in update_sql
    assert "'reconcile_required', true" in update_sql
    assert params["attempt_count"] == 1


@pytest.mark.asyncio
async def test_busy_reconciliation_lock_does_not_consume_attempt_budget() -> None:
    engine = _FakeEngine(rowcount=1)
    task = _task_snapshot(
        92,
        payload={
            "mutation_kind": "bulk_status_change",
            "target_id": "bulk:busy",
            "ad_account_id": "123",
            "params": {"action": "activate", "ad_ids": ["1"]},
        },
        attempt_count=5,
        max_attempts=5,
        requested_by="cabinet_autostart",
        result={"outcome": "UNKNOWN", "reconcile_required": True},
    )
    task.idempotency_key = "busy-reconciliation"

    assert await defer_unknown_reconciliation(
        engine,
        task=task,
        error="target lock busy",
    )

    update_sql, params = engine.calls[0]
    assert "status = 'retrying'" in update_sql
    assert "attempt_count" not in update_sql
    assert params["deadline_delay_seconds"] == 31


# _execute_with_touch: во время долгой mutation фоновый touch освежает updated_at,
# по завершении touch-таск отменяется, результат mutation возвращается как есть.
@pytest.mark.asyncio
async def test_execute_with_touch_calls_touch_during_long_mutation(monkeypatch) -> None:
    touched: list[int] = []

    async def fake_touch(engine, *, task_id, **kwargs):
        touched.append(task_id)
        return True

    monkeypatch.setattr(meta, "touch_task_running", fake_touch)

    # execute_mutation "долгая": спим дольше touch-интервала, чтобы touch успел сработать.
    async def slow_mutation(payload, *, client, **_kwargs):
        await asyncio.sleep(0.05)
        return {"success": True, "modified_ids": ["1"]}

    monkeypatch.setattr(meta, "execute_mutation", slow_mutation)

    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="duplicate_adset_structure",
        target_id="456",
    )
    task = _task_snapshot(99, lease_token=7)
    # Интервал touch'а сильно меньше длительности mutation → минимум один touch.
    result = await meta._execute_with_touch(
        MagicMock(), task, payload, client=AsyncMock(), touch_interval_seconds=0.01
    )

    assert result == {"success": True, "modified_ids": ["1"]}
    assert touched, "фоновый touch должен освежить updated_at хотя бы раз за долгую mutation"
    assert all(tid == 99 for tid in touched)


# _execute_with_touch: короткая mutation завершается ДО первого touch-интервала —
# touch не вызывается (нет лишних апдейтов), результат пробрасывается.
@pytest.mark.asyncio
async def test_execute_with_touch_no_touch_for_fast_mutation(monkeypatch) -> None:
    touched: list[int] = []

    async def fake_touch(engine, *, task_id, **kwargs):
        touched.append(task_id)
        return True

    monkeypatch.setattr(meta, "touch_task_running", fake_touch)
    monkeypatch.setattr(
        meta, "execute_mutation", AsyncMock(return_value={"success": True, "modified_ids": []})
    )

    payload = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="123")
    task = _task_snapshot(1, lease_token=8)
    # Большой интервал → быстрая mutation закончится раньше первого touch.
    result = await meta._execute_with_touch(
        MagicMock(), task, payload, client=AsyncMock(), touch_interval_seconds=100.0
    )

    assert result["success"] is True
    assert touched == [], "быстрая mutation не должна триггерить touch"


# _execute_with_touch: исключение mutation пробрасывается, touch-таск корректно отменяется.
@pytest.mark.asyncio
async def test_execute_with_touch_propagates_exception(monkeypatch) -> None:
    monkeypatch.setattr(meta, "touch_task_running", AsyncMock(return_value=True))

    async def failing_mutation(payload, *, client):
        raise RuntimeError("meta boom")

    monkeypatch.setattr(meta, "execute_mutation", failing_mutation)

    payload = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="123")
    with pytest.raises(RuntimeError, match="meta boom"):
        await meta._execute_with_touch(
            MagicMock(),
            _task_snapshot(1),
            payload,
            client=AsyncMock(),
            touch_interval_seconds=0.01,
        )


@pytest.mark.asyncio
async def test_control_monitor_failure_cancels_and_drains_external_operation(
    monkeypatch,
) -> None:
    """A failed DB control poll must not leave a mutation running in background."""
    mutation_started = asyncio.Event()
    mutation_drained = asyncio.Event()

    async def slow_mutation(payload, *, client):
        mutation_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            mutation_drained.set()

    async def failed_control(engine, task):
        await mutation_started.wait()
        raise RuntimeError("control database unavailable")

    async def idle_touch(engine, task, interval_seconds):
        await asyncio.Event().wait()

    monkeypatch.setattr(meta, "execute_mutation", slow_mutation)
    monkeypatch.setattr(meta, "_wait_for_task_control", failed_control)
    monkeypatch.setattr(meta, "_touch_loop", idle_touch)

    task = _task_snapshot(100, lease_token=9)
    payload = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="123")

    with pytest.raises(meta.AmbiguousResultError, match="control_monitor_failed"):
        await meta._execute_with_touch(
            MagicMock(),
            task,
            payload,
            client=AsyncMock(),
            touch_interval_seconds=100.0,
        )

    assert mutation_drained.is_set(), "external task must be cancelled and awaited before return"


@pytest.mark.asyncio
async def test_lease_renewal_false_cancels_and_drains_external_operation(
    monkeypatch,
) -> None:
    mutation_started = asyncio.Event()
    mutation_drained = asyncio.Event()

    async def slow_mutation(payload, *, client):
        mutation_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            mutation_drained.set()

    async def lost_touch(engine, *, task_id, **kwargs):
        await mutation_started.wait()
        return False

    async def idle_control(engine, task):
        await asyncio.Event().wait()

    monkeypatch.setattr(meta, "execute_mutation", slow_mutation)
    monkeypatch.setattr(meta, "touch_task_running", lost_touch)
    monkeypatch.setattr(meta, "_wait_for_task_control", idle_control)
    task = _task_snapshot(101, lease_token=10)
    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="123",
    )

    with pytest.raises(meta.AmbiguousResultError, match="lease_renewal_failed"):
        await meta._execute_with_touch(
            MagicMock(),
            task,
            payload,
            client=AsyncMock(),
            touch_interval_seconds=0.001,
        )

    assert mutation_drained.is_set()


@pytest.mark.asyncio
async def test_lease_renewal_exception_cancels_and_drains_external_operation(
    monkeypatch,
) -> None:
    mutation_started = asyncio.Event()
    mutation_drained = asyncio.Event()

    async def slow_mutation(payload, *, client):
        mutation_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            mutation_drained.set()

    async def failed_touch(engine, *, task_id, **kwargs):
        await mutation_started.wait()
        raise RuntimeError("database unavailable")

    async def idle_control(engine, task):
        await asyncio.Event().wait()

    monkeypatch.setattr(meta, "execute_mutation", slow_mutation)
    monkeypatch.setattr(meta, "touch_task_running", failed_touch)
    monkeypatch.setattr(meta, "_wait_for_task_control", idle_control)
    task = _task_snapshot(102, lease_token=11)
    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="123",
    )

    with pytest.raises(meta.AmbiguousResultError, match="lease_renewal_failed"):
        await meta._execute_with_touch(
            MagicMock(),
            task,
            payload,
            client=AsyncMock(),
            touch_interval_seconds=0.001,
        )

    assert mutation_drained.is_set()


@pytest.mark.asyncio
async def test_cancelling_owner_drains_every_background_task(monkeypatch) -> None:
    mutation_started = asyncio.Event()
    mutation_drained = asyncio.Event()
    touch_drained = asyncio.Event()
    control_drained = asyncio.Event()

    async def slow_mutation(payload, *, client):
        mutation_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            mutation_drained.set()

    async def idle_touch(engine, task, interval_seconds):
        try:
            await asyncio.Event().wait()
        finally:
            touch_drained.set()

    async def idle_control(engine, task):
        try:
            await asyncio.Event().wait()
        finally:
            control_drained.set()

    monkeypatch.setattr(meta, "execute_mutation", slow_mutation)
    monkeypatch.setattr(meta, "_touch_loop", idle_touch)
    monkeypatch.setattr(meta, "_wait_for_task_control", idle_control)
    task = _task_snapshot(103, lease_token=12)
    payload = MetaMutationPayload(
        ad_account_id="123",
        mutation_kind="pause_ad",
        target_id="123",
    )
    owner = asyncio.create_task(
        meta._execute_with_touch(
            MagicMock(),
            task,
            payload,
            client=AsyncMock(),
            touch_interval_seconds=100,
        )
    )
    await mutation_started.wait()
    owner.cancel()

    with pytest.raises(asyncio.CancelledError):
        await owner

    assert mutation_drained.is_set()
    assert touch_drained.is_set()
    assert control_drained.is_set()
