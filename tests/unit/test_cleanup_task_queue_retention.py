# -*- coding: utf-8 -*-
"""Ретенция task_queue: завершённые задачи не копятся в очереди вечно.

Money-инвариант: удаляются ТОЛЬКО терминальные статусы. Незавершённая задача
переживает уборку при любом возрасте — пропажа следа незакрытой команды
опаснее роста таблицы.

Тесты гоняют настоящий SQL по in-memory SQLite: проверяется, какие строки
реально остались в таблице, а не факт вызова.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from apps.cleanup_worker import worker as cleanup_worker
from apps.cleanup_worker.worker import delete_task_queue_completed

_NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)

# Политика с раздельными сроками: succeeded — 30 дней, failed/cancelled — 45.
_POLICY = {"task_queue_completed": "30 days", "task_queue_failed": "45 days"}

# Урезанная копия боевой task_queue: CHECK повторяет
# ck_task_queue_ck_task_queue_status, чтобы тест не мог завести статус,
# которого в production-схеме не существует.
_SCHEMA = """
CREATE TABLE task_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'running', 'succeeded', 'failed', 'retrying', 'cancelled'
    )),
    completed_at TEXT,
    updated_at TEXT NOT NULL
)
"""


def _adapt(value: datetime) -> str:
    """ISO-8601 UTC фиксированной ширины — лексикографический порядок = хронологический."""
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


@pytest.fixture
def _sqlite_datetime_adapter():
    """Явный adapter datetime→TEXT (дефолтный в sqlite3 объявлен устаревшим)."""
    key = (datetime, sqlite3.PrepareProtocol)
    previous = sqlite3.adapters.get(key)
    sqlite3.register_adapter(datetime, _adapt)
    yield
    if previous is None:
        sqlite3.adapters.pop(key, None)
    else:
        sqlite3.adapters[key] = previous


@pytest.fixture
async def engine(_sqlite_datetime_adapter) -> AsyncEngine:
    """Одна in-memory база на все транзакции прогона (StaticPool держит соединение)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.execute(text(_SCHEMA))
    try:
        yield engine
    finally:
        await engine.dispose()


async def _insert(
    engine: AsyncEngine,
    key: str,
    status: str,
    *,
    completed_at: datetime | None,
    updated_at: datetime,
) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO task_queue (idempotency_key, status, completed_at, updated_at)"
                " VALUES (:key, :status, :completed_at, :updated_at)"
            ),
            {
                "key": key,
                "status": status,
                "completed_at": completed_at,
                "updated_at": updated_at,
            },
        )


