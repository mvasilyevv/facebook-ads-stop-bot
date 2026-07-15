"""Crash-safe checkpoint and PAUSE-only recovery for ad-set duplication."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
from core.tasks.queue import (
    checkpoint_duplicate_adset_structure,
    fail_stuck_duplicate_without_checkpoint,
    prepare_stuck_duplicate_recovery,
    requeue_duplicate_recovery,
)


class _Result:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount


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
        "checkpoint_version": 1,
        "phase": "activating",
        "step": "activate_adset[2001]",
        "created_ids": {
            "campaigns": ["1001"],
            "adsets": ["2001"],
            "ads": ["3001"],
        },
        "activated_ids": {
            "campaigns": ["1001"],
            "adsets": [],
            "ads": ["3001"],
        },
        "recovery_requested": recovery_requested,
    }


@pytest.mark.asyncio
async def test_checkpoint_is_duplicate_only_and_fenced_after_recovery_request() -> None:
    engine = _Engine()

    applied = await checkpoint_duplicate_adset_structure(
        engine,
        task_id=42,
        checkpoint=_checkpoint(),
    )

    assert applied is True
    sql, params = engine.connection.calls[0]
    assert "payload->>'mutation_kind' = 'duplicate_adset_structure'" in sql
    assert "result->>'recovery_requested'" in sql
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

    applied = await requeue_duplicate_recovery(
        engine,
        task_id=42,
        checkpoint={**_checkpoint(recovery_requested=True), "phase": "recovery_retrying"},
        error="one PAUSE failed",
    )

    assert applied is True
    sql, params = engine.connection.calls[0]
    assert "status = 'retrying'" in sql
    assert "max_attempts" not in sql
    assert "payload->>'mutation_kind' = 'duplicate_adset_structure'" in sql
    assert "result->>'checkpoint_type' = 'duplicate_adset_structure'" in sql
    assert "result->>'recovery_requested' = 'true'" not in sql
    assert json.loads(params["checkpoint"])["phase"] == "recovery_retrying"


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
async def test_worker_recovery_pauses_checkpointed_ids_and_never_replays_create(
    monkeypatch,
) -> None:
    async def graph_call(**kwargs):
        if kwargs["method"] == "POST":
            return {"success": True}
        object_id = kwargs["endpoint"][1:]
        return {"id": object_id, "status": "PAUSED", "effective_status": "PAUSED"}

    graph = AsyncMock(side_effect=graph_call)
    client = SimpleNamespace(execute_graph_call=graph)
    task = SimpleNamespace(
        id=42,
        task_type="meta_api_mutation",
        payload={
            "mutation_kind": "duplicate_adset_structure",
            "target_id": "201",
            "params": {"source_adset_id": "201"},
            "ad_account_id": "act_999",
        },
        result=_checkpoint(recovery_requested=True),
        attempt_count=0,
        max_attempts=1,
        requested_by="test",
    )
    mark_failed = AsyncMock(return_value=True)
    execute = AsyncMock()
    monkeypatch.setattr(meta, "mark_task_failed", mark_failed)
    monkeypatch.setattr(meta, "execute_mutation", execute)
    monkeypatch.setattr(meta, "notify_owners", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "_publish_task_changed", AsyncMock())

    await meta.process_one_task(object(), task, client=client)

    execute.assert_not_awaited()
    assert [call.kwargs["endpoint"] for call in graph.await_args_list] == [
        "/1001",
        "/1001",
        "/2001",
        "/2001",
        "/3001",
        "/3001",
    ]
    assert [call.kwargs["method"] for call in graph.await_args_list] == [
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
        return {"id": object_id, "status": "PAUSED"}

    graph = AsyncMock(side_effect=graph_call)
    client = SimpleNamespace(execute_graph_call=graph)
    task = SimpleNamespace(
        id=43,
        task_type="meta_api_mutation",
        payload={
            "mutation_kind": "duplicate_adset_structure",
            "target_id": "201",
            "params": {},
            "ad_account_id": "act_999",
        },
        result=_checkpoint(recovery_requested=True),
        attempt_count=0,
        max_attempts=1,
        requested_by="test",
    )
    defer = AsyncMock(return_value=True)
    monkeypatch.setattr(meta, "defer_duplicate_recovery", defer)
    monkeypatch.setattr(meta, "mark_task_failed", AsyncMock())
    monkeypatch.setattr(meta, "notify_owners", AsyncMock(return_value=True))

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
        if object_id == "2001" and unverified_mode == "inherited_pause_only":
            return {"id": object_id, "effective_status": "PAUSED"}
        status = "ACTIVE" if object_id == "2001" and unverified_mode == "still_active" else "PAUSED"
        return {"id": object_id, "status": status}

    graph = AsyncMock(side_effect=graph_call)
    client = SimpleNamespace(execute_graph_call=graph)
    task = SimpleNamespace(
        id=44,
        task_type="meta_api_mutation",
        payload={
            "mutation_kind": "duplicate_adset_structure",
            "target_id": "201",
            "params": {},
            "ad_account_id": "act_999",
        },
        result=_checkpoint(recovery_requested=True),
        attempt_count=0,
        max_attempts=1,
        requested_by="test",
    )
    defer = AsyncMock(return_value=True)
    mark_failed = AsyncMock()
    monkeypatch.setattr(meta, "defer_duplicate_recovery", defer)
    monkeypatch.setattr(meta, "mark_task_failed", mark_failed)
    monkeypatch.setattr(meta, "notify_owners", AsyncMock(return_value=True))

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
