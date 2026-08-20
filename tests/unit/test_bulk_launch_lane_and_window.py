# -*- coding: utf-8 -*-
"""Залив живёт в полосе bulk от постановки до захвата (#203).

Окно на работу выбирается по полосе строки в момент захвата. Значит полоса —
не бухгалтерия, а срок жизни необратимой операции: залив, случайно попавший в
``interactive``, получил бы на работу две минуты вместо тридцати и был бы
срезан посреди создания объектов в кабинете.

Здесь закреплён путь полосы целиком и только исполнением:

1. первичная постановка выводит полосу из типа задачи (``infer_task_lane``);
2. повторная постановка задаёт ту же полосу явно;
3. обе кладут в очередь предельный срок ОЖИДАНИЯ, а не окно на работу;
4. воркер залива забирает задачу из той же полосы и приносит в захват окно
   именно этой полосы.

Само значение дедлайна после захвата проверяется исполнением claim на
PostgreSQL — ``tests/integration/test_claim_execution_window.py``. Проверять
здесь текст SQL бессмысленно: 20.08.2026 такие проверки были зелёными, пока
claim не исполнялся вообще.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import core.tasks.queue as task_queue
from apps.campaign_creator_worker import claim_campaign_task
from core.tasks.queue import create_task, infer_task_lane

_BULK_WINDOW_SECONDS = 30 * 60

# Постановка в очередь и чтение часов происходят в одном тесте; расхождение
# больше пары секунд означало бы, что дедлайн считают не от «сейчас».
_TOLERANCE_SECONDS = 2

# Снимок payload первичного залива: ровно те ключи, что кладёт
# apps/api/routers/v1/campaigns_create.py.
_LAUNCH_PAYLOAD = {
    "run_id": "00000000-0000-0000-0000-000000000001",
    "account_id": "123456789",
    "currency": "USD",
    "currency_exponent": 2,
    "cabinet_timezone": "Africa/Accra",
}


def _recording_engine(*, insert_id: int | None = 77):
    """Движок с журналом операторов; INSERT возвращает id, claim — пусто."""
    calls: list[tuple[str, dict]] = []

    async def execute(statement, params=None):
        sql = str(statement)
        calls.append((sql, dict(params or {})))
        row = (insert_id,) if "INSERT INTO task_queue" in sql else None
        return SimpleNamespace(first=lambda: row, all=lambda: [], rowcount=0)

    connection = SimpleNamespace(execute=execute)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.begin.return_value = context
    return engine, calls


def _only(calls: list[tuple[str, dict]], marker: str) -> dict:
    matched = [params for sql, params in calls if marker in sql]
    assert len(matched) == 1, f"ожидал ровно один оператор с {marker!r}, получил {len(matched)}"
    return matched[0]


def _lane_wait_seconds(lane: str) -> int:
    """Предельный срок ожидания полосы — источник истины один, копий нет."""
    return task_queue._queue_wait_seconds(lane)


# ============ 1. полоса первичного залива ============


def test_primary_launch_path_lands_in_the_bulk_lane() -> None:
    """Тип задачи достаточен: полосу залива не выводят из того, кто её просил."""
    for requested_by in ("operator:web", "owner:telegram", ""):
        assert (
            infer_task_lane("campaign_create", _LAUNCH_PAYLOAD, requested_by=requested_by) == "bulk"
        )


def test_launch_lane_is_not_stolen_by_the_interactive_fallthrough() -> None:
    """Ключ ``action`` в payload не переводит залив в короткое окно.

    Полоса ``interactive`` — общий конец лестницы выбора: в неё попадает всё,
    что не опознано. Залив с двухминутным окном был бы срезан посреди создания
    объектов, поэтому тип задачи обязан решать раньше любых ключей payload.
    """
    payload = dict(_LAUNCH_PAYLOAD, action="pause")

    assert infer_task_lane("campaign_create", payload, requested_by="operator:web") == "bulk"


# ============ 2. что кладут в очередь оба пути постановки ============


async def test_primary_launch_is_enqueued_into_bulk_with_the_wait_limit() -> None:
    """Первичный путь: полоса не передаётся, срок в строке — ожидание в очереди."""
    engine, calls = _recording_engine()

    task_id = await create_task(
        engine,
        task_type="campaign_create",
        idempotency_key="campaign:launch:00000000-0000-0000-0000-000000000001",
        payload=_LAUNCH_PAYLOAD,
        requested_by="operator:web",
        max_attempts=1,
    )

    assert task_id == 77
    inserted = _only(calls, "INSERT INTO task_queue")
    assert inserted["lane"] == "bulk"
    # Строка без дедлайна бессмертна в очереди: её никто не закроет отказом.
    assert inserted["deadline_at"] is not None
    left = (inserted["deadline_at"] - datetime.now(timezone.utc)).total_seconds()
    assert abs(left - _lane_wait_seconds("bulk")) <= _TOLERANCE_SECONDS
    # Сигнал пробуждения называет ту же полосу, из которой задачу и заберут.
    assert json.loads(_only(calls, "pg_notify")["payload"])["lane"] == "bulk"


async def test_repeat_launch_is_enqueued_into_the_same_lane() -> None:
    """Повторный путь: полоса задана явно и совпадает с первичной."""
    engine, calls = _recording_engine(insert_id=78)

    task_id = await create_task(
        engine,
        task_type="campaign_create",
        idempotency_key="campaign:resume:00000000-0000-0000-0000-000000000001:77",
        payload={
            "run_id": "00000000-0000-0000-0000-000000000001",
            "resume_of_task_id": 77,
            "resume_generation": 1,
            "checkpoint": "pre_external",
        },
        requested_by="operator:web",
        lane="bulk",
        max_attempts=1,
    )

    assert task_id == 78
    inserted = _only(calls, "INSERT INTO task_queue")
    assert inserted["lane"] == "bulk"
    assert inserted["deadline_at"] is not None
    left = (inserted["deadline_at"] - datetime.now(timezone.utc)).total_seconds()
    assert abs(left - _lane_wait_seconds("bulk")) <= _TOLERANCE_SECONDS


# ============ 3. из какой полосы и с каким окном забирают ============


async def test_launch_is_claimed_from_the_lane_it_was_enqueued_into() -> None:
    """Воркер залива приносит в захват окно ровно своей полосы.

    Окно выбирает SQL по полосе строки, но набор окон приходит параметрами.
    Здесь проверяется значение параметра, а не текст запроса: разъезд полосы
    постановки и полосы захвата оставил бы залив незабранным до просрочки.
    """
    engine, calls = _recording_engine(insert_id=None)

    claim = await claim_campaign_task(engine)

    assert claim.task is None
    claimed = _only(calls, "UPDATE task_queue AS task")
    assert tuple(claimed["lanes"]) == ("bulk",)
    assert claimed["bulk_deadline_seconds"] == _BULK_WINDOW_SECONDS
    # Аренда обязана пережить окно: иначе задача не закроет саму себя.
    assert claimed["lease_seconds"] + claimed["finalize_headroom_seconds"] > _BULK_WINDOW_SECONDS
