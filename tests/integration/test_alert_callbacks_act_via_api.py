# -*- coding: utf-8 -*-
"""Integration: ручные TG-кнопки dis/ereco роутятся по observer_config.act_via_api (#39).

При полном scope флаг = «все toggle через Marketing API». Проверяем, что кнопка
«Отключить» под алертом и кнопка enable-рекомендации создают meta_api_mutation при
act_via_api=True и обычные disable/enable при False. Требует реальный Postgres.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.handlers.alerts import handle_dis_callback, handle_enable_reco_callback


@pytest_asyncio.fixture
async def act_via_api_flag(pg_engine: AsyncEngine):
    """Управляет observer_config.act_via_api. Возвращает setter, восстанавливает исходное в teardown."""

    async def _read() -> bool:
        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT act_via_api FROM observer_config WHERE singleton_key = 'default'")
                )
            ).first()
        return bool(row[0]) if row else False

    original = await _read()

    async def _set(value: bool) -> None:
        # UPSERT: тестовая БД может не иметь singleton-строки observer_config.
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO observer_config (singleton_key, act_via_api)
                    VALUES ('default', :v)
                    ON CONFLICT (singleton_key) DO UPDATE
                    SET act_via_api = EXCLUDED.act_via_api, updated_at = NOW()
                    """
                ),
                {"v": value},
            )

    yield _set
    await _set(original)


@pytest_asyncio.fixture
async def fbid_cleanup(pg_engine: AsyncEngine):
    """Уникальный fb_ad_id + очистка его task_queue записей."""
    fbid = f"23009{uuid.uuid4().int % 10_000_000_000}"

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key LIKE :p"), {"p": f"%{fbid}%"}
            )

    await _cleanup()
    yield fbid
    await _cleanup()


async def _fetch(pg_engine: AsyncEngine, fbid: str) -> dict:
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT task_type, payload, status FROM task_queue "
                    "WHERE idempotency_key LIKE :p"
                ),
                {"p": f"%{fbid}%"},
            )
        ).first()
    assert row is not None, "task_queue запись не создана"
    payload = row[1]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {"task_type": row[0], "payload": payload, "status": row[2]}


# dis-кнопка при act_via_api=True → meta_api_mutation pause_ad
@pytest.mark.asyncio
async def test_dis_callback_api_creates_pause_ad(pg_engine, act_via_api_flag, fbid_cleanup) -> None:
    await act_via_api_flag(True)
    client = AsyncMock()

    await handle_dis_callback(
        engine=pg_engine,
        client=client,
        cq_id="cq1",
        fb_ad_id=fbid_cleanup,
        token=str(uuid.uuid4()),
        username="tester",
    )

    task = await _fetch(pg_engine, fbid_cleanup)
    assert task["task_type"] == "meta_api_mutation"
    assert task["payload"]["mutation_kind"] == "pause_ad"
    assert task["payload"]["target_id"] == fbid_cleanup
    assert task["status"] == "pending"
    client.answer_callback_query.assert_awaited()


# dis-кнопка при act_via_api=False → обычный disable (DOM)
@pytest.mark.asyncio
async def test_dis_callback_dom_creates_disable(pg_engine, act_via_api_flag, fbid_cleanup) -> None:
    await act_via_api_flag(False)
    client = AsyncMock()

    await handle_dis_callback(
        engine=pg_engine,
        client=client,
        cq_id="cq2",
        fb_ad_id=fbid_cleanup,
        token=str(uuid.uuid4()),
        username="tester",
    )

    task = await _fetch(pg_engine, fbid_cleanup)
    assert task["task_type"] == "disable"
    assert task["payload"]["fb_ad_id"] == fbid_cleanup


# ereco-кнопка при act_via_api=True → meta_api_mutation activate_ad
@pytest.mark.asyncio
async def test_ereco_callback_api_creates_activate_ad(
    pg_engine, act_via_api_flag, fbid_cleanup
) -> None:
    await act_via_api_flag(True)
    client = AsyncMock()

    await handle_enable_reco_callback(
        engine=pg_engine,
        client=client,
        cq_id="cq3",
        fb_ad_id=fbid_cleanup,
        username="tester",
    )

    task = await _fetch(pg_engine, fbid_cleanup)
    assert task["task_type"] == "meta_api_mutation"
    assert task["payload"]["mutation_kind"] == "activate_ad"
    assert task["payload"]["target_id"] == fbid_cleanup


# ereco-кнопка при act_via_api=False → обычный enable (DOM)
@pytest.mark.asyncio
async def test_ereco_callback_dom_creates_enable(pg_engine, act_via_api_flag, fbid_cleanup) -> None:
    await act_via_api_flag(False)
    client = AsyncMock()

    await handle_enable_reco_callback(
        engine=pg_engine,
        client=client,
        cq_id="cq4",
        fb_ad_id=fbid_cleanup,
        username="tester",
    )

    task = await _fetch(pg_engine, fbid_cleanup)
    assert task["task_type"] == "enable"
    assert task["payload"]["fb_ad_id"] == fbid_cleanup
