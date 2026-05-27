# -*- coding: utf-8 -*-
"""Integration: creator_worker lifecycle на реальном Postgres.

Сценарии:
- creator_plans (active) + task_queue(plan_run) → process_one_task с fake gRPC stream → succeeded
- архивированный plan → mark_failed
- StepFailed в stream → mark_failed
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.creator_worker.main import process_one_task
from core.tasks.queue import claim_next_task, create_task


# Чистим task_queue (plan_run) и creator_plans перед и после теста.
@pytest_asyncio.fixture
async def clean_creator_tables(pg_engine: AsyncEngine):
    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'plan_run'"))
            await conn.execute(
                text("DELETE FROM creator_plans WHERE created_by = 'integration_test'")
            )

    await _truncate()
    yield
    await _truncate()


async def _insert_active_plan(
    pg_engine: AsyncEngine,
    *,
    name: str,
    steps: list[dict[str, Any]] | None = None,
) -> str:
    """INSERT в creator_plans с is_archived=false. Возвращает UUID."""
    plan_id = str(uuid.uuid4())
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO creator_plans
                    (id, name, schema_version, steps, variables, created_by, is_archived)
                VALUES
                    (:id, :n, 1, CAST(:s AS JSONB), CAST('{}' AS JSONB),
                     'integration_test', false)
                """
            ),
            {
                "id": plan_id,
                "n": name,
                "s": json.dumps(steps or [{"step": "noop"}]),
            },
        )
    return plan_id


def _event(field_name: str, value: Any):
    """PlanEvent-подобный объект с одним заполненным oneof-полем."""

    class _Evt:
        def __init__(self) -> None:
            self._field = field_name
            setattr(self, field_name, value)

        def HasField(self, name: str) -> bool:
            return name == self._field

    return _Evt()


class _FakeBrowserClient:
    """Фейк BrowserAgentClient: run_plan(...) проигрывает заранее заданные события."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self.calls: list[tuple[str, str]] = []

    def run_plan(self, plan_json: str, variables_json: str):
        self.calls.append((plan_json, variables_json))

        async def _aiter():
            for ev in self._events:
                yield ev

        return _aiter()


# Активный план + plan_run task: stream(success) → task в БД переходит в succeeded.
@pytest.mark.asyncio
async def test_lifecycle_active_plan_succeeds(
    pg_engine: AsyncEngine,
    clean_creator_tables,
):
    plan_id = await _insert_active_plan(
        pg_engine,
        name=f"plan_{uuid.uuid4().hex[:8]}",
        steps=[{"step": "open_ads_manager"}, {"step": "click_create"}],
    )
    task_id = await create_task(
        pg_engine,
        task_type="plan_run",
        idempotency_key=f"plan_run:test:{uuid.uuid4().hex}",
        payload={"plan_id": plan_id},
        requested_by="integration_test",
    )
    assert task_id is not None

    claim = await claim_next_task(pg_engine, task_type="plan_run")
    assert claim.task is not None
    assert claim.task.id == task_id

    fake_client = _FakeBrowserClient(
        [
            _event("started", SimpleNamespace(step="s1", index=0, timestamp_ms=0)),
            _event(
                "finished",
                SimpleNamespace(step="s1", index=0, timestamp_ms=0, detail_json="{}"),
            ),
            _event("started", SimpleNamespace(step="s2", index=1, timestamp_ms=0)),
            _event(
                "finished",
                SimpleNamespace(step="s2", index=1, timestamp_ms=0, detail_json="{}"),
            ),
            _event(
                "complete",
                SimpleNamespace(ok=True, total_steps=2, duration_ms=5000, error=""),
            ),
        ]
    )

    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "succeeded"
    result = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    assert result["steps_executed"] == 2
    assert result["total_steps"] == 2
    assert result["ok"] is True

    # Fake клиент действительно получил наш plan_json
    assert len(fake_client.calls) == 1
    plan_json_arg = json.loads(fake_client.calls[0][0])
    assert plan_json_arg["schema_version"] == 1
    assert len(plan_json_arg["steps"]) == 2


# Архивированный план → mark_failed без вызова клиента.
@pytest.mark.asyncio
async def test_lifecycle_archived_plan_marked_failed(
    pg_engine: AsyncEngine,
    clean_creator_tables,
):
    plan_id = str(uuid.uuid4())
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO creator_plans
                    (id, name, schema_version, steps, variables, created_by, is_archived)
                VALUES
                    (:id, :n, 1, CAST('[]' AS JSONB), CAST('{}' AS JSONB),
                     'integration_test', true)
                """
            ),
            {"id": plan_id, "n": f"archived_{uuid.uuid4().hex[:8]}"},
        )
    task_id = await create_task(
        pg_engine,
        task_type="plan_run",
        idempotency_key=f"plan_run:archived:{uuid.uuid4().hex}",
        payload={"plan_id": plan_id},
        requested_by="integration_test",
    )
    assert task_id is not None

    claim = await claim_next_task(pg_engine, task_type="plan_run")
    assert claim.task is not None

    fake_client = _FakeBrowserClient([])
    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "failed"
    assert "not found" in (row[1] or "") or "archived" in (row[1] or "")
    # Stream так и не запускался
    assert fake_client.calls == []


# StepFailed в stream → task failed (а не retrying, потому что это бизнес-ошибка плана).
@pytest.mark.asyncio
async def test_lifecycle_step_failed_marks_failed(
    pg_engine: AsyncEngine,
    clean_creator_tables,
):
    plan_id = await _insert_active_plan(
        pg_engine,
        name=f"plan_fail_{uuid.uuid4().hex[:8]}",
        steps=[{"step": "click_submit"}],
    )
    task_id = await create_task(
        pg_engine,
        task_type="plan_run",
        idempotency_key=f"plan_run:fail:{uuid.uuid4().hex}",
        payload={"plan_id": plan_id},
        requested_by="integration_test",
    )
    assert task_id is not None

    claim = await claim_next_task(pg_engine, task_type="plan_run")
    assert claim.task is not None

    fake_client = _FakeBrowserClient(
        [
            _event("started", SimpleNamespace(step="click_submit", index=0, timestamp_ms=0)),
            _event(
                "failed",
                SimpleNamespace(
                    step="click_submit", index=0, error="selector missing", timestamp_ms=0
                ),
            ),
            _event(
                "complete",
                SimpleNamespace(ok=False, total_steps=1, duration_ms=999, error="aborted"),
            ),
        ]
    )

    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, last_error FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert row is not None
    assert row[0] == "failed"
    # last_error берётся из PlanComplete.error если он есть (финальный диагноз)
    assert (row[1] or "") in ("aborted", "selector missing")
