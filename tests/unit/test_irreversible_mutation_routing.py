# -*- coding: utf-8 -*-
"""Unit: маршрутизация ошибок duplicate_adset_structure в meta_api_worker.

duplicate_adset_structure создаёт новые Meta objects.
Если ответ потерян ПОСЛЕ коммита Meta (transient gRPC / битый JSON / ValueError на
постобработке / неклассифицированное), retry создал бы ДУБЛЬ кампании. Поэтому такие
ошибки уводятся в mark_failed (не requeue). Обратимые (pause_ad) — обычный requeue.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
from core.meta_api.errors import AmbiguousResultError, TemporaryError
from core.meta_api.mutations.duplicate_adset_structure import (
    DuplicateAdsetStructurePartialError,
)
from core.observer.enable_grace import EnableGraceUnsafeError
from core.tasks.queue import Task


@asynccontextmanager
async def _unlocked_targets(_engine, *, ad_ids):
    yield SimpleNamespace(
        requested_ad_ids=tuple(ad_ids),
        busy_ad_id=None,
    )


class _LockConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def begin(self):
        class _Begin:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, *_args):
                return False

        return _Begin()

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return SimpleNamespace(rowcount=1)


def _task(kind: str, tid: int = 1) -> Task:
    now = datetime.now(UTC)
    lane = "bulk" if kind in {"bulk_status_change", "duplicate_adset_structure"} else "interactive"
    return Task(
        id=tid,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key=f"meta:{kind}:{tid}",
        payload={"mutation_kind": kind, "target_id": "100", "ad_account_id": "123"},
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane=lane,
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(seconds=1800 if lane == "bulk" else 120),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000111"),
        lease_token=7,
        lease_expires_at=now + timedelta(minutes=2),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


def _fenced_task(kind: str, tid: int = 1) -> Task:
    return _task(kind, tid)


@pytest.fixture
def _patched(monkeypatch):
    """Сканирование включено + owner-фильтр выключен → доходим до execute_mutation."""
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    spy_fail = AsyncMock(return_value=True)
    spy_fail_or_cancelled = AsyncMock(return_value="failed")
    spy_requeue = AsyncMock(return_value=True)
    spy_pre_send = AsyncMock(return_value="retrying")
    monkeypatch.setattr(meta, "mark_task_failed", spy_fail)
    monkeypatch.setattr(meta, "mark_task_failed_or_cancelled", spy_fail_or_cancelled)
    monkeypatch.setattr(meta, "requeue_task", spy_requeue)
    monkeypatch.setattr(meta, "requeue_task_proven_not_committed", spy_pre_send)
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta,
        "authorize_duplicate_execution_boundary",
        AsyncMock(return_value=True),
    )
    return spy_fail, spy_requeue, spy_fail_or_cancelled


@pytest.mark.asyncio
async def test_duplicate_adset_structure_temporary_marks_failed(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("deadline")))
    await meta.process_one_task(object(), _task("duplicate_adset_structure"), client=AsyncMock())
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_adset_structure_partial_routes_created_ids(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    defer_recovery = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "defer_duplicate_recovery", defer_recovery)
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(
            side_effect=DuplicateAdsetStructurePartialError(
                "verification failed",
                created_ids={"campaigns": ["800"], "adsets": ["801"], "ads": ["802"]},
                failed_steps=[{"step": "verify_paused_structure", "error": "deadline"}],
                cleanup_failures=[{"id": "802", "error": "pause failed"}],
            )
        ),
    )
    await meta.process_one_task(object(), _task("duplicate_adset_structure"), client=AsyncMock())
    spy_fail.assert_not_awaited()
    spy_requeue.assert_not_awaited()
    defer_recovery.assert_awaited_once()
    error = defer_recovery.await_args.kwargs["error"]
    assert "duplicate_adset_structure_partial_fail" in error
    assert "800" in error and "801" in error and "802" in error
    assert defer_recovery.await_args.kwargs["checkpoint"] == {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "phase": "recovery_retrying",
        "partial_fail": True,
        "created_ids": {"campaigns": ["800"], "adsets": ["801"], "ads": ["802"]},
        "failed_steps": [{"step": "verify_paused_structure", "error": "deadline"}],
        "cleanup_failures": [{"id": "802", "error": "pause failed"}],
        "recovery_requested": True,
        "outcome": "UNKNOWN",
        "reconcile_required": True,
        "manual_review_required": True,
    }
    # The queue transition owns the incident projection. The worker must never
    # perform a second best-effort send after the fenced writer returns.


@pytest.mark.asyncio
async def test_duplicate_unknown_exception_marks_failed(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=RuntimeError("boom")))
    await meta.process_one_task(object(), _task("duplicate_adset_structure"), client=AsyncMock())
    spy_fail.assert_awaited_once()
    spy_requeue.assert_not_awaited()


# Контраст: pause_ad после зафиксированной внешней границы → UNKNOWN reconciliation.
@pytest.mark.asyncio
async def test_pause_ad_temporary_requeues(monkeypatch, _patched) -> None:
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    reconcile = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", reconcile)
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("deadline")))
    await meta.process_one_task(object(), _task("pause_ad"), client=AsyncMock())
    reconcile.assert_awaited_once()
    spy_requeue.assert_not_awaited()
    spy_fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_pause_ad_temporary_after_boundary_requires_reconciliation(
    monkeypatch, _patched
) -> None:
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    reconcile = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", reconcile)
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("deadline")))

    task = _fenced_task("pause_ad", tid=41)
    await meta.process_one_task(object(), task, client=AsyncMock())

    reconcile.assert_awaited_once()
    assert "external boundary" in reconcile.await_args.kwargs["error"]
    spy_requeue.assert_not_awaited()
    spy_fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_status_temporary_after_boundary_is_terminal_unknown(
    monkeypatch, _patched
) -> None:
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=TemporaryError("reset")))

    await meta.process_one_task(
        object(),
        _fenced_task("duplicate_adset_structure", tid=42),
        client=AsyncMock(),
    )

    spy_requeue.assert_not_awaited()
    spy_fail.assert_awaited_once()
    assert spy_fail.await_args.kwargs["result"] == {
        "outcome": "UNKNOWN",
        "reconcile_required": True,
        "manual_review_required": True,
        "operation": "duplicate_adset_structure",
        "target_id": "100",
        "reason": "temporary_after_external_boundary",
    }


@pytest.mark.asyncio
async def test_page_evaluate_failure_after_boundary_requires_status_reconciliation(
    monkeypatch, _patched
) -> None:
    """A lost page can hide an accepted status write; verify before resend."""
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    reconcile = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", reconcile)
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(
            side_effect=AmbiguousResultError(
                "page.evaluate failed after fetch may have committed",
                code=-3,
            )
        ),
    )

    await meta.process_one_task(
        object(),
        _fenced_task("pause_ad", tid=47),
        client=AsyncMock(),
    )

    reconcile.assert_awaited_once()
    spy_requeue.assert_not_awaited()
    spy_fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_bulk_status_queues_read_reconciliation_not_mutation_retry(
    monkeypatch,
    _patched,
) -> None:
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    reconcile = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", reconcile)
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=AmbiguousResultError("batch response lost", code=-3)),
    )
    task = _fenced_task("bulk_status_change", tid=49)
    task.payload["params"] = {"action": "activate", "ad_ids": ["101", "102"]}

    await meta.process_one_task(object(), task, client=AsyncMock())

    reconcile.assert_awaited_once()
    spy_requeue.assert_not_awaited()
    spy_fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_temporary_after_boundary_also_requires_read_reconciliation(
    monkeypatch,
    _patched,
) -> None:
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    reconcile = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", reconcile)
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=TemporaryError("batch transport reset")),
    )
    task = _fenced_task("bulk_status_change", tid=52)
    task.payload["params"] = {"action": "pause", "ad_ids": ["301", "302"]}

    await meta.process_one_task(object(), task, client=AsyncMock())

    reconcile.assert_awaited_once()
    spy_requeue.assert_not_awaited()
    spy_fail.assert_not_awaited()


@pytest.mark.asyncio
async def test_page_evaluate_failure_after_boundary_never_blindly_retries_non_status(
    monkeypatch,
    _patched,
) -> None:
    """Non-status writes remain terminal UNKNOWN when their response is lost."""
    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(
            side_effect=AmbiguousResultError(
                "page.evaluate failed after fetch may have committed",
                code=-3,
            )
        ),
    )

    await meta.process_one_task(
        object(),
        _fenced_task("duplicate_adset_structure", tid=48),
        client=AsyncMock(),
    )

    spy_requeue.assert_not_awaited()
    spy_fail.assert_awaited_once()
    assert spy_fail.await_args.kwargs["result"] == {
        "outcome": "UNKNOWN",
        "reconcile_required": True,
        "manual_review_required": True,
        "operation": "duplicate_adset_structure",
        "target_id": "100",
        "reason": "ambiguous_result",
    }


@pytest.mark.asyncio
async def test_irreversible_value_error_after_boundary_is_unknown_manual_review(
    monkeypatch,
    _patched,
) -> None:
    """A post-response parser error cannot prove that Meta rejected the create."""
    spy_fail, spy_requeue, _spy_fail_or_cancelled = _patched
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=ValueError("created id is malformed")),
    )

    await meta.process_one_task(
        object(),
        _fenced_task("duplicate_adset_structure", tid=58),
        client=AsyncMock(),
    )

    spy_requeue.assert_not_awaited()
    spy_fail.assert_awaited_once()
    assert spy_fail.await_args.kwargs["result"] == {
        "outcome": "UNKNOWN",
        "reconcile_required": True,
        "manual_review_required": True,
        "operation": "duplicate_adset_structure",
        "target_id": "100",
        "reason": "value_error_postprocess",
        "operator_reason": "Связь с Meta прервалась, но необратимая команда уже была отправлена. Повторный запуск может создать дубль (например, двойной бюджет), операция заморожена. Проверьте результат вручную непосредственно в рекламном кабинете Meta.",
    }


@pytest.mark.asyncio
async def test_bulk_reconciliation_is_per_ad_and_never_resends_status(monkeypatch) -> None:
    task = _fenced_task("bulk_status_change", tid=50)
    task.payload["params"] = {"action": "activate", "ad_ids": ["101", "102"]}
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value=[
            {
                "code": 200,
                "body": '{"status":"ACTIVE","effective_status":"PAUSED"}',
            },
            {
                "code": 500,
                "body": '{"error":{"message":"read unavailable"}}',
            },
        ]
    )
    failed = AsyncMock(return_value=True)
    requeue = AsyncMock(return_value=True)
    fsm = AsyncMock()
    monkeypatch.setattr(meta, "mark_task_failed", failed)
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", requeue)
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", fsm)
    monkeypatch.setattr(meta, "locked_status_targets", _unlocked_targets)

    terminal = await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    assert terminal is True
    requeue.assert_not_awaited()
    failed.assert_awaited_once()
    result = failed.await_args.kwargs["result"]
    assert result["outcome"] == "UNKNOWN"
    assert result["confirmed_ids"] == ["101"]
    assert result["unknown_ids"] == ["102"]
    assert result["modified_ids"] == ["101"]
    assert [item["outcome"] for item in result["per_ad"]] == ["CONFIRMED", "UNKNOWN"]
    await failed.await_args.kwargs["transactional_effect"](object())
    fsm.assert_awaited_once()
    call = client.execute_graph_call.await_args.kwargs
    assert call["method"] == "POST"
    assert '"method": "GET"' in call["query_params"]["batch"]
    assert "status=ACTIVE" not in call["query_params"]["batch"]


@pytest.mark.asyncio
async def test_bulk_reconciliation_read_failure_is_terminal_unknown_without_retry(
    monkeypatch,
) -> None:
    task = _fenced_task("bulk_status_change", tid=51)
    task.payload["params"] = {"action": "pause", "ad_ids": ["201", "202"]}
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(side_effect=TemporaryError("read timeout"))
    failed = AsyncMock(return_value=True)
    requeue = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_failed", failed)
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", requeue)
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())
    monkeypatch.setattr(meta, "locked_status_targets", _unlocked_targets)

    terminal = await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    assert terminal is True
    requeue.assert_not_awaited()
    result = failed.await_args.kwargs["result"]
    assert result["outcome"] == "UNKNOWN"
    assert result["confirmed_ids"] == []
    assert result["unknown_ids"] == ["201", "202"]


@pytest.mark.asyncio
async def test_bulk_reconciliation_busy_target_stays_non_terminal(monkeypatch) -> None:
    task = _fenced_task("bulk_status_change", tid=55)
    task.payload["params"] = {"action": "activate", "ad_ids": ["301", "302"]}
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)

    @asynccontextmanager
    async def busy_targets(_engine, *, ad_ids):
        yield SimpleNamespace(
            requested_ad_ids=tuple(ad_ids),
            busy_ad_id="302",
        )

    defer = AsyncMock(return_value=True)
    fail = AsyncMock(return_value=True)
    client = AsyncMock()
    monkeypatch.setattr(meta, "locked_status_targets", busy_targets)
    monkeypatch.setattr(meta, "defer_unknown_reconciliation", defer)
    monkeypatch.setattr(meta, "mark_task_failed", fail)

    assert await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    defer.assert_awaited_once()
    assert "302" in defer.await_args.kwargs["error"]
    fail.assert_not_awaited()
    client.execute_graph_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_honours_cancel_after_verified_not_applied(monkeypatch) -> None:
    task = _fenced_task("pause_ad", tid=43)
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value={"status": "ACTIVE", "effective_status": "ACTIVE"}
    )
    resolve = AsyncMock(return_value="cancelled")
    monkeypatch.setattr(meta, "resolve_status_reconciliation_not_applied", resolve)

    terminal = await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    assert terminal is True
    resolve.assert_awaited_once()
    assert resolve.await_args.kwargs["effective_status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_reconciliation_prepares_exactly_one_safe_resend(monkeypatch) -> None:
    task = _fenced_task("pause_ad", tid=44)
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value={"status": "ACTIVE", "effective_status": "ACTIVE"}
    )
    monkeypatch.setattr(
        meta,
        "resolve_status_reconciliation_not_applied",
        AsyncMock(return_value="running"),
    )

    terminal = await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    assert terminal is False
    assert task.result == {
        "reconciled_not_applied": True,
        "effective_status": "ACTIVE",
    }
    assert task.external_started_at is None


@pytest.mark.asyncio
async def test_reconciliation_does_not_resend_after_final_verified_attempt(
    monkeypatch,
) -> None:
    task = _fenced_task("pause_ad", tid=55)
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value={"status": "ACTIVE", "effective_status": "ACTIVE"}
    )
    resolve = AsyncMock(return_value="failed")
    monkeypatch.setattr(
        meta,
        "resolve_status_reconciliation_not_applied",
        resolve,
    )

    terminal = await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    assert terminal is True
    assert task.result == {"outcome": "UNKNOWN", "reconcile_required": True}


@pytest.mark.asyncio
async def test_pause_reconciliation_does_not_accept_inherited_parent_pause(monkeypatch) -> None:
    task = _fenced_task("pause_ad", tid=45)
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value={"status": "ACTIVE", "effective_status": "PAUSED"}
    )
    resolve = AsyncMock(return_value="running")
    monkeypatch.setattr(meta, "resolve_status_reconciliation_not_applied", resolve)

    terminal = await meta._reconcile_unknown_status_action(object(), task, payload, client=client)

    assert terminal is False
    assert resolve.await_args.kwargs["effective_status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_activate_reconciliation_accepts_configured_active_under_paused_parent(
    monkeypatch,
) -> None:
    task = _fenced_task("activate_ad", tid=46)
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value={"status": "ACTIVE", "effective_status": "PAUSED"}
    )
    succeeded = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_succeeded", succeeded)
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())

    terminal = await meta._reconcile_unknown_status_action(object(), task, payload, client=client)

    assert terminal is True
    assert succeeded.await_args.kwargs["result"] == {
        "outcome": "CONFIRMED",
        "reconciled_after_unknown": True,
        "status": "ACTIVE",
        "effective_status": "PAUSED",
    }


@pytest.mark.asyncio
async def test_confirmed_active_without_grace_atomically_queues_pause_compensation(
    monkeypatch,
) -> None:
    task = _fenced_task("activate_ad", tid=54)
    task.payload["params"] = {"enable_grace": {"spend_cap": "10.00"}}
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value={"status": "ACTIVE", "effective_status": "ACTIVE"}
    )
    monkeypatch.setattr(
        meta,
        "_prepare_enable_grace_for_payload",
        AsyncMock(side_effect=EnableGraceUnsafeError("snapshot missing")),
    )
    failed = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_failed", failed)
    enqueue = AsyncMock(return_value=SimpleNamespace(task_id=901, created=True, state="queued"))
    monkeypatch.setattr(
        meta,
        "CommandService",
        lambda _engine: SimpleNamespace(enqueue_verified_pause_compensation=enqueue),
    )

    assert await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    failure_result = failed.await_args.kwargs["result"]
    assert failure_result["outcome"] == "UNKNOWN"
    assert failure_result["reason"] == "enable_grace_compensation_pending"
    assert failure_result["external_status"] == "ACTIVE"
    conn = _LockConnection()
    await failed.await_args.kwargs["transactional_effect"](conn)
    assert enqueue.await_args.kwargs["reason"] == "activation_without_grace"
    assert enqueue.await_args.kwargs["source_task_id"] == 54
    assert enqueue.await_args.kwargs["observed_delivery_status"] == "ACTIVE"
    assert enqueue.await_args.kwargs["connection"] is conn
    assert any("alert_state = 'stop_sent'" in sql for sql, _params in conn.calls)
    assert any("'compensation_task_id'" in sql for sql, _params in conn.calls)


@pytest.mark.asyncio
async def test_autostop_reconciliation_confirmed_paused_uses_atomic_finalizer(monkeypatch) -> None:
    task = _fenced_task("pause_ad", tid=47)
    task.requested_by = "bot_auto_stop"
    task.lane = "money"
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value={"status": "PAUSED", "effective_status": "PAUSED"}
    )
    succeeded = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_succeeded", succeeded)
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", AsyncMock())

    terminal = await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    assert terminal is True
    assert succeeded.await_args.kwargs["result"] == {
        "outcome": "CONFIRMED",
        "reconciled_after_unknown": True,
        "status": "PAUSED",
        "effective_status": "PAUSED",
    }


@pytest.mark.asyncio
async def test_autostop_reconciliation_unknown_never_resolves_incidents(monkeypatch) -> None:
    task = _fenced_task("pause_ad", tid=48)
    task.requested_by = "bot_auto_stop"
    task.lane = "money"
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    payload = meta.MetaMutationPayload.from_dict(task.payload)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(side_effect=TemporaryError("timeout", code=-2))
    requeue = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", requeue)
    succeeded = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "mark_task_succeeded", succeeded)

    terminal = await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    assert terminal is True
    requeue.assert_awaited_once()
    succeeded.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconciliation_lifecycle_failure_defers_without_external_resend(
    monkeypatch,
) -> None:
    task = _fenced_task("activate_ad", tid=56)
    task.result = {"outcome": "UNKNOWN", "reconcile_required": True}
    reconcile = AsyncMock(side_effect=RuntimeError("compensation transaction failed"))
    defer = AsyncMock(return_value=True)
    execute = AsyncMock()
    monkeypatch.setattr(meta, "_reconcile_unknown_status_action", reconcile)
    monkeypatch.setattr(meta, "defer_unknown_reconciliation", defer)
    monkeypatch.setattr(meta, "execute_mutation", execute)

    await meta.process_one_task(
        object(),
        task,
        client=AsyncMock(),
    )

    defer.assert_awaited_once()
    assert defer.await_args.kwargs["task"] is task
    assert defer.await_args.kwargs["delay_seconds"] == 5
    execute.assert_not_awaited()


# ─── Аудит 2026-07-12 (M-2): pre-send ошибки ретраятся даже для необратимых ──


# SessionUnavailableError (circuit-open / Vision не готов) = запрос НЕ ушёл в Meta,
# поэтому retry безопасен для duplicate_adset_structure.
@pytest.mark.asyncio
async def test_duplicate_session_unavailable_requeues(monkeypatch, _patched) -> None:
    from core.meta_api.errors import SessionUnavailableError

    spy_fail, spy_requeue, spy_fail_or_cancelled = _patched
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=SessionUnavailableError("browser-agent недоступен")),
    )
    await meta.process_one_task(object(), _task("duplicate_adset_structure"), client=AsyncMock())
    spy_requeue.assert_not_awaited()
    meta.requeue_task_proven_not_committed.assert_awaited_once()
    assert meta.requeue_task_proven_not_committed.await_args.kwargs["target_lock_key"] == "100"
    spy_fail.assert_not_awaited()
