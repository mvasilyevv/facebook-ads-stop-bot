# -*- coding: utf-8 -*-
"""E2E cross-cutting сценарий: TG callback `plan:<id>` → task_queue → creator_worker.

Сшивка трёх доменов:
1. INSERT в `creator_plans` (имитируем результат /record + /stoprecord).
2. `core/telegram/handlers/creator.handle_plan_run_callback` принимает callback
   `plan:<plan_id>` от inline-кнопки и создаёт task_queue запись plan_run.
3. `apps/creator_worker.process_one_task` с fake BrowserAgentClient прогоняет
   событие-стрим RunPlan → task_queue.status='succeeded'.

Дополнительно: callback на archived plan возвращает ack без создания task.
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
from core.tasks.queue import claim_next_task
from core.telegram.handlers.creator import handle_plan_run_callback


@pytest_asyncio.fixture
async def clean_creator_pipeline(pg_engine: AsyncEngine):
    """Чистит plan_run tasks и тестовые creator_plans до/после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'plan_run'"))
            await conn.execute(text("DELETE FROM creator_plans WHERE created_by = 'e2e_test'"))

    await _truncate()
    yield
    await _truncate()


async def _insert_plan(
    pg_engine: AsyncEngine,
    *,
    name: str,
    steps: list[dict[str, Any]],
    is_archived: bool = False,
) -> str:
    """INSERT creator_plans. Возвращает UUID плана."""
    plan_id = str(uuid.uuid4())
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO creator_plans
                    (id, name, schema_version, steps, variables,
                     created_by, is_archived)
                VALUES
                    (:id, :n, 1, CAST(:s AS JSONB), CAST('{}' AS JSONB),
                     'e2e_test', :arch)
                """
            ),
            {
                "id": plan_id,
                "n": name,
                "s": json.dumps(steps),
                "arch": is_archived,
            },
        )
    return plan_id


def _evt(field_name: str, value: Any):
    """PlanEvent-подобный объект с одним заполненным oneof-полем."""

    class _Evt:
        def __init__(self) -> None:
            self._field = field_name
            setattr(self, field_name, value)

        def HasField(self, name: str) -> bool:
            return name == self._field

    return _Evt()


class _FakeBrowserClient:
    """Фейк BrowserAgentClient.run_plan: проигрывает заранее заданный поток событий."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self.calls: list[tuple[str, str]] = []

    def run_plan(self, plan_json: str, variables_json: str):
        self.calls.append((plan_json, variables_json))

        async def _aiter():
            for ev in self._events:
                yield ev

        return _aiter()


class _FakeTGClient:
    """Минимальный TelegramBotClient: фиксирует acks и edit_message_reply_markup."""

    def __init__(self) -> None:
        self.acks: list[tuple[str, str]] = []
        self.edits: list[dict] = []

    async def answer_callback_query(self, cq_id: str, text: str = "") -> None:
        self.acks.append((cq_id, text))

    async def edit_message_reply_markup(self, *, chat_id: str, message_id: int, **_kw) -> None:
        self.edits.append({"chat_id": chat_id, "message_id": message_id})


def _callback_query(*, data: str, user_id: int = 5001) -> dict[str, Any]:
    """Эмуляция TG update.callback_query — то что приходит от long-polling."""
    return {
        "id": f"cq-{uuid.uuid4().hex[:8]}",
        "data": data,
        "from": {"id": user_id, "username": "e2e_user"},
        "message": {
            "message_id": 9999,
            "chat": {"id": 100500, "type": "private"},
        },
    }


