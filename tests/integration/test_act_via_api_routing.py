# -*- coding: utf-8 -*-
"""Integration: роутинг авто-стопа по observer_config.act_via_api (#39).

maybe_create_disable_task должна создавать meta_api_mutation pause_ad при act_via_api=True
и обычный disable при False (регресс-защита: переключатель не должен ломать DOM-путь).
Требует реальный Postgres (pg_engine).
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.queries import load_observer_config
from core.observer.state_machine import FsmTransition
from core.observer.writers import maybe_create_disable_task


@pytest_asyncio.fixture
async def unique_fb_ad_id(pg_engine: AsyncEngine):
    """Уникальный числовой fb_ad_id + teardown очистки его task_queue записей."""
    fbid = f"23005{uuid.uuid4().int % 10_000_000_000}"

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key LIKE :pat"),
                {"pat": f"%{fbid}%"},
            )

    await _cleanup()
    yield fbid
    await _cleanup()


def _stop_transition() -> FsmTransition:
    """FSM-переход в STOP с флагом создания disable-задачи."""
    return FsmTransition(
        new_state="stop_sent",
        new_stage="stop",
        new_open_token=uuid.uuid4(),
        emit_alert=True,
        alert_stage="stop",
        alert_rule_codes=("cpa_stop",),
        create_disable_task=True,
        transition_reason="test stop",
    )


async def _fetch_task(pg_engine: AsyncEngine, fbid: str) -> dict:
    """Достаёт единственную task_queue запись по fb_ad_id (через idempotency_key)."""
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT task_type, payload, requested_by, status, idempotency_key
                    FROM task_queue
                    WHERE idempotency_key LIKE :pat
                    """
                ),
                {"pat": f"%{fbid}%"},
            )
        ).first()
    assert row is not None, "task_queue запись не создана"
    payload = row[1]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {
        "task_type": row[0],
        "payload": payload,
        "requested_by": row[2],
        "status": row[3],
        "idempotency_key": row[4],
    }


# act_via_api=True → создаётся meta_api_mutation pause_ad (НЕ disable)
@pytest.mark.asyncio
async def test_act_via_api_true_creates_pause_mutation(pg_engine, unique_fb_ad_id) -> None:
    fbid = unique_fb_ad_id
    transition = _stop_transition()

    task_id = await maybe_create_disable_task(
        pg_engine,
        transition=transition,
        fb_ad_id=fbid,
        open_token=transition.new_open_token,
        act_via_api=True,
    )
    assert task_id is not None

    task = await _fetch_task(pg_engine, fbid)
    assert task["task_type"] == "meta_api_mutation"
    assert task["payload"]["mutation_kind"] == "pause_ad"
    assert task["payload"]["target_id"] == fbid
    assert task["requested_by"] == "bot_auto_stop"
    assert task["status"] == "pending"
    assert task["idempotency_key"].startswith("auto:pause_ad:")


# act_via_api=False → создаётся обычный disable (регресс-защита DOM-пути)
@pytest.mark.asyncio
async def test_act_via_api_false_creates_disable(pg_engine, unique_fb_ad_id) -> None:
    fbid = unique_fb_ad_id
    transition = _stop_transition()

    task_id = await maybe_create_disable_task(
        pg_engine,
        transition=transition,
        fb_ad_id=fbid,
        open_token=transition.new_open_token,
        act_via_api=False,
    )
    assert task_id is not None

    task = await _fetch_task(pg_engine, fbid)
    assert task["task_type"] == "disable"
    assert task["payload"]["fb_ad_id"] == fbid
    assert task["idempotency_key"].startswith("auto:disable:")


# Код-дефолт ПАРАМЕТРА функции (без act_via_api) = DOM (безопасная сигнатура для
# прямых вызовов/тестов). Продуктовый дефолт — в observer_config.act_via_api (TRUE).
@pytest.mark.asyncio
async def test_default_is_dom_disable(pg_engine, unique_fb_ad_id) -> None:
    fbid = unique_fb_ad_id
    transition = _stop_transition()

    await maybe_create_disable_task(
        pg_engine,
        transition=transition,
        fb_ad_id=fbid,
        open_token=transition.new_open_token,
    )
    task = await _fetch_task(pg_engine, fbid)
    assert task["task_type"] == "disable"


# Идемпотентность: повторный STOP того же incident'а (тот же token) → дубль не создаётся
@pytest.mark.asyncio
async def test_act_via_api_idempotent_per_incident(pg_engine, unique_fb_ad_id) -> None:
    fbid = unique_fb_ad_id
    token = uuid.uuid4()
    transition = FsmTransition(
        new_state="stop_sent",
        new_stage="stop",
        new_open_token=token,
        create_disable_task=True,
        transition_reason="test",
    )

    first = await maybe_create_disable_task(
        pg_engine, transition=transition, fb_ad_id=fbid, open_token=token, act_via_api=True
    )
    second = await maybe_create_disable_task(
        pg_engine, transition=transition, fb_ad_id=fbid, open_token=token, act_via_api=True
    )
    assert first is not None
    assert second is None  # тот же idempotency_key → no-op


# create_disable_task=False → ничего не создаётся независимо от флага
@pytest.mark.asyncio
async def test_no_task_when_flag_off(pg_engine, unique_fb_ad_id) -> None:
    fbid = unique_fb_ad_id
    transition = FsmTransition(
        new_state="warning_sent",
        new_stage="warning",
        new_open_token=uuid.uuid4(),
        create_disable_task=False,
        transition_reason="warning, no disable",
    )
    task_id = await maybe_create_disable_task(
        pg_engine, transition=transition, fb_ad_id=fbid, open_token=None, act_via_api=True
    )
    assert task_id is None


# load_observer_config возвращает act_via_api как bool (контракт чтения флага observer'ом)
@pytest.mark.asyncio
async def test_load_observer_config_exposes_act_via_api(pg_engine) -> None:
    # Гарантируем singleton (тестовая БД может быть без него).
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO observer_config (singleton_key, act_via_api)
                VALUES ('default', true)
                ON CONFLICT (singleton_key) DO UPDATE SET act_via_api = EXCLUDED.act_via_api
                """
            )
        )
    cfg = await load_observer_config(pg_engine)
    assert cfg is not None
    assert "act_via_api" in cfg
    assert cfg["act_via_api"] is True
