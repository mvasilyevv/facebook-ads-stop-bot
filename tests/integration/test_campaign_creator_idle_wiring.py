# -*- coding: utf-8 -*-
"""Пустой claim залива объясняется оператору, а не проглатывается (#251).

``explain_browser_claim_block`` и ``note_waiting_reason`` покрыты каждый в
изоляции, но их ПОДКЛЮЧЕНИЕ к ``task_loop`` не было закреплено ничем: ни один
тест цикла не доходил до ветки ``queue_empty=True``, поэтому всю ветку можно
было удалить и весь набор оставался зелёным. Ровно этот дефект 18.08.2026
оставил залив лежать до дедлайна с текстом про следствие вместо причины.

Наблюдается durable-состояние, которое видит оператор (``campaign_run.progress``
и отметка опроса в ``worker_heartbeats``), а не факт вызова функции.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.tasks.queue import create_task


@pytest_asyncio.fixture
async def browser_channel_never_confirmed(pg_engine):
    """Канал браузера ни разу не подтвердил готовность — гейт claim закрыт.

    Строка ``vision_config`` нужна самому диагнозу (он читает ожидаемый профиль),
    поэтому она создаётся, если её ещё нет, но чужая не переписывается.
    """
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO vision_config (x_token_encrypted, profile_id, singleton_key)
                VALUES ('synthetic-idle-wiring-token', 'idle-wiring-profile', 'default')
                ON CONFLICT (singleton_key) DO NOTHING
                """
            )
        )
        await conn.execute(text("DELETE FROM browser_channel_readiness WHERE channel = 'meta_api'"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM browser_channel_readiness WHERE channel = 'meta_api'"))


@pytest_asyncio.fixture
async def clean_idle_queue(pg_engine):
    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))
            await conn.execute(text("DELETE FROM campaign_run"))

    await _truncate()
    yield
    await _truncate()


async def _seed_queued_run(pg_engine) -> str:
    run_id = str(uuid.uuid4())
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO campaign_run (id, config, status, idempotency_key) "
                "VALUES (:id, CAST(:cfg AS JSONB), 'queued', :ik)"
            ),
            {"id": run_id, "cfg": json.dumps({}), "ik": f"idle-wiring-{run_id}"},
        )
    task_id = await create_task(
        pg_engine,
        task_type="campaign_create",
        idempotency_key=f"idle-wiring-task-{run_id}",
        payload={"run_id": run_id},
        requested_by="test",
    )
    assert task_id is not None
    return run_id


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_unclaimable_launch_learns_why_it_waits_instead_of_lying_queued(
    pg_engine,
    clean_idle_queue,
    browser_channel_never_confirmed,
    monkeypatch,
) -> None:
    """Залив, который гейт не отдаёт, получает причину ожидания в свой прогон."""
    import apps.campaign_creator_worker.main as worker

    run_id = await _seed_queued_run(pg_engine)

    stop = asyncio.Event()

    async def _stop_instead_of_sleeping(_stop: asyncio.Event) -> None:
        stop.set()

    monkeypatch.setattr(worker, "_sleep_or_stop", _stop_instead_of_sleeping)
    client = MagicMock()

    await worker.task_loop(pg_engine, stop, client=client, uploader=object())

    async with pg_engine.connect() as conn:
        status, reason = (
            await conn.execute(
                text(
                    "SELECT status, progress->>'waiting_reason' FROM campaign_run WHERE id = :rid"
                ),
                {"rid": run_id},
            )
        ).one()

    # Статус не подделан: залив действительно ещё не начинался.
    assert status == "queued"
    # Названа причина, а не следствие. Точный текст — контракт с оператором:
    # он читает эту строку в карточке прогона.
    assert reason == "Жду готовности браузера: канал браузера ни разу не подтвердил готовность."
    # Задача осталась в очереди: объяснение не подменяет собой исполнение.
    async with pg_engine.connect() as conn:
        queued = await conn.scalar(
            text(
                "SELECT count(*) FROM task_queue "
                "WHERE task_type = 'campaign_create' AND status = 'pending'"
            )
        )
    assert queued == 1


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_idle_launch_worker_marks_its_poll_even_with_nothing_to_claim(
    pg_engine,
    clean_idle_queue,
    monkeypatch,
) -> None:
    """Пустая очередь — здоровое состояние, и воркер обязан выглядеть здоровым.

    Отметка опроса стоит ДО ветки пустого claim'а: перенеси её ниже — и
    простаивающий воркер молча начнёт числиться зависшим, хотя очередь он
    честно опрашивает.
    """
    import apps.campaign_creator_worker.main as worker

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM worker_heartbeats WHERE worker_name = :name"),
            {"name": worker.WORKER_NAME},
        )

    stop = asyncio.Event()

    async def _stop_instead_of_sleeping(_stop: asyncio.Event) -> None:
        stop.set()

    monkeypatch.setattr(worker, "_sleep_or_stop", _stop_instead_of_sleeping)

    await worker.task_loop(pg_engine, stop, client=MagicMock(), uploader=object())

    async with pg_engine.connect() as conn:
        marked_at = await conn.scalar(
            text("SELECT last_poll_success_at FROM worker_heartbeats WHERE worker_name = :name"),
            {"name": worker.WORKER_NAME},
        )
    assert marked_at is not None
