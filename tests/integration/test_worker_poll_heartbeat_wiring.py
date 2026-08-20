# -*- coding: utf-8 -*-
"""Занятый воркер не выглядит зависшим: отметка опроса живёт всю задачу (#243).

``tests/unit/test_worker_liveness.py`` доказывает, что сам контекстный менеджер
тикает. Это не то же самое, что «воркер им обёрнут»: обе строки проводки —
``apps/campaign_creator_worker/main.py`` и ``apps/meta_api_worker/main.py`` —
можно было удалить, и весь набор оставался зелёным. Дефект первого круга
(занятый воркер числится зависшим, пока грузит видео) вернулся бы молча.

Здесь наблюдается durable-состояние, а не вызов: строка ``worker_heartbeats``
обязана появиться, ПОКА захваченная задача ещё исполняется. Отметку на claim'е
тест не видит намеренно — ``record_worker_heartbeat`` в модуле воркера
подменён, поэтому единственный оставшийся писатель этой строки — обёртка.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text

import core.worker_liveness as worker_liveness

# Каденция тика на время теста: сама проводка от неё не зависит, а ждать
# боевые 15 секунд в наборе нельзя.
_TEST_TICK_SECONDS = 0.02
# Верхняя граница ожидания отметки. Условие с дедлайном, а не сон на глазок:
# не дождались — проводки нет, и это отказ теста, а не «долго идёт».
_MARK_DEADLINE_SECONDS = 10.0


async def _forget_worker_row(pg_engine, worker_name: str) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM worker_heartbeats WHERE worker_name = :worker_name"),
            {"worker_name": worker_name},
        )


async def _await_poll_mark(pg_engine, worker_name: str):
    """Ждёт появления durable-отметки опроса; None — не дождались за дедлайн."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _MARK_DEADLINE_SECONDS
    while loop.time() < deadline:
        async with pg_engine.connect() as conn:
            marked_at = await conn.scalar(
                text(
                    "SELECT last_poll_success_at FROM worker_heartbeats "
                    "WHERE worker_name = :worker_name"
                ),
                {"worker_name": worker_name},
            )
        if marked_at is not None:
            return marked_at
        await asyncio.sleep(_TEST_TICK_SECONDS)
    return None


@pytest.mark.asyncio
async def test_campaign_worker_keeps_the_poll_signal_fresh_while_one_task_runs(
    pg_engine,
    monkeypatch,
) -> None:
    import apps.campaign_creator_worker.main as worker

    monkeypatch.setattr(worker_liveness, "HEARTBEAT_INTERVAL_SECONDS", _TEST_TICK_SECONDS)
    # Отметка на claim'е не должна путаться с тиком обёртки: у строки остаётся
    # ровно один писатель — тот, чью проводку и проверяем.
    monkeypatch.setattr(worker, "record_worker_heartbeat", AsyncMock())
    await _forget_worker_row(pg_engine, worker.WORKER_NAME)

    stop = asyncio.Event()
    task = SimpleNamespace(
        id=1,
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        lease_token=1,
    )
    monkeypatch.setattr(
        worker,
        "_claim",
        AsyncMock(
            return_value=SimpleNamespace(
                queue_empty=False,
                task=task,
                browser_profile_id="campaign-profile-1",
                browser_session_id="campaign-session-1",
                browser_readiness_generation=4,
            )
        ),
    )
    marked_while_running: list = []

    async def _long_running_task(*_args, **_kwargs) -> None:
        marked_while_running.append(await _await_poll_mark(pg_engine, worker.WORKER_NAME))
        stop.set()

    monkeypatch.setattr(worker, "process_one_task", _long_running_task)
    client = MagicMock()
    client.operation_authority.return_value = nullcontext()

    await worker.task_loop(pg_engine, stop, client=client, uploader=object())

    assert marked_while_running and marked_while_running[0] is not None


@pytest.mark.asyncio
async def test_meta_api_worker_keeps_the_poll_signal_fresh_while_one_task_runs(
    pg_engine,
    monkeypatch,
) -> None:
    import apps.meta_api_worker.main as worker

    monkeypatch.setattr(worker_liveness, "HEARTBEAT_INTERVAL_SECONDS", _TEST_TICK_SECONDS)
    monkeypatch.setattr(worker, "record_worker_heartbeat", AsyncMock())
    await _forget_worker_row(pg_engine, worker.WORKER_NAME)

    stop = asyncio.Event()
    task = SimpleNamespace(
        id=7,
        task_type="meta_api_mutation",
        requested_by="bot_auto_stop",
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000007"),
        lease_token=1,
    )
    monkeypatch.setattr(
        worker,
        "claim_browser_ready_mutation_task",
        AsyncMock(
            return_value=SimpleNamespace(
                queue_empty=False,
                task=task,
                browser_profile_id="vision-profile-1",
                browser_readiness_generation=1,
            )
        ),
    )
    marked_while_running: list = []

    async def _long_running_task(*_args, **_kwargs) -> None:
        marked_while_running.append(await _await_poll_mark(pg_engine, worker.WORKER_NAME))
        stop.set()

    monkeypatch.setattr(worker, "process_one_task", _long_running_task)
    client = MagicMock()
    client.operation_authority.return_value = nullcontext()

    await worker.task_loop(pg_engine, stop, client=client, alert_ctx=None)

    assert marked_while_running and marked_while_running[0] is not None