# E2E: INSERT plan → callback `plan:<id>` → creator_worker → succeeded
@pytest.mark.asyncio
async def test_full_cycle_callback_to_worker_success(
    pg_engine: AsyncEngine,
    clean_creator_pipeline,
) -> None:
    plan_id = await _insert_plan(
        pg_engine,
        name=f"e2e_plan_{uuid.uuid4().hex[:6]}",
        steps=[{"step": "open_ads_manager"}, {"step": "click_create"}],
    )

    # Шаг 1: пользователь жмёт inline "Запустить" под планом → handle_plan_run_callback
    fake_tg = _FakeTGClient()
    await handle_plan_run_callback(
        _callback_query(data=f"plan:{plan_id}"),
        engine=pg_engine,
        client=fake_tg,
    )
    # ack отправлен пользователю + кнопки убрали
    assert any("Задача #" in t for _, t in fake_tg.acks)
    assert len(fake_tg.edits) == 1

    # Шаг 2: в task_queue появилась plan_run запись
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, status, payload, requested_by
                    FROM task_queue WHERE task_type = 'plan_run'
                    ORDER BY id DESC LIMIT 1
                    """
                )
            )
        ).first()
    assert row is not None
    task_id = int(row[0])
    assert row[1] == "pending"
    assert row[2]["plan_id"] == plan_id
    assert row[3] == "user:5001"

    # Шаг 3: creator_worker claim'ит
    claim = await claim_next_task(pg_engine, task_type="plan_run")
    assert claim.task is not None
    assert claim.task.id == task_id

    # Шаг 4: подменяем browser-agent на фейк со стримом success
    fake_browser = _FakeBrowserClient(
        [
            _evt("started", SimpleNamespace(step="open_ads_manager", index=0, timestamp_ms=0)),
            _evt(
                "finished",
                SimpleNamespace(
                    step="open_ads_manager", index=0, timestamp_ms=10, detail_json="{}"
                ),
            ),
            _evt("started", SimpleNamespace(step="click_create", index=1, timestamp_ms=20)),
            _evt(
                "finished",
                SimpleNamespace(step="click_create", index=1, timestamp_ms=30, detail_json="{}"),
            ),
            _evt(
                "complete",
                SimpleNamespace(ok=True, total_steps=2, duration_ms=42, error=""),
            ),
        ]
    )

    await process_one_task(pg_engine, claim.task, client=fake_browser)

    # Шаг 5: status=succeeded + result содержит steps_executed
    async with pg_engine.connect() as conn:
        final = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :i"),
                {"i": task_id},
            )
        ).first()
    assert final[0] == "succeeded"
    assert final[1]["ok"] is True
    assert final[1]["steps_executed"] == 2
    assert final[1]["total_steps"] == 2

    # Шаг 6: fake_browser реально получил наш plan_json (sanity-check сшивки)
    assert len(fake_browser.calls) == 1
    parsed_plan = json.loads(fake_browser.calls[0][0])
    assert parsed_plan["schema_version"] == 1
    assert len(parsed_plan["steps"]) == 2


# E2E: callback на архивированный план → ack об ошибке, без создания task
@pytest.mark.asyncio
async def test_callback_on_archived_plan_does_not_create_task(
    pg_engine: AsyncEngine,
    clean_creator_pipeline,
) -> None:
    plan_id = await _insert_plan(
        pg_engine,
        name=f"archived_{uuid.uuid4().hex[:6]}",
        steps=[{"step": "noop"}],
        is_archived=True,
    )

    fake_tg = _FakeTGClient()
    await handle_plan_run_callback(
        _callback_query(data=f"plan:{plan_id}"),
        engine=pg_engine,
        client=fake_tg,
    )

    # Пользователь получил отказ
    assert any("архивирован" in t.lower() for _, t in fake_tg.acks)
    # И в task_queue ничего нового
    async with pg_engine.connect() as conn:
        n = (
            await conn.execute(text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'plan_run'"))
        ).scalar()
    assert n == 0


# E2E: callback на несуществующий план → ack об ошибке, без создания task
@pytest.mark.asyncio
async def test_callback_on_missing_plan_does_not_create_task(
    pg_engine: AsyncEngine,
    clean_creator_pipeline,
) -> None:
    fake_tg = _FakeTGClient()
    await handle_plan_run_callback(
        _callback_query(data=f"plan:{uuid.uuid4()}"),
        engine=pg_engine,
        client=fake_tg,
    )

    assert any("не найден" in t.lower() for _, t in fake_tg.acks)
    async with pg_engine.connect() as conn:
        n = (
            await conn.execute(text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'plan_run'"))
        ).scalar()
    assert n == 0
