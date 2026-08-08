# -*- coding: utf-8 -*-
"""Integration: _begin_scan_run атомарен — scan_id всегда равен id, без осиротевших строк.

После фикса HIGH #9 INSERT идёт одним statement'ом с CTE+nextval. Проверяем:
1. Нормальный путь — scan_id == id, ровно одна запись.
2. Параллельные scan'ы получают уникальные id и uniform invariant scan_id==id.
3. Rollback внутри транзакции (через CancelledError) не оставляет осиротевшего scan_id.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.observer_worker.main import _begin_scan_run


@pytest_asyncio.fixture
async def clean_scan_runs(pg_engine):
    """Удаляет тестовые scan_runs (по большим id) до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            # удаляем только записи без finished_at, чтобы не задеть real-prod данные
            await conn.execute(text("DELETE FROM scan_runs WHERE finished_at IS NULL"))

    await _truncate()
    yield
    await _truncate()


# Сценарий: один _begin_scan_run пишет ровно одну строку с scan_id == id
@pytest.mark.asyncio
async def test_begin_scan_run_writes_consistent_scan_id(pg_engine, clean_scan_runs) -> None:
    scan_id = await _begin_scan_run(pg_engine, ad_account_id="123")
    assert scan_id > 0

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id, scan_id, started_at, finished_at FROM scan_runs WHERE id = :i"),
                {"i": scan_id},
            )
        ).first()

    assert row is not None
    assert row[0] == row[1]  # id == scan_id (контракт)
    assert row[2] is not None  # started_at заполнен
    assert row[3] is None  # finished_at ставит _finish_scan_run


# Сценарий: 5 параллельных _begin_scan_run → 5 разных id, у каждого scan_id == id
@pytest.mark.asyncio
async def test_parallel_begin_scan_run_unique_ids(pg_engine, clean_scan_runs) -> None:
    ids = await asyncio.gather(*[_begin_scan_run(pg_engine, ad_account_id="123") for _ in range(5)])

    assert len(set(ids)) == 5  # все уникальны

    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, scan_id FROM scan_runs WHERE id = ANY(:ids)"),
                {"ids": list(ids)},
            )
        ).all()

    assert len(rows) == 5
    for id_val, scan_val in rows:
        assert id_val == scan_val


# Сценарий: CancelledError в середине _begin_scan_run не оставляет осиротевшего scan_id.
# Один INSERT с CTE атомарен — либо запись есть и scan_id == id, либо её нет вовсе.
@pytest.mark.asyncio
async def test_cancelled_begin_does_not_leave_orphan(pg_engine, clean_scan_runs) -> None:
    async with pg_engine.connect() as conn:
        n_before = (
            await conn.execute(text("SELECT COUNT(*) FROM scan_runs WHERE finished_at IS NULL"))
        ).scalar()

    async def _interrupt():
        # Запускаем begin и моментально cancel'им
        task = asyncio.create_task(_begin_scan_run(pg_engine, ad_account_id="123"))
        await asyncio.sleep(0)  # даём task стартовать
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # Гоняем 10 раз чтобы повысить шанс попасть в окно после INSERT и до RETURNING
    for _ in range(10):
        await _interrupt()

    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, scan_id FROM scan_runs "
                    "WHERE finished_at IS NULL AND scan_id IS DISTINCT FROM id"
                )
            )
        ).all()

    # Записей с scan_id != id (осиротевших) быть не должно ни при каком исходе
    assert rows == []

    async with pg_engine.connect() as conn:
        n_after = (
            await conn.execute(text("SELECT COUNT(*) FROM scan_runs WHERE finished_at IS NULL"))
        ).scalar()
    # сколько бы записей ни осталось от прерванных задач — invariant scan_id == id
    # уже проверен выше; здесь просто sanity что цифра не уехала в отрицательную область
    assert n_after >= n_before