async def _survivors(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT idempotency_key FROM task_queue"))
        return {row[0] for row in rows}


@pytest.mark.asyncio
async def test_expired_terminal_tasks_are_deleted(engine: AsyncEngine) -> None:
    """Терминальные задачи старше своего срока уходят из очереди."""
    old = _NOW - timedelta(days=60)
    for key, status in (
        ("old-succeeded", "succeeded"),
        ("old-failed", "failed"),
        ("old-cancelled", "cancelled"),
    ):
        await _insert(engine, key, status, completed_at=old, updated_at=old)

    deleted = await delete_task_queue_completed(engine, _POLICY, now=_NOW)

    assert deleted == 3
    assert await _survivors(engine) == set()


@pytest.mark.asyncio
async def test_fresh_terminal_tasks_survive(engine: AsyncEngine) -> None:
    """Свежие завершённые задачи нужны оператору и остаются на месте."""
    fresh = _NOW - timedelta(days=5)
    for key, status in (
        ("fresh-succeeded", "succeeded"),
        ("fresh-failed", "failed"),
        ("fresh-cancelled", "cancelled"),
    ):
        await _insert(engine, key, status, completed_at=fresh, updated_at=fresh)

    # Ровно на границе своего срока: failed/cancelled живут дольше succeeded.
    between = _NOW - timedelta(days=35)
    await _insert(engine, "between-failed", "failed", completed_at=between, updated_at=between)

    deleted = await delete_task_queue_completed(engine, _POLICY, now=_NOW)

    assert deleted == 0
    assert await _survivors(engine) == {
        "fresh-succeeded",
        "fresh-failed",
        "fresh-cancelled",
        "between-failed",
    }


@pytest.mark.asyncio
async def test_unfinished_tasks_are_never_deleted(engine: AsyncEngine) -> None:
    """Money-инвариант: незавершённую задачу не удаляем ни при каком возрасте.

    Даже намертво зависшая задача годовой давности остаётся: по ней
    восстанавливают, была ли выполнена денежная операция.
    """
    ancient = _NOW - timedelta(days=365)
    for key, status in (
        ("stuck-pending", "pending"),
        ("stuck-running", "running"),
        ("stuck-retrying", "retrying"),
    ):
        await _insert(engine, key, status, completed_at=None, updated_at=ancient)
    # Патологический случай: незавершённая задача с проставленным completed_at.
    await _insert(
        engine, "stuck-running-stamped", "running", completed_at=ancient, updated_at=ancient
    )

    deleted = await delete_task_queue_completed(engine, _POLICY, now=_NOW)

    assert deleted == 0
    assert await _survivors(engine) == {
        "stuck-pending",
        "stuck-running",
        "stuck-retrying",
        "stuck-running-stamped",
    }


@pytest.mark.asyncio
async def test_missing_completed_at_falls_back_to_updated_at(engine: AsyncEngine) -> None:
    """Без completed_at граница берётся по updated_at, иначе строка бессмертна."""
    old = _NOW - timedelta(days=60)
    fresh = _NOW - timedelta(days=2)
    await _insert(engine, "old-no-stamp", "succeeded", completed_at=None, updated_at=old)
    await _insert(engine, "fresh-no-stamp", "succeeded", completed_at=None, updated_at=fresh)

    deleted = await delete_task_queue_completed(engine, _POLICY, now=_NOW)

    assert deleted == 1
    assert await _survivors(engine) == {"fresh-no-stamp"}


@pytest.mark.asyncio
async def test_deletion_runs_in_bounded_batches(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Уборка не держит долгую транзакцию: строки уходят батчами с лимитом."""
    monkeypatch.setattr(cleanup_worker, "_TASK_QUEUE_DELETE_BATCH", 2)
    old = _NOW - timedelta(days=60)
    for index in range(5):
        await _insert(engine, f"old-{index}", "succeeded", completed_at=old, updated_at=old)

    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany) -> None:
        if statement.lstrip().upper().startswith("DELETE"):
            statements.append(statement)

    deleted = await delete_task_queue_completed(engine, _POLICY, now=_NOW)

    assert deleted == 5
    assert await _survivors(engine) == set()
    # 5 строк по 2 за батч — одним оператором это не сделать.
    assert len(statements) >= 3
    assert all("LIMIT" in statement.upper() for statement in statements)


@pytest.mark.asyncio
async def test_special_retention_keeps_everything(engine: AsyncEngine) -> None:
    """Специальное значение политики выключает уборку, а не чистит всё подряд."""
    old = _NOW - timedelta(days=900)
    await _insert(engine, "old-succeeded", "succeeded", completed_at=old, updated_at=old)
    await _insert(engine, "old-failed", "failed", completed_at=old, updated_at=old)

    deleted = await delete_task_queue_completed(
        engine,
        {"task_queue_completed": "forever", "task_queue_failed": "forever"},
        now=_NOW,
    )

    assert deleted == 0
    assert await _survivors(engine) == {"old-succeeded", "old-failed"}


@pytest.mark.asyncio
async def test_deleted_count_is_logged(
    engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """Оператор должен видеть в логах, сколько строк унесла уборка."""
    old = _NOW - timedelta(days=60)
    await _insert(engine, "old-succeeded", "succeeded", completed_at=old, updated_at=old)

    with caplog.at_level("INFO", logger=cleanup_worker.__name__):
        await delete_task_queue_completed(engine, _POLICY, now=_NOW)

    messages = [record.getMessage() for record in caplog.records]
    assert any("task_queue" in message and "1" in message for message in messages)
