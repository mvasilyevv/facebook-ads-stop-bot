# -*- coding: utf-8 -*-
"""Unit-тесты singleton file-lock воркеров (защита от двойного запуска)."""

from __future__ import annotations

import asyncio

import pytest

import core.worker_lock as wl


# Первый захват lock проходит, второй (другой fd того же файла) — отклоняется
def test_second_acquire_blocked(tmp_path) -> None:
    name = "t_block"
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    # имитируем второй процесс: убираем in-process запись (fd остаётся открыт и держит lock)
    held = wl._HELD.pop(name)
    try:
        assert wl.try_acquire(name, lock_dir=str(tmp_path)) is False
    finally:
        held.close()
        wl.release(name)


# acquire_singleton_lock завершает процесс (exit 0) при занятом lock
def test_acquire_exits_on_duplicate(tmp_path) -> None:
    name = "t_exit"
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    held = wl._HELD.pop(name)
    try:
        with pytest.raises(SystemExit) as exc:
            wl.acquire_singleton_lock(name, lock_dir=str(tmp_path))
        assert exc.value.code == 0
    finally:
        held.close()
        wl.release(name)


# Повторный захват тем же процессом (lock уже держится) → True (идемпотентно)
def test_reacquire_same_process_idempotent(tmp_path) -> None:
    name = "t_idem"
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    wl.release(name)


# release освобождает lock — после него захват снова возможен
def test_release_frees_lock(tmp_path) -> None:
    name = "t_rel"
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    wl.release(name)
    assert wl.try_acquire(name, lock_dir=str(tmp_path)) is True
    wl.release(name)


class _FakePgConnection:
    def __init__(self, *, acquired: bool = True, lose_on_check: bool = False) -> None:
        self.acquired = acquired
        self.lose_on_check = lose_on_check
        self.closed = False
        self.queries: list[str] = []
        self.listeners = []

    async def fetchval(self, query: str, *_args):
        self.queries.append(query)
        if "pg_try_advisory_lock" in query:
            return self.acquired
        if "pg_catalog.pg_locks" in query:
            if self.lose_on_check:
                raise ConnectionError("ownership connection lost")
            return True
        if "pg_advisory_unlock" in query:
            return True
        raise AssertionError(query)

    def add_termination_listener(self, listener) -> None:
        self.listeners.append(listener)

    def is_closed(self) -> bool:
        return self.closed

    async def close(self, *, timeout: float | None = None) -> None:  # noqa: ASYNC109
        _ = timeout
        self.closed = True
        for listener in self.listeners:
            listener(self)


@pytest.mark.asyncio
async def test_postgres_singleton_publishes_marker_only_while_owned(
    tmp_path,
    monkeypatch,
) -> None:
    connection = _FakePgConnection()

    async def connect(**_kwargs):
        return connection

    monkeypatch.setattr(wl.asyncpg, "connect", connect)
    marker = tmp_path / "ready"

    async def worker() -> str:
        assert marker.read_text(encoding="utf-8").startswith("scheduler:")
        return "finished"

    result = await wl.run_postgres_singleton(
        "scheduler",
        worker,
        database_url="postgresql+asyncpg://user:secret@postgres/db",
        ready_marker=marker,
        check_seconds=0.01,
    )

    assert result == "finished"
    assert not marker.exists()
    assert any("pg_try_advisory_lock" in query for query in connection.queries)
    assert any("pg_advisory_unlock" in query for query in connection.queries)
    assert connection.closed is True


@pytest.mark.asyncio
async def test_postgres_singleton_waits_for_incumbent_before_running(
    tmp_path,
    monkeypatch,
) -> None:
    incumbent = _FakePgConnection(acquired=False)
    target = _FakePgConnection(acquired=True)
    connections = iter((incumbent, target))

    async def connect(**_kwargs):
        return next(connections)

    monkeypatch.setattr(wl.asyncpg, "connect", connect)
    ran = False

    async def worker() -> None:
        nonlocal ran
        ran = True

    await wl.run_postgres_singleton(
        "digest",
        worker,
        database_url="postgresql://user:secret@postgres/db",
        ready_marker=tmp_path / "ready",
        retry_seconds=0,
    )

    assert incumbent.closed is True
    assert ran is True


@pytest.mark.asyncio
async def test_postgres_singleton_cancels_worker_when_fencing_session_is_lost(
    tmp_path,
    monkeypatch,
) -> None:
    connection = _FakePgConnection(lose_on_check=True)

    async def connect(**_kwargs):
        return connection

    monkeypatch.setattr(wl.asyncpg, "connect", connect)
    cancelled = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    marker = tmp_path / "ready"
    with pytest.raises(wl.SingletonOwnershipLostError):
        await wl.run_postgres_singleton(
            "cleanup",
            worker,
            database_url="postgresql://user:secret@postgres/db",
            ready_marker=marker,
            check_seconds=0.001,
        )

    assert cancelled.is_set()
    assert not marker.exists()
