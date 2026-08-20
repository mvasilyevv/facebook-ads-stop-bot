# -*- coding: utf-8 -*-
"""Окно на работу залива открывается захватом задачи — проверено исполнением (#203).

Залив полосы ``bulk`` создаёт объекты в рекламном кабинете необратимо. Пока
абсолютный дедлайн оставался тем, что выставлен при постановке в очередь,
задача, пролежавшая почти всё окно ожидания, стартовала с остатком, которого
заведомо не хватает, и её срезало посередине: созданные объекты оставались
сиротами, а исход — ``UNKNOWN`` с ручной сверкой.

Тесты ИСПОЛНЯЮТ настоящий claim на PostgreSQL и смотрят на значение
``deadline_at`` у забранной задачи. Проверка текста запроса здесь запрещена
намеренно: 20.08.2026 узкие тесты на подстроку были зелёными, пока claim не
исполнялся вообще (``make_interval(secs => text)``) и воркер не брал ни одной
задачи. Значение дедлайна такую поломку показывает сразу — забранной задачи
просто не будет.

НЕ гонять на боевой :5433 — нужен изолированный TEST_DATABASE_URL.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest_asyncio
from sqlalchemy import text

from apps.campaign_creator_worker import claim_campaign_task
from core.tasks.queue import Task, create_task, infer_task_lane

# Окно полосы bulk. Число записано здесь, а не импортировано из очереди: тест
# обязан упасть, если окно залива тихо уменьшат, а не переехать вслед за ним.
_BULK_WINDOW = timedelta(minutes=30)

# Сколько задача пролежала незабранной, ожидая готовности канала браузера.
# Минута до предельного срока ожидания — ровно случай из #203.
_WAITED_IN_QUEUE = _BULK_WINDOW - timedelta(minutes=1)

# Часы БД читаются двумя отдельными запросами вокруг захвата; между ними
# проходит сам claim. Всё, что больше, означает не медленный тест, а тормоза,
# при которых измерение окна теряет смысл.
_MEASUREMENT_LIMIT = timedelta(seconds=10)


async def _purge(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))


@pytest_asyncio.fixture
async def bulk_queue(pg_engine, fresh_browser_readiness):
    """Пустая очередь залива и подтверждённая свежая готовность канала."""
    await _purge(pg_engine)
    yield pg_engine
    await _purge(pg_engine)


async def _db_now(engine) -> datetime:
    """Часы того же сервера, который считает дедлайн."""
    async with engine.connect() as conn:
        return await conn.scalar(text("SELECT clock_timestamp()"))


async def _stored_deadline(engine, task_id: int) -> datetime:
    async with engine.connect() as conn:
        return await conn.scalar(
            text("SELECT deadline_at FROM task_queue WHERE id = :id"),
            {"id": task_id},
        )


async def _spend_in_queue(engine, task_id: int, waited: timedelta) -> None:
    """Сдвинуть строку в прошлое: столько она пролежала незабранной.

    ``CAST(... AS double precision)`` обязателен по той же причине, что и в
    самом claim: связанное значение приходит без типа, а ``make_interval`` не
    принимает ``text``.
    """
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET created_at = created_at
                        - make_interval(secs => CAST(:sec AS double precision)),
                    available_at = available_at
                        - make_interval(secs => CAST(:sec AS double precision)),
                    deadline_at = deadline_at
                        - make_interval(secs => CAST(:sec AS double precision))
                WHERE id = :id
                """
            ),
            {"id": int(task_id), "sec": waited.total_seconds()},
        )


async def _enqueue_launch(engine, *, run_id: str) -> int:
    """Первичный путь постановки: полосу выбирает infer_task_lane.

    Повторяет вызов из ``apps/api/routers/v1/campaigns_create.py``: ``lane``
    не передаётся, поэтому полоса выводится из семантики задачи.
    """
    payload = {"run_id": run_id, "account_id": "123456789", "currency": "USD"}
    assert infer_task_lane("campaign_create", payload, requested_by="operator") == "bulk"
    task_id = await create_task(
        engine,
        task_type="campaign_create",
        idempotency_key=f"campaign:launch:{run_id}",
        payload=payload,
        requested_by="operator",
        max_attempts=1,
    )
    assert task_id is not None
    return task_id


async def _enqueue_resume(engine, *, run_id: str, previous_task_id: int) -> int:
    """Повторный путь постановки: полоса задана явно.

    Повторяет вызов из ``core/commands/campaign_runs.py``: ``lane='bulk'``
    приходит от команды, а не из вывода по типу задачи.
    """
    task_id = await create_task(
        engine,
        task_type="campaign_create",
        idempotency_key=f"campaign:resume:{run_id}:{previous_task_id}",
        payload={
            "run_id": run_id,
            "resume_of_task_id": previous_task_id,
            "resume_generation": 1,
            "checkpoint": "pre_external",
        },
        requested_by="operator",
        lane="bulk",
        max_attempts=1,
    )
    assert task_id is not None
    return task_id


