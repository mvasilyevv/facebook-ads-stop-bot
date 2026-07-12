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
from core.tasks.queue import (
    fail_stuck_campaign_create,
    fail_stuck_irreversible,
    reconcile_stuck_running,
)


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


# ====================== campaign_create (CRIT-1 + HIGH-3) ======================


async def _make_stuck_campaign_create(pg_engine) -> int:
    """Создаёт campaign_create задачу и эмулирует зависание в 'running' 2 часа."""
    task_id = await create_task(
        pg_engine,
        task_type="campaign_create",
        idempotency_key=f"cc-{uuid.uuid4().hex[:10]}",
        payload={"run_id": str(uuid.uuid4())},
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


# Зависший campaign_create → fail_stuck_campaign_create помечает failed (НЕ retry)
@pytest.mark.asyncio
async def test_stuck_campaign_create_marked_failed(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_campaign_create(pg_engine)

    n = await fail_stuck_campaign_create(pg_engine)

    assert n == 1
    assert await _status(pg_engine, task_id) == "failed"


# CRIT-1: reconcile_stuck_running НЕ уводит campaign_create в retrying (даже без exclude_kinds)
@pytest.mark.asyncio
async def test_reconcile_does_not_retry_campaign_create(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_campaign_create(pg_engine)

    # Без exclude_kinds (meta) — campaign_create исключён безусловным task_type guard'ом.
    moved = await reconcile_stuck_running(pg_engine, exclude_kinds=None)

    assert moved == 0
    # campaign_create НЕ должна уйти в retrying — иначе риск дубля кампании
    assert await _status(pg_engine, task_id) == "running"


# Свежий fresh-run campaign_create (НЕ зависший) reconcile/fail не трогают
@pytest.mark.asyncio
async def test_fresh_campaign_create_untouched(pg_engine, clean_task_queue) -> None:
    task_id = await create_task(
        pg_engine,
        task_type="campaign_create",
        idempotency_key=f"cc-fresh-{uuid.uuid4().hex[:8]}",
        payload={"run_id": str(uuid.uuid4())},
        requested_by="test",
    )
    # status='pending', не running → ни одна из reconcile-функций не трогает.
    failed = await fail_stuck_campaign_create(pg_engine)
    moved = await reconcile_stuck_running(pg_engine, exclude_kinds=None)

    assert failed == 0
    assert moved == 0
    assert await _status(pg_engine, task_id) == "pending"


# ====================== plan_run (аудит 2026-07-12, M-3) ======================


async def _make_stuck_plan_run(pg_engine) -> int:
    """Создаёт plan_run задачу и эмулирует зависание в 'running' 2 часа (SIGKILL-зомби)."""
    task_id = await create_task(
        pg_engine,
        task_type="plan_run",
        idempotency_key=f"pr-{uuid.uuid4().hex[:10]}",
        payload={"plan_id": str(uuid.uuid4())},
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


# Зависший plan_run → fail_stuck_plan_run помечает failed (НЕ retry) — раньше
# такой зомби висел в 'running' вечно и невидимо (частичный залив без алерта).
@pytest.mark.asyncio
async def test_stuck_plan_run_marked_failed(pg_engine, clean_task_queue) -> None:
    from core.tasks.queue import fail_stuck_plan_run

    task_id = await _make_stuck_plan_run(pg_engine)

    n = await fail_stuck_plan_run(pg_engine)

    assert n == 1
    assert await _status(pg_engine, task_id) == "failed"


# reconcile_stuck_running по-прежнему НЕ трогает plan_run (двойная защита от дубля).
@pytest.mark.asyncio
async def test_reconcile_does_not_retry_plan_run(pg_engine, clean_task_queue) -> None:
    task_id = await _make_stuck_plan_run(pg_engine)

    moved = await reconcile_stuck_running(pg_engine, exclude_kinds=None)

    assert moved == 0
    assert await _status(pg_engine, task_id) == "running"


# Свежий plan_run (pending, не зависший) fail_stuck_plan_run не трогает.
@pytest.mark.asyncio
async def test_fresh_plan_run_untouched(pg_engine, clean_task_queue) -> None:
    from core.tasks.queue import fail_stuck_plan_run

    task_id = await create_task(
        pg_engine,
        task_type="plan_run",
        idempotency_key=f"pr-fresh-{uuid.uuid4().hex[:8]}",
        payload={"plan_id": str(uuid.uuid4())},
        requested_by="test",
    )
    failed = await fail_stuck_plan_run(pg_engine)

    assert failed == 0
    assert await _status(pg_engine, task_id) == "pending"
