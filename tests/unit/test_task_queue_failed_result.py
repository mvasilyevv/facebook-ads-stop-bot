"""Atomic persistence contract for structured failed-task results."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.meta_api.queue import mark_task_failed
from core.tasks.queue import mark_failed


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

    applied = await mark_failed(engine, task_id=42, error="partial", result=partial)

    assert applied is True
    statement = str(connection.execute.await_args.args[0])
    params = connection.execute.await_args.args[1]
    assert "SET status = 'failed'" in statement
    assert "result = COALESCE(CAST(:res AS JSONB), result)" in statement
    assert "WHERE id = :id AND status = 'running'" in statement
    assert json.loads(params["res"]) == partial


@pytest.mark.asyncio
async def test_mark_failed_without_result_preserves_existing_json() -> None:
    engine, connection = _engine()

    await mark_failed(engine, task_id=7, error="legacy caller")

    statement = str(connection.execute.await_args.args[0])
    params = connection.execute.await_args.args[1]
    assert "result = COALESCE(CAST(:res AS JSONB), result)" in statement
    assert params["res"] is None


@pytest.mark.asyncio
async def test_meta_queue_wrapper_forwards_optional_result(monkeypatch) -> None:
    spy = AsyncMock(return_value=True)
    monkeypatch.setattr("core.meta_api.queue.mark_failed", spy)
    result = {"created_ids": {"campaigns": ["1001"]}}

    applied = await mark_task_failed(object(), task_id=9, error="partial", result=result)

    assert applied is True
    assert spy.await_args.kwargs["result"] == result