async def _claim_measured(engine) -> tuple[Task, datetime, datetime]:
    """Забрать задачу, зажав захват между двумя показаниями часов БД."""
    before = await _db_now(engine)
    claim = await claim_campaign_task(engine)
    after = await _db_now(engine)
    assert claim.task is not None, (
        "claim не вернул задачу: либо запрос не исполняется, либо строка "
        "перестала быть кандидатом — воркер не взял бы ни одного залива"
    )
    assert after - before < _MEASUREMENT_LIMIT
    return claim.task, before, after


def _assert_full_window(task: Task, before: datetime, after: datetime) -> None:
    """Дедлайн забранной задачи = момент захвата + полное окно полосы.

    Момент захвата известен с точностью до ``[before, after]``, поэтому окно
    зажимается тем же интервалом. Границы точные, без «примерно»: сдвиг даже
    на минуту означает, что окно считают не от захвата.
    """
    assert task.lane == "bulk"
    assert task.deadline_at is not None
    assert task.deadline_at - after <= _BULK_WINDOW <= task.deadline_at - before


async def test_launch_that_almost_ran_out_of_wait_still_gets_a_full_window(bulk_queue) -> None:
    """Ожидание в очереди не съедает окно на работу.

    Строка входит в захват с минутой остатка от срока ожидания. Выйти она
    обязана с полными тридцатью минутами: иначе залив пойдёт к внешней границе
    с остатком, которого не хватает, и будет срезан посередине.
    """
    run_id = str(uuid.uuid4())
    task_id = await _enqueue_launch(bulk_queue, run_id=run_id)
    await _spend_in_queue(bulk_queue, task_id, _WAITED_IN_QUEUE)

    queued_deadline = await _stored_deadline(bulk_queue, task_id)
    left_before_claim = queued_deadline - await _db_now(bulk_queue)
    assert left_before_claim < timedelta(minutes=2), (
        "сценарий не воспроизведён: задача обязана входить в захват на исходе ожидания"
    )

    task, before, after = await _claim_measured(bulk_queue)

    assert task.id == task_id
    _assert_full_window(task, before, after)
    # Прямая формулировка #203: дедлайн отсчитан от захвата, а не от постановки.
    assert task.deadline_at - queued_deadline >= _WAITED_IN_QUEUE - _MEASUREMENT_LIMIT
    # Воркер берёт дедлайн из того, что вернул захват. Значение в таблице и в
    # снимке задачи обязаны совпадать, иначе окна расходятся.
    assert await _stored_deadline(bulk_queue, task_id) == task.deadline_at
    # Задача обязана успеть закрыть себя под тем же фенсом, которым открыта:
    # аренда воркера залива не продлевается, у неё только запас поверх окна.
    assert task.lease_expires_at is not None
    assert task.lease_expires_at >= task.deadline_at


async def test_repeat_launch_gets_the_same_window_as_the_first(bulk_queue) -> None:
    """Оба пути постановки дают одно окно: и первичный, и повторный залив."""
    run_id = str(uuid.uuid4())
    first_id = await _enqueue_launch(bulk_queue, run_id=run_id)
    await _spend_in_queue(bulk_queue, first_id, _WAITED_IN_QUEUE)
    first_task, first_before, first_after = await _claim_measured(bulk_queue)
    assert first_task.id == first_id
    _assert_full_window(first_task, first_before, first_after)

    resume_id = await _enqueue_resume(bulk_queue, run_id=run_id, previous_task_id=first_id)
    await _spend_in_queue(bulk_queue, resume_id, _WAITED_IN_QUEUE)
    resume_task, resume_before, resume_after = await _claim_measured(bulk_queue)

    assert resume_task.id == resume_id
    _assert_full_window(resume_task, resume_before, resume_after)


async def test_time_spent_in_queue_never_shortens_the_window(bulk_queue) -> None:
    """Ожидавшая задача получает то же окно, что и только что поставленная.

    Это и есть «срез посередине воспроизвести нельзя», записанный значением:
    длина окна не зависит от того, сколько задача пролежала в очереди.
    """
    waited_id = await _enqueue_launch(bulk_queue, run_id=str(uuid.uuid4()))
    await _spend_in_queue(bulk_queue, waited_id, _WAITED_IN_QUEUE)
    fresh_id = await _enqueue_launch(bulk_queue, run_id=str(uuid.uuid4()))

    # Порядок захвата детерминирован: available_at ожидавшей строки старше.
    waited_task, waited_before, waited_after = await _claim_measured(bulk_queue)
    fresh_task, fresh_before, fresh_after = await _claim_measured(bulk_queue)

    assert (waited_task.id, fresh_task.id) == (waited_id, fresh_id)
    _assert_full_window(waited_task, waited_before, waited_after)
    _assert_full_window(fresh_task, fresh_before, fresh_after)

    waited_window = waited_task.deadline_at - waited_after
    fresh_window = fresh_task.deadline_at - fresh_before
    assert abs(waited_window - fresh_window) < _MEASUREMENT_LIMIT
