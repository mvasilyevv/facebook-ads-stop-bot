# -*- coding: utf-8 -*-
"""Unit: логический провал mutation (success=False) НЕ метится succeeded (R3, HIGH).

Batch-конверт Graph API даёт HTTP 200, но пер-саб ошибки лежат в теле. Handler
(bulk_status_change при полном отказе Meta)
возвращает dict без exception, в котором success=False (или для bulk succeeded==0 &
failed>0). Раньше process_one_task после execute_mutation БЕЗУСЛОВНО звал
mark_task_succeeded и не читал result['success'] → bulk-стоп при полном отказе метился
succeeded, durable failure event не создавался, объявления тратили бюджет.

Фикс: после execute_mutation проверять полный handler contract — провал → mark_failed
+ durable failure event в той же транзакции. Partial bulk (часть failed) →
UNKNOWN + FSM-sync только подтверждённых modified_ids + ручная сверка.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import Task


def _task(kind: str, tid: int = 1, requested_by: str = "") -> Task:
    now = datetime.now(UTC)
    return Task(
        id=tid,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key=f"meta:{kind}:{tid}",
        payload={"mutation_kind": kind, "target_id": "100", "ad_account_id": "123"},
        requested_by=requested_by,
        attempt_count=0,
        max_attempts=5,
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="money",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        lease_token=1,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


@pytest.fixture
def _patched(monkeypatch):
    """Сканирование включено + owner-фильтр выключен → доходим до execute_mutation.

    Спаим mark_task_succeeded/mark_task_failed/sync_fsm, чтобы проверить,
    какая ветка отработала без живой БД.
    """
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(
        meta,
        "check_mutation_ownership",
        AsyncMock(return_value=SimpleNamespace(allowed=True, not_found=False, reason="")),
    )
    spy_succeed = AsyncMock(return_value=True)
    spy_fail = AsyncMock(return_value=True)
    spy_fsm = AsyncMock()
    monkeypatch.setattr(meta, "mark_task_succeeded", spy_succeed)
    monkeypatch.setattr(meta, "mark_task_failed", spy_fail)
    monkeypatch.setattr(meta, "sync_fsm_after_mutation", spy_fsm)
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    return SimpleNamespace(succeed=spy_succeed, fail=spy_fail, fsm=spy_fsm)


# ====================== process_one_task маршрутизация ======================


@pytest.mark.asyncio
async def test_process_rejects_unfenced_task_before_any_external_call(monkeypatch) -> None:
    task = _task("pause_ad")
    task.lease_owner = None
    execute = AsyncMock()
    monkeypatch.setattr(meta, "execute_mutation", execute)

    with pytest.raises(ValueError, match="valid lease fence"):
        await meta.process_one_task(object(), task, client=AsyncMock())

    execute.assert_not_awaited()


# bulk полный отказ Meta (success=True, succeeded=0, failed=3) → mark_failed, НЕ succeeded
@pytest.mark.asyncio
async def test_bulk_full_fail_marks_failed_not_succeeded(monkeypatch, _patched) -> None:
    result = {
        "success": True,
        "modified_ids": [],
        "succeeded": 0,
        "failed": 3,
        "sub_results": [
            {"id": "1", "success": False, "code": 400},
            {"id": "2", "success": False, "code": 400},
            {"id": "3", "success": False, "code": 400},
        ],
    }
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(
        object(), _task("bulk_status_change", requested_by="bot_auto_stop"), client=AsyncMock()
    )
    _patched.fail.assert_awaited_once()
    _patched.succeed.assert_not_awaited()
    assert _patched.fail.await_args.kwargs["result"] == {
        **result,
        "outcome": "REJECTED",
        "reason": "bulk_all_rejected",
    }
    # Durable money-fail event создаётся внутри mark_task_failed.


# Bulk partial → terminal UNKNOWN; подтверждённые modified_ids всё равно идут в FSM.
@pytest.mark.asyncio
async def test_bulk_partial_is_unknown_and_requires_manual_reconciliation(
    monkeypatch, _patched
) -> None:
    result = {
        "success": True,
        "modified_ids": ["1", "2"],
        "succeeded": 2,
        "failed": 1,
        "sub_results": [
            {"id": "1", "success": True, "code": 200},
            {"id": "2", "success": True, "code": 200},
            {"id": "3", "success": False, "code": 400},
        ],
    }
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(
        object(), _task("bulk_status_change", requested_by="bot_auto_stop"), client=AsyncMock()
    )
    _patched.fail.assert_awaited_once()
    _patched.succeed.assert_not_awaited()
    failure = _patched.fail.await_args.kwargs["result"]
    assert failure["outcome"] == "UNKNOWN"
    assert failure["reconcile_required"] is True
    assert failure["manual_review_required"] is True
    assert failure["reason"] == "bulk_partially_applied"
    await _patched.fail.await_args.kwargs["transactional_effect"](object())
    _patched.fsm.assert_awaited_once()  # FSM-sync по modified_ids


@pytest.mark.asyncio
async def test_terminal_transaction_rolls_back_when_fsm_projection_fails(
    monkeypatch,
) -> None:
    async def terminal_writer(_engine, *, transactional_effect, **_kwargs):
        await transactional_effect(object())
        return True

    monkeypatch.setattr(meta, "mark_task_succeeded", terminal_writer)
    monkeypatch.setattr(
        meta,
        "sync_fsm_after_mutation",
        AsyncMock(side_effect=RuntimeError("fsm projection failed")),
    )
    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id="100",
        ad_account_id="123",
    )

    with pytest.raises(RuntimeError, match="fsm projection failed"):
        await meta._mark_confirmed_with_grace(
            object(),
            _task("pause_ad"),
            payload=payload,
            result={"outcome": "CONFIRMED", "modified_ids": ["100"]},
            prepared_grace=None,
        )


# Контраст: полный успех (success=True, succeeded=N, failed=0) → succeeded, без алерта
@pytest.mark.asyncio
async def test_full_success_no_alert(monkeypatch, _patched) -> None:
    result = {
        "success": True,
        "modified_ids": ["1", "2", "3"],
        "succeeded": 3,
        "failed": 0,
        "sub_results": [
            {"id": "1", "success": True, "code": 200},
            {"id": "2", "success": True, "code": 200},
            {"id": "3", "success": True, "code": 200},
        ],
    }
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(
        object(), _task("bulk_status_change", requested_by="bot_auto_stop"), client=AsyncMock()
    )
    _patched.succeed.assert_awaited_once()
    _patched.fail.assert_not_awaited()


# Контраст: обычный pause_ad success=True → succeeded, без алерта
@pytest.mark.asyncio
async def test_pause_ad_success(monkeypatch, _patched) -> None:
    result = {"success": True, "modified_ids": ["100"]}
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=result))
    await meta.process_one_task(object(), _task("pause_ad"), client=AsyncMock())
    _patched.succeed.assert_awaited_once()
    _patched.fail.assert_not_awaited()


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (None, "handler_result_not_object"),
        ({}, "handler_success_not_boolean"),
        ({"success": "true", "modified_ids": ["100"]}, "handler_success_not_boolean"),
        ({"success": True}, "status_handler_modified_ids_mismatch"),
        (
            {
                "success": True,
                "modified_ids": ["100"],
                "succeeded": 1,
                "failed": 1,
                "sub_results": [{"id": "100", "success": True, "code": 200}],
            },
            "bulk_result_counts_mismatch",
        ),
        (
            {
                "success": False,
                "modified_ids": ["100"],
                "succeeded": 1,
                "failed": 0,
                "sub_results": [{"id": "100", "success": True, "code": 200}],
            },
            "bulk_acknowledgement_conflict",
        ),
    ],
)
def test_handler_contract_is_fail_closed(result, reason: str) -> None:
    kind = "bulk_status_change" if "succeeded" in (result or {}) else "pause_ad"
    payload = MetaMutationPayload(
        mutation_kind=kind,
        target_id="100",
        ad_account_id="123",
        params={"action": "pause", "ad_ids": ["100"]} if kind == "bulk_status_change" else {},
    )

    assessment = meta.assess_mutation_result(payload, result)

    assert assessment.state == "invalid"
    assert assessment.reason == reason


def test_bulk_top_level_rejection_cannot_hide_confirmed_partial_writes() -> None:
    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="bulk:2",
        ad_account_id="123",
        params={"action": "pause", "ad_ids": ["100", "200"]},
    )

    assessment = meta.assess_mutation_result(
        payload,
        {
            "success": False,
            "modified_ids": ["100"],
            "succeeded": 1,
            "failed": 1,
            "sub_results": [
                {"id": "100", "success": True, "code": 200},
                {"id": "200", "success": False, "code": 400},
            ],
        },
    )

    assert assessment.state == "partial"
    assert assessment.reason == "bulk_partially_applied"


@pytest.mark.asyncio
async def test_malformed_handler_result_is_terminal_unknown_not_confirmed(
    monkeypatch,
    _patched,
) -> None:
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(return_value=None))

    await meta.process_one_task(object(), _task("pause_ad"), client=AsyncMock())

    _patched.succeed.assert_not_awaited()
    _patched.fail.assert_awaited_once()
    result = _patched.fail.await_args.kwargs["result"]
    assert result["outcome"] == "UNKNOWN"
    assert result["manual_review_required"] is True
    assert result["contract_error"] == "handler_result_not_object"


@pytest.mark.parametrize(
    "observed",
    [
        None,
        [],
        {"status": True},
        {"status": "unexpected"},
        {"status": "ACTIVE", "effective_status": False},
    ],
)
@pytest.mark.asyncio
async def test_malformed_status_reconciliation_stays_unknown_without_resend(
    monkeypatch,
    observed,
) -> None:
    requeue_unknown = AsyncMock(return_value=True)
    resolve_not_applied = AsyncMock(return_value="running")
    mark_succeeded = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "requeue_unknown_for_reconciliation", requeue_unknown)
    monkeypatch.setattr(meta, "resolve_status_reconciliation_not_applied", resolve_not_applied)
    monkeypatch.setattr(meta, "mark_task_succeeded", mark_succeeded)
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value=observed)
    task = _task("pause_ad")
    payload = MetaMutationPayload(ad_account_id="123", mutation_kind="pause_ad", target_id="100")

    completed = await meta._reconcile_unknown_status_action(
        object(),
        task,
        payload,
        client=client,
    )

    assert completed is True
    requeue_unknown.assert_awaited_once()
    resolve_not_applied.assert_not_awaited()
    mark_succeeded.assert_not_awaited()
    client.execute_graph_call.assert_awaited_once()
    assert client.execute_graph_call.await_args.kwargs["method"] == "GET"


@pytest.mark.asyncio
async def test_curator_grace_is_applied_before_terminal_success(monkeypatch, _patched) -> None:
    """Grace runs inside the terminal task transaction, before it can commit."""
    events: list[str] = []
    prepared = object()

    async def persist_grace(*args, **kwargs):
        events.append("grace")

    async def mark_succeeded(*args, **kwargs):
        await kwargs["transactional_effect"](object())
        events.append("succeeded")
        return True

    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(return_value={"success": True, "modified_ids": ["100"]}),
    )
    monkeypatch.setattr(
        meta,
        "_prepare_enable_grace_for_payload",
        AsyncMock(return_value=prepared),
    )
    monkeypatch.setattr(meta, "persist_enable_grace", persist_grace)
    monkeypatch.setattr(meta, "mark_task_succeeded", mark_succeeded)

    await meta.process_one_task(object(), _task("activate_ad"), client=AsyncMock())

    assert events == ["grace", "succeeded"]
