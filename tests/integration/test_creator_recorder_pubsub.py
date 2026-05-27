# -*- coding: utf-8 -*-
"""Integration: creator_recorder реагирует на Redis pubsub события.

Сценарии:
- record_start → fake StartRecording вызван
- record_stop с непустым plan_json → fake StopRecording вызван + INSERT в creator_plans
- record_stop с пустым планом → ничего не сохраняем

Используем fakeredis (in-memory) + реальный pg_engine.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.creator_recorder.main import (
    CHANNEL_RECORD_START,
    CHANNEL_RECORD_STOP,
    _process_message,
    handle_record_start,
    handle_record_stop,
)


# Чистим creator_plans перед/после теста (только наши, по created_by).
@pytest_asyncio.fixture
async def clean_recorder_plans(pg_engine: AsyncEngine):
    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM creator_plans
                    WHERE created_by IN ('integration_test', 'creator_recorder')
                    """
                )
            )

    await _truncate()
    yield
    await _truncate()


class _FakeBrowserClient:
    """Минимальный fake: записывает вызовы StartRecording/StopRecording."""

    def __init__(
        self,
        *,
        stop_payload: tuple[bool, str, int] = (False, "", 0),
    ) -> None:
        self.start_calls: list[str] = []
        self.stop_calls: int = 0
        self._stop_payload = stop_payload

    async def start_recording(self, plan_name: str) -> tuple[bool, str]:
        self.start_calls.append(plan_name)
        return True, "started"

    async def stop_recording(self) -> tuple[bool, str, int]:
        self.stop_calls += 1
        return self._stop_payload


# handle_record_start дёргает client.start_recording с переданным plan_name.
@pytest.mark.asyncio
async def test_handle_record_start_invokes_grpc():
    client = _FakeBrowserClient()
    ok = await handle_record_start(client, {"plan_name": "my_test_plan"})
    assert ok is True
    assert client.start_calls == ["my_test_plan"]


# handle_record_stop с заполненным плэном сохраняет запись в creator_plans.
@pytest.mark.asyncio
async def test_handle_record_stop_persists_plan(
    pg_engine: AsyncEngine,
    clean_recorder_plans,
):
    plan_name = f"recorded_{uuid.uuid4().hex[:8]}"
    plan_json = json.dumps(
        {
            "schema_version": 1,
            "steps": [{"step": "click", "input": {"selector": "#cta"}}],
            "variables": {},
        }
    )
    client = _FakeBrowserClient(stop_payload=(True, plan_json, 1))

    plan_id = await handle_record_stop(
        client,
        pg_engine,
        {"plan_name": plan_name, "requested_by": "integration_test"},
    )
    assert plan_id is not None
    assert client.stop_calls == 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT name, schema_version, steps, is_archived, created_by
                    FROM creator_plans WHERE id = :i
                    """
                ),
                {"i": plan_id},
            )
        ).first()
    assert row is not None
    assert row[0] == plan_name
    assert int(row[1]) == 1
    assert row[3] is False  # is_archived
    assert row[4] == "integration_test"
    steps = row[2] if isinstance(row[2], list) else json.loads(row[2])
    assert len(steps) == 1
    assert steps[0]["step"] == "click"


# handle_record_stop с пустыми recorded_steps НЕ создаёт запись.
@pytest.mark.asyncio
async def test_handle_record_stop_empty_skips_insert(
    pg_engine: AsyncEngine,
    clean_recorder_plans,
):
    client = _FakeBrowserClient(stop_payload=(True, "", 0))
    plan_id = await handle_record_stop(client, pg_engine, {"plan_name": "empty_one"})
    assert plan_id is None
    assert client.stop_calls == 1


# _process_message + record_start канал → handle_record_start вызван (full e2e dispatcher).
@pytest.mark.asyncio
async def test_process_message_record_start_dispatched(pg_engine: AsyncEngine):
    client = _FakeBrowserClient()
    await _process_message(
        CHANNEL_RECORD_START,
        json.dumps({"plan_name": "dispatched"}),
        client=client,
        engine=pg_engine,
    )
    assert client.start_calls == ["dispatched"]


# _process_message + record_stop канал → handle_record_stop вызван и план сохранён.
@pytest.mark.asyncio
async def test_process_message_record_stop_dispatched(
    pg_engine: AsyncEngine,
    clean_recorder_plans,
):
    plan_name = f"dispatched_{uuid.uuid4().hex[:8]}"
    plan_json = json.dumps(
        {
            "schema_version": 1,
            "steps": [{"step": "open"}, {"step": "click"}],
            "variables": {},
        }
    )
    client = _FakeBrowserClient(stop_payload=(True, plan_json, 2))
    await _process_message(
        CHANNEL_RECORD_STOP,
        json.dumps({"plan_name": plan_name, "requested_by": "integration_test"}),
        client=client,
        engine=pg_engine,
    )
    assert client.stop_calls == 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id FROM creator_plans
                    WHERE name = :n AND created_by = 'integration_test'
                    """
                ),
                {"n": plan_name},
            )
        ).first()
    assert row is not None


# Невалидный JSON в payload не валит обработку — просто логируется и пропускается.
@pytest.mark.asyncio
async def test_process_message_tolerates_bad_json(pg_engine: AsyncEngine):
    client = _FakeBrowserClient()
    await _process_message(CHANNEL_RECORD_START, "not-a-json-{{{", client=client, engine=pg_engine)
    # start_recording не был вызван — payload отбросили
    assert client.start_calls == []


# Неизвестный канал тихо игнорируется — без вызовов и без ошибок.
@pytest.mark.asyncio
async def test_process_message_unknown_channel_noop(pg_engine: AsyncEngine):
    client = _FakeBrowserClient()
    await _process_message(
        "fb_agent:creator:unknown",
        json.dumps({"foo": "bar"}),
        client=client,
        engine=pg_engine,
    )
    assert client.start_calls == []
    assert client.stop_calls == 0
