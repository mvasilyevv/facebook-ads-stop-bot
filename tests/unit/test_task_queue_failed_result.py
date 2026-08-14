"""Atomic persistence contract for structured failed-task results."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.tasks.queue as task_queue
from core.meta_api.queue import mark_task_failed
from core.tasks.queue import mark_failed

_LEASE_OWNER = uuid.UUID("00000000-0000-0000-0000-000000000042")
_LEASE_TOKEN = 3


def _engine(*, rowcount: int = 1):
    execute_result = MagicMock(rowcount=rowcount)
    connection = AsyncMock()
    connection.execute = AsyncMock(return_value=execute_result)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = context
    return engine, connection


@pytest.mark.asyncio
async def test_mark_failed_atomically_writes_structured_result() -> None:
    engine, connection = _engine()
    partial = {
        "partial_fail": True,
        "created_ids": {"campaigns": ["1001"], "adsets": ["2001"], "ads": []},
        "failed_steps": [{"step": "verify", "error": "mismatch"}],
        "cleanup_failures": [],
    }

    applied = await mark_failed(
        engine,
        task_id=42,
        error="partial",
        result=partial,
        lease_owner=_LEASE_OWNER,
        lease_token=_LEASE_TOKEN,
    )

    assert applied is True
    update_call = connection.execute.await_args_list[0]
    statement = str(update_call.args[0])
    params = update_call.args[1]
    assert "SET status = 'failed'" in statement
    assert "result = COALESCE(CAST(:res AS JSONB), result)" in statement
    assert "WHERE id = :id AND status = 'running'" in statement
    assert json.loads(params["res"]) == partial


@pytest.mark.asyncio
async def test_mark_failed_without_result_preserves_existing_json() -> None:
    engine, connection = _engine()

    await mark_failed(
        engine,
        task_id=7,
        error="fenced caller",
        lease_owner=_LEASE_OWNER,
        lease_token=_LEASE_TOKEN,
    )

    update_call = connection.execute.await_args_list[0]
    statement = str(update_call.args[0])
    params = update_call.args[1]
    assert "result = COALESCE(CAST(:res AS JSONB), result)" in statement
    assert params["res"] is None


@pytest.mark.asyncio
async def test_mark_failed_without_fence_fails_closed_without_sql() -> None:
    engine, connection = _engine()

    applied = await mark_failed(engine, task_id=7, error="unfenced")

    assert applied is False
    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_meta_queue_wrapper_forwards_optional_result(monkeypatch) -> None:
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr("core.meta_api.queue.mark_failed", spy)
    result = {"created_ids": {"campaigns": ["1001"]}}

    applied = await mark_task_failed(object(), task_id=9, error="partial", result=result)

    assert applied is True
    assert spy.await_args.kwargs["result"] == result


class _IncidentResult:
    def __init__(self, row) -> None:
        self._row = row

    def first(self):
        return self._row


@pytest.mark.asyncio
async def test_rejected_autostop_keeps_correlated_incident_open(monkeypatch) -> None:
    correlation_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    connection = SimpleNamespace(
        execute=AsyncMock(
            return_value=_IncidentResult(
                SimpleNamespace(
                    id=incident_id,
                    title="Ad STOP",
                    correlation_id=correlation_id,
                )
            )
        )
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(
        "core.telegram.notifications.enqueue_notification_in_transaction",
        enqueue,
    )

    await task_queue._transition_terminal_task(
        connection,
        task_id=42,
        correlation_id=correlation_id,
        phase="failed",
        payload={"mutation_kind": "pause_ad", "target_id": "230011223344"},
        result={"outcome": "REJECTED"},
        requested_by="bot_auto_stop",
        lane="money",
        task_type="meta_api_mutation",
    )

    statement = str(connection.execute.await_args.args[0])
    params = connection.execute.await_args.args[1]
    assert params["status"] == "open"
    assert "resolved_at = NULL" in statement
    assert "'failed'" in statement
    enqueue.assert_awaited_once()
