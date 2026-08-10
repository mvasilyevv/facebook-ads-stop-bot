# -*- coding: utf-8 -*-
"""Персист created_ids partial-провала в task_queue.result (MID-24).

У 8 failed campaign_create (26-27.06) result был NULL — id осиротевших объектов
Meta жили только в ротируемых логах. Теперь PartialCreateError-ветка пишет их
и в task_queue.result (кроме campaign_run.created_meta_ids).
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.campaign_creator_worker.main import _persist_partial_created_ids
from core.tasks.queue import Task


def _task(task_id: int) -> Task:
    now = datetime.now(UTC)
    return Task(
        id=task_id,
        task_type="campaign_create",
        status="running",
        idempotency_key=f"campaign-{task_id}",
        payload={"run_id": "run-1"},
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="bulk",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(minutes=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000042"),
        lease_token=11,
        lease_expires_at=now + timedelta(minutes=30),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


def _fake_engine():
    """engine.begin() как async context manager с мок-conn."""
    conn = AsyncMock()
    conn.execute.return_value = MagicMock(rowcount=1)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin = MagicMock(return_value=ctx)
    return engine, conn


# UPDATE уходит с guard status='running' и полным JSON (partial_fail/step/created_ids)
@pytest.mark.asyncio
async def test_persist_writes_result_json_with_running_guard():
    engine, conn = _fake_engine()
    created = {"campaigns": ["c1"], "adsets": ["s1", "s2"], "ads": []}

    await _persist_partial_created_ids(
        engine, task=_task(42), created_ids=created, failed_step="creating"
    )

    conn.execute.assert_awaited_once()
    sql = str(conn.execute.await_args.args[0])
    params = conn.execute.await_args.args[1]
    assert "SET result" in sql
    assert "status = 'running'" in sql  # не затираем result чужой терминальной задачи
    assert "lease_owner = :lease_owner" in sql
    assert "lease_token = :lease_token" in sql
    assert params["id"] == 42
    payload = json.loads(params["r"])
    assert payload["outcome"] == "UNKNOWN"
    assert payload["manual_review_required"] is True
    assert payload["partial_fail"] is True
    assert payload["failed_step"] == "creating"
    assert payload["created_ids"] == created


# Сбой записи (БД упала) → warning, БЕЗ исключения наружу (mark_failed важнее)
@pytest.mark.asyncio
async def test_persist_failure_is_swallowed(caplog):
    engine = MagicMock()
    engine.begin = MagicMock(side_effect=ConnectionError("db down"))

    with caplog.at_level("WARNING"):
        await _persist_partial_created_ids(
            engine, task=_task(7), created_ids={"campaigns": []}, failed_step="creating"
        )

    assert any("created_ids" in r.getMessage() for r in caplog.records)


# Контракт ветки PartialCreateError: checkpoint пишется до атомарного terminal finalize.
def test_partial_branch_persists_before_terminal_finalize():
    import apps.campaign_creator_worker.main as m

    src = inspect.getsource(m._execute_run)
    branch = src.split("except PartialCreateError")[1].split("except Exception")[0]
    assert "_persist_partial_created_ids" in branch
    assert branch.index("_persist_partial_created_ids") < branch.index("finalize_run_failed")
