"""Crash-safe checkpoint and PAUSE-only recovery for ad-set duplication."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
import core.meta_api.duplicate_incidents as duplicate_incidents
import core.tasks.queue as task_queue_module
from core.adset_duplicates.plan_integrity import duplicate_execution_plan_digest
from core.tasks.queue import (
    Task,
    checkpoint_duplicate_adset_structure,
    fail_stuck_duplicate_without_checkpoint,
    prepare_stuck_duplicate_recovery,
    requeue_duplicate_recovery,
)


class _Result:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount

    def all(self):
        return [SimpleNamespace(_mapping={"id": 42, "result": {}})] if self.rowcount else []


class _Connection:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return _Result(self.rowcount)


class _Engine:
    def __init__(self, rowcount: int = 1) -> None:
        self.connection = _Connection(rowcount)

    def begin(self):
        connection = self.connection

        class _Context:
            async def __aenter__(self):
                return connection

            async def __aexit__(self, *args):
                return False

        return _Context()


def _checkpoint(*, recovery_requested: bool = False) -> dict:
    return {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "phase": "verifying_paused",
        "step": "verify_paused_structure",
        "created_ids": {
            "campaigns": ["1001"],
            "adsets": ["2001"],
            "ads": ["3001"],
        },
        "recovery_requested": recovery_requested,
    }


def _recovery_task(task_id: int, *, params: dict) -> Task:
    now = datetime.now(UTC)
    recovery_params = {
        "source_campaign_id": "101",
        "source_adset_id": "201",
        "selected_ad_ids": ["301"],
        "campaign_count": 1,
        "adsets_per_campaign": 1,
        "budget_level": "ABO",
        "daily_budget": "50.00",
        "currency": "USD",
        "currency_exponent": 2,
        "start_time": "2099-07-16T08:00:00Z",
        "campaign_names": ["recovery campaign"],
        "adset_names": [["recovery adset"]],
        **params,
    }
    payload = {
        "mutation_kind": "duplicate_adset_structure",
        "target_id": "201",
        "params": recovery_params,
        "ad_account_id": "999",
    }
    recovery_params["plan_digest"] = duplicate_execution_plan_digest(**payload).hex()
    return Task(
        id=task_id,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key=f"duplicate-recovery:{task_id}",
        payload=payload,
        result=_checkpoint(recovery_requested=True),
        attempt_count=0,
        max_attempts=1,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=now - timedelta(seconds=5),
        lane="money",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000105"),
        lease_token=5,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


def _recovery_graph_row(object_id: str, *, status: str = "PAUSED") -> dict:
    if object_id == "1001":
        return {
            "id": object_id,
            "account_id": "999",
            "name": "recovery campaign",
            "objective": "OUTCOME_SALES",
            "status": status,
        }
    if object_id == "2001":
        return {
            "id": object_id,
            "account_id": "999",
            "campaign_id": "1001",
            "status": status,
        }
    if object_id == "3001":
        return {
            "id": object_id,
            "account_id": "999",
            "campaign_id": "1001",
            "adset_id": "2001",
            "name": "recovery ad",
            "status": status,
            "creative": {"id": "4001"},
        }
    raise AssertionError(f"unexpected recovery object {object_id}")


@pytest.fixture(autouse=True)
def _fenced_external_boundary(monkeypatch) -> AsyncMock:
    duplicate_boundary = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta,
        "authorize_duplicate_execution_boundary",
        duplicate_boundary,
    )
    return duplicate_boundary


@pytest.fixture(autouse=True)
def _duplicate_incident_projection(monkeypatch) -> AsyncMock:
    projection = AsyncMock(return_value=None)
    monkeypatch.setattr(
        duplicate_incidents,
        "project_duplicate_incident_in_transaction",
        projection,
    )
    return projection


@pytest.mark.asyncio
async def test_checkpoint_is_duplicate_only_and_fenced_after_recovery_request() -> None:
    engine = _Engine()
    lease_owner = uuid.uuid4()

    applied = await checkpoint_duplicate_adset_structure(
        engine,
        task_id=42,
        checkpoint=_checkpoint(),
        lease_owner=lease_owner,
        lease_token=7,
    )

    assert applied is True
    sql, params = engine.connection.calls[0]
    assert "payload->>'mutation_kind' = 'duplicate_adset_structure'" in sql
    assert "result->>'recovery_requested'" in sql
    assert params["lease_owner"] == lease_owner
    assert params["lease_token"] == 7
    assert json.loads(params["checkpoint"])["created_ids"]["adsets"] == ["2001"]


@pytest.mark.asyncio
async def test_stale_recovery_claim_is_rescheduled_even_when_already_requested() -> None:
    """SIGKILL-equivalent: recovery worker died while checkpoint remained running."""
    engine = _Engine()

    count = await prepare_stuck_duplicate_recovery(engine, stuck_after_seconds=10)

    assert count == 1
    sql, _ = engine.connection.calls[0]
    assert "status = 'retrying'" in sql
    assert "result->>'checkpoint_type' = 'duplicate_adset_structure'" in sql
    assert "recovery_requested', 'false'" not in sql


@pytest.mark.asyncio
async def test_no_checkpoint_duplicate_has_separate_fail_path() -> None:
    engine = _Engine()

    count = await fail_stuck_duplicate_without_checkpoint(engine, stuck_after_seconds=10)

    assert count == 1
    sql, _ = engine.connection.calls[0]
    assert "payload->>'mutation_kind' = 'duplicate_adset_structure'" in sql
    assert "jsonb_typeof(result->'created_ids')" in sql
    assert "status = 'failed'" in sql


@pytest.mark.asyncio
async def test_cleanup_retry_ignores_original_max_attempts_and_stays_pause_only() -> None:
    engine = _Engine()
    lease_owner = uuid.uuid4()

    applied = await requeue_duplicate_recovery(
        engine,
        task_id=42,
        checkpoint={**_checkpoint(recovery_requested=True), "phase": "recovery_retrying"},
        error="one PAUSE failed",
        lease_owner=lease_owner,
        lease_token=8,
    )

    assert applied is True
    sql, params = engine.connection.calls[0]
    assert "status = 'retrying'" in sql
    assert "max_attempts" not in sql
    assert "payload->>'mutation_kind' = 'duplicate_adset_structure'" in sql
    assert "result->>'checkpoint_type' = 'duplicate_adset_structure'" not in sql
    assert "result->>'recovery_requested' = 'true'" not in sql
    assert params["lease_owner"] == lease_owner
    assert params["lease_token"] == 8
    assert json.loads(params["checkpoint"])["phase"] == "recovery_retrying"


@pytest.mark.asyncio
async def test_cleanup_retry_stale_fence_does_not_project_incident(
    _duplicate_incident_projection: AsyncMock,
) -> None:
    engine = _Engine(rowcount=0)

    applied = await requeue_duplicate_recovery(
        engine,
        task_id=42,
        checkpoint={**_checkpoint(recovery_requested=True), "phase": "recovery_retrying"},
        error="one PAUSE failed",
        lease_owner=uuid.uuid4(),
        lease_token=8,
    )

    assert applied is False
    _duplicate_incident_projection.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_retry_rejects_incoming_checkpoint_without_recovery_flag() -> None:
    engine = _Engine()
    incoming = _checkpoint(recovery_requested=False)

    with pytest.raises(ValueError, match="invalid duplicate recovery checkpoint"):
        await requeue_duplicate_recovery(
            engine,
            task_id=42,
            checkpoint=incoming,
            error="cleanup failed",
        )

    assert engine.connection.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "created_ids",
    [
        None,
        {"campaigns": [], "adsets": [], "ads": []},
        {"campaigns": ["not-an-id"], "adsets": [], "ads": []},
    ],
)
async def test_cleanup_retry_rejects_missing_empty_or_malformed_created_ids(
    created_ids: object,
) -> None:
    engine = _Engine()
    incoming = _checkpoint(recovery_requested=True)
    if created_ids is None:
        incoming.pop("created_ids")
    else:
        incoming["created_ids"] = created_ids

    with pytest.raises(ValueError):
        await requeue_duplicate_recovery(
            engine,
            task_id=42,
            checkpoint=incoming,
            error="cleanup failed",
            lease_owner=uuid.uuid4(),
            lease_token=8,
        )

    assert engine.connection.calls == []


@pytest.mark.asyncio
async def test_checkpoint_missing_reconciler_projects_only_after_applied_update(
    monkeypatch,
    _duplicate_incident_projection: AsyncMock,
) -> None:
    engine = _Engine()
    row = SimpleNamespace(
        _mapping={
            "id": 42,
            "result": {
                "outcome": "UNKNOWN",
                "reconcile_required": True,
                "reason": "stuck_duplicate_without_checkpoint",
            },
        }
    )
    monkeypatch.setattr(task_queue_module, "_returned_task_rows", lambda result: ([row], 1))

    count = await fail_stuck_duplicate_without_checkpoint(engine, stuck_after_seconds=10)

    assert count == 1
    _duplicate_incident_projection.assert_awaited_once_with(
        engine.connection,
        task_id=42,
        checkpoint=row._mapping["result"],
        stage="checkpoint_missing",
    )


@pytest.mark.asyncio
async def test_checkpoint_missing_reconciler_emits_nothing_when_update_matches_no_rows(
    monkeypatch,
    _duplicate_incident_projection: AsyncMock,
) -> None:
    engine = _Engine(rowcount=0)
    monkeypatch.setattr(task_queue_module, "_returned_task_rows", lambda result: ([], 0))

    count = await fail_stuck_duplicate_without_checkpoint(engine, stuck_after_seconds=10)

    assert count == 0
    _duplicate_incident_projection.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_recovery_pauses_checkpointed_ids_and_never_replays_create(
    monkeypatch,
    _fenced_external_boundary: AsyncMock,
) -> None:
    async def graph_call(**kwargs):
        if kwargs["method"] == "POST":
            return {"success": True}
        object_id = kwargs["endpoint"][1:]
        return _recovery_graph_row(object_id)

    graph = AsyncMock(side_effect=graph_call)
    client = SimpleNamespace(execute_graph_call=graph)
    task = _recovery_task(42, params={"source_adset_id": "201"})
    mark_failed = AsyncMock(return_value=True)
    execute = AsyncMock()
    monkeypatch.setattr(meta, "mark_task_failed", mark_failed)
    monkeypatch.setattr(meta, "execute_mutation", execute)

    await meta.process_one_task(object(), task, client=client)

    _fenced_external_boundary.assert_awaited_once()
    assert _fenced_external_boundary.await_args.kwargs["recovery_checkpoint"] == task.result
    execute.assert_not_awaited()
    assert [call.kwargs["endpoint"] for call in graph.await_args_list] == [
        "/1001",
        "/2001",
        "/3001",
        "/1001",
        "/1001",
        "/2001",
        "/2001",
        "/3001",
        "/3001",
    ]
    assert [call.kwargs["method"] for call in graph.await_args_list] == [
        "GET",
        "GET",
        "GET",
        "POST",
        "GET",
        "POST",
        "GET",
        "POST",
        "GET",
    ]
    pause_calls = [call for call in graph.await_args_list if call.kwargs["method"] == "POST"]
    assert all(call.kwargs["body_json"] == {"status": "PAUSED"} for call in pause_calls)
    mark_failed.assert_awaited_once()
    assert mark_failed.await_args.kwargs["result"]["phase"] == "recovery_paused"
    assert mark_failed.await_args.kwargs["result"]["recovered_after_crash"] is True


@pytest.mark.asyncio
async def test_worker_recovery_requeues_until_every_pause_succeeds(monkeypatch) -> None:
    async def graph_call(**kwargs):
        if kwargs["method"] == "POST" and kwargs["endpoint"] == "/2001":
            raise RuntimeError("adset pause transport failure")
        if kwargs["method"] == "POST":
            return {"success": True}
        object_id = kwargs["endpoint"][1:]
        return _recovery_graph_row(object_id)

    graph = AsyncMock(side_effect=graph_call)
    client = SimpleNamespace(execute_graph_call=graph)
    task = _recovery_task(43, params={})
    defer = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "defer_duplicate_recovery", defer)
    monkeypatch.setattr(meta, "mark_task_failed", AsyncMock())

    await meta.process_one_task(object(), task, client=client)

    defer.assert_awaited_once()
    persisted = defer.await_args.kwargs["checkpoint"]
    assert persisted["phase"] == "recovery_retrying"
    assert persisted["cleanup_failures"][0]["id"] == "2001"
    meta.mark_task_failed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unverified_mode",
    ["empty_ack", "still_active", "inherited_pause_only"],
)
async def test_worker_recovery_stays_recoverable_until_pause_is_verified(
    monkeypatch,
    unverified_mode: str,
) -> None:
    async def graph_call(**kwargs):
        object_id = kwargs["endpoint"][1:]
        if kwargs["method"] == "POST":
            if object_id == "2001" and unverified_mode == "empty_ack":
                return {}
            return {"success": True}
        cleanup_read = kwargs.get("query_params", {}).get("fields") == (
            "id,status,effective_status"
        )
        if cleanup_read and object_id == "2001" and unverified_mode == "inherited_pause_only":
            return {"id": object_id, "effective_status": "PAUSED"}
        status = (
            "ACTIVE"
            if cleanup_read and object_id == "2001" and unverified_mode == "still_active"
            else "PAUSED"
        )
        return _recovery_graph_row(object_id, status=status)

    graph = AsyncMock(side_effect=graph_call)
    client = SimpleNamespace(execute_graph_call=graph)
    task = _recovery_task(44, params={})
    defer = AsyncMock(return_value=True)
    mark_failed = AsyncMock()
    monkeypatch.setattr(meta, "defer_duplicate_recovery", defer)
    monkeypatch.setattr(meta, "mark_task_failed", mark_failed)

    await meta.process_one_task(object(), task, client=client)

    defer.assert_awaited_once()
    mark_failed.assert_not_awaited()
    persisted = defer.await_args.kwargs["checkpoint"]
    assert persisted["phase"] == "recovery_retrying"
    assert [failure["id"] for failure in persisted["cleanup_failures"]] == ["2001"]


def test_recovery_checkpoint_rejects_non_numeric_or_oversized_ids() -> None:
    from core.meta_api.errors import MutationValidationError
    from core.meta_api.mutations.duplicate_adset_structure import DuplicateAdsetStructureHandler

    bad = _checkpoint()
    bad["created_ids"] = {"campaigns": ["not-an-id"], "adsets": [], "ads": []}
    with pytest.raises(MutationValidationError, match="only digits|цифр"):
        DuplicateAdsetStructureHandler.created_ids_from_checkpoint(bad)
