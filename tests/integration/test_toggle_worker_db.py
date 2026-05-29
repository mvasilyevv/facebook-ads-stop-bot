# -*- coding: utf-8 -*-
"""Интеграционные тесты disable/enable воркеров через fake gate + real DB."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.tasks import create_task
from core.tasks.toggle_executor import execute_one_toggle_task


@pytest_asyncio.fixture
async def clean_task_queue(pg_engine):
    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue"))

    await _truncate()
    yield
    await _truncate()


class _RecordingGate:
    """Fake ToggleGate — записывает все вызовы + программируемый ответ."""

    def __init__(self, *, succeed: bool = True, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._succeed = succeed
        self._raise_exc = raise_exc

    async def toggle_ad(self, fb_ad_id: str, target_state: bool = True) -> dict[str, Any]:
        self.calls.append({"fb_ad_id": fb_ad_id, "target_state": target_state})
        if self._raise_exc is not None:
            raise self._raise_exc
        return {
            "success": self._succeed,
            "final_state": "true" if target_state else "false",
        }


# Сценарий: disable task → gate.toggle_ad(target_state=False) → mark_succeeded
@pytest.mark.asyncio
async def test_disable_task_full_flow(pg_engine, clean_task_queue) -> None:
    fb_ad_id = f"23000{uuid.uuid4().hex[:8]}"
    await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"d-{fb_ad_id}",
        payload={"fb_ad_id": fb_ad_id},
        requested_by="test",
    )

    gate = _RecordingGate(succeed=True)
    outcome = await execute_one_toggle_task(pg_engine, task_type="disable", gate=gate)

    assert outcome == "succeeded"
    assert len(gate.calls) == 1
    assert gate.calls[0]["fb_ad_id"] == fb_ad_id
    assert gate.calls[0]["target_state"] is False  # disable

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, result, completed_at FROM task_queue WHERE idempotency_key = :k"
                ),
                {"k": f"d-{fb_ad_id}"},
            )
        ).first()
    assert row[0] == "succeeded"
    assert row[1]["final_state"] == "false"
    assert row[2] is not None


# Сценарий: enable task → target_state=True
@pytest.mark.asyncio
async def test_enable_task_calls_with_target_true(pg_engine, clean_task_queue) -> None:
    fb_ad_id = f"24000{uuid.uuid4().hex[:8]}"
    await create_task(
        pg_engine,
        task_type="enable",
        idempotency_key=f"e-{fb_ad_id}",
        payload={"fb_ad_id": fb_ad_id},
        requested_by="test",
    )

    gate = _RecordingGate(succeed=True)
    outcome = await execute_one_toggle_task(pg_engine, task_type="enable", gate=gate)

    assert outcome == "succeeded"
    assert gate.calls[0]["target_state"] is True


# Сценарий: пустая очередь → 'idle', gate не вызывается
@pytest.mark.asyncio
async def test_idle_when_queue_empty(pg_engine, clean_task_queue) -> None:
    gate = _RecordingGate()
    outcome = await execute_one_toggle_task(pg_engine, task_type="disable", gate=gate)
    assert outcome == "idle"
    assert gate.calls == []


# Сценарий: gate бросает Exception → retrying, attempt_count++
@pytest.mark.asyncio
async def test_grpc_error_triggers_retry(pg_engine, clean_task_queue) -> None:
    fb_ad_id = f"25000{uuid.uuid4().hex[:8]}"
    await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"err-{fb_ad_id}",
        payload={"fb_ad_id": fb_ad_id},
        requested_by="test",
        max_attempts=3,
    )

    gate = _RecordingGate(raise_exc=ConnectionRefusedError("browser-agent down"))
    outcome = await execute_one_toggle_task(pg_engine, task_type="disable", gate=gate)
    assert outcome == "retrying"

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, attempt_count, last_error FROM task_queue "
                    "WHERE idempotency_key = :k"
                ),
                {"k": f"err-{fb_ad_id}"},
            )
        ).first()
    assert row[0] == "retrying"
    assert row[1] == 1
    assert "browser-agent down" in row[2]


# Сценарий: gate возвращает success=false → retrying (TS-сторона не подтвердила клик)
@pytest.mark.asyncio
async def test_toggle_success_false_triggers_retry(pg_engine, clean_task_queue) -> None:
    fb_ad_id = f"26000{uuid.uuid4().hex[:8]}"
    await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"nack-{fb_ad_id}",
        payload={"fb_ad_id": fb_ad_id},
        requested_by="test",
    )

    gate = _RecordingGate(succeed=False)
    outcome = await execute_one_toggle_task(pg_engine, task_type="disable", gate=gate)
    assert outcome == "retrying"


# Сценарий: payload без fb_ad_id → requeue (или fail если max_attempts достигнут)
@pytest.mark.asyncio
async def test_missing_fb_ad_id_in_payload(pg_engine, clean_task_queue) -> None:
    await create_task(
        pg_engine,
        task_type="disable",
        idempotency_key=f"bad-{uuid.uuid4().hex[:8]}",
        payload={"wrong_key": "x"},
        requested_by="test",
        max_attempts=2,
    )

    gate = _RecordingGate()
    outcome = await execute_one_toggle_task(pg_engine, task_type="disable", gate=gate)
    # gate не вызывался — payload невалидный, поставили retry
    assert gate.calls == []
    assert outcome in ("retrying", "failed")
