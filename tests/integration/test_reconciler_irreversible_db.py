# -*- coding: utf-8 -*-
"""Integration (Postgres): H-1 — крэш-путь reconciler money-safe для необратимых.

Сценарий бага: worker создал кампанию в Meta (create_campaign/duplicate_campaign),
но умер (SIGKILL/OOM/деплой) ДО mark_succeeded → задача застряла в 'running'.
Слепой reconcile_stuck_running перевёл бы её в 'retrying' → повторное исполнение =
ДУБЛЬ кампании + двойной открут бюджета.

Фикс: fail_stuck_irreversible уводит такие задачи в 'failed' (ручная проверка), а
reconcile_stuck_running(exclude_kinds=...) их НЕ ретраит. Обратимые (pause_ad) —
обычный requeue в 'retrying'.

Требует Postgres из docker-compose (pg_engine fixture; skip если БД недоступна).
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.meta_api.schemas import IRREVERSIBLE_MUTATION_KINDS
from core.tasks import create_task
from core.tasks.queue import fail_stuck_irreversible, reconcile_stuck_running


@pytest_asyncio.fixture
async def clean_task_queue(pg_engine):
    """Чистка task_queue до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue"))

    await _truncate()
    yield
    await _truncate()


async def _make_stuck_running(pg_engine, *, mutation_kind: str) -> int:
    """Создаёт meta_api_mutation задачу и эмулирует зависание в 'running' 2 часа."""
    task_id = await create_task(
        pg_engine,
        task_type="meta_api_mutation",
        idempotency_key=f"irrev-{uuid.uuid4().hex[:10]}",
        payload={"mutation_kind": mutation_kind, "target_id": "act_test", "params": {}},
        requested_by="test",
    )
    assert task_id is not None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'running', updated_at = NOW() - INTERVAL '2 hours'
                WHERE id = :i
                """
            ),
            {"i": task_id},
        )
    return task_id


async def _status(pg_engine, task_id: int) -> str:
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(text("SELECT status FROM task_queue WHERE id = :i"), {"i": task_id})
        ).first()
    return str(row[0]) if row else ""


# Зависший create_campaign → fail_stuck_irreversible помечает failed (НЕ retry)
@pytest.mark.asyncio
async def test_stuck_create_campaign_marked_failed(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_running(pg_engine, mutation_kind="create_campaign")

    n = await fail_stuck_irreversible(pg_engine, mutation_kinds=IRREVERSIBLE_MUTATION_KINDS)

    assert n == 1
    assert await _status(pg_engine, task_id) == "failed"


# Зависший duplicate_campaign → тоже failed
@pytest.mark.asyncio
async def test_stuck_duplicate_campaign_marked_failed(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_running(pg_engine, mutation_kind="duplicate_campaign")

    await fail_stuck_irreversible(pg_engine, mutation_kinds=IRREVERSIBLE_MUTATION_KINDS)

    assert await _status(pg_engine, task_id) == "failed"


# reconcile с exclude_kinds НЕ ретраит необратимую — остаётся running (двойная защита)
@pytest.mark.asyncio
async def test_reconcile_excludes_irreversible(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_running(pg_engine, mutation_kind="create_campaign")

    moved = await reconcile_stuck_running(pg_engine, exclude_kinds=IRREVERSIBLE_MUTATION_KINDS)

    assert moved == 0
    # create_campaign НЕ должна уйти в retrying — иначе риск дубля кампании
    assert await _status(pg_engine, task_id) == "running"


# Контраст: зависший pause_ad (обратимая) → fail игнорирует, reconcile → retrying
@pytest.mark.asyncio
async def test_stuck_pause_ad_requeued_not_failed(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_running(pg_engine, mutation_kind="pause_ad")

    failed = await fail_stuck_irreversible(pg_engine, mutation_kinds=IRREVERSIBLE_MUTATION_KINDS)
    assert failed == 0  # обратимую не трогаем
    assert await _status(pg_engine, task_id) == "running"

    moved = await reconcile_stuck_running(pg_engine, exclude_kinds=IRREVERSIBLE_MUTATION_KINDS)
    assert moved == 1
    assert await _status(pg_engine, task_id) == "retrying"
