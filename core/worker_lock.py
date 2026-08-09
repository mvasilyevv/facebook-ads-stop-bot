# -*- coding: utf-8 -*-
"""Local and PostgreSQL singleton ownership for scheduled workers.

Повторный container/manual launch мог поднять ДВА экземпляра одного воркера на
одной очереди. `FOR UPDATE SKIP LOCKED` спасал от двойного claim, но оба процесса
жили: двойной poll и дублирующие TG-алерты (health_watchdog). Эксклюзивный
fcntl-lock на файле в /tmp гарантирует один
экземпляр — второй процесс видит занятый lock и завершается с exit 0.

The file lock prevents accidental duplicate processes inside one container or
single-host development runtime. It is deliberately *not* the distributed ownership guarantee:
blue/green containers use :func:`run_postgres_singleton`, which holds a
session-level PostgreSQL advisory lock and cancels the worker immediately if
that ownership connection is lost.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import IO, TypeVar

import asyncpg

from core.config import get_settings

logger = logging.getLogger(__name__)

# worker_name → открытый fd с удержанным lock. Не давать GC закрыть fd.
_HELD: dict[str, IO[str]] = {}

POSTGRES_SINGLETON_READY_PREFIX = "/tmp/fb-agent-postgres-singleton-"
_LOCK_NAMESPACE = "fb-agent:scheduled-worker:v1:"
_T = TypeVar("_T")


class SingletonOwnershipLostError(RuntimeError):
    """The PostgreSQL session that fenced the singleton has disappeared."""


def _advisory_lock_key(worker_name: str) -> int:
    digest = hashlib.sha256(f"{_LOCK_NAMESPACE}{worker_name}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _asyncpg_dsn(database_url: str) -> str:
    """Convert SQLAlchemy's async driver URL to a libpq-style asyncpg DSN."""
    if database_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+asyncpg://")
    if database_url.startswith("postgresql://"):
        return database_url
    raise ValueError("PostgreSQL singleton requires a postgresql URL")


def _remove_ready_marker(marker: Path) -> None:
    try:
        marker.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Could not remove singleton readiness marker: %s", type(exc).__name__)


def _publish_ready_marker(marker: Path, worker_name: str) -> None:
    temporary = marker.with_name(f".{marker.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(f"{worker_name}:{os.getpid()}\n", encoding="utf-8")
        os.replace(temporary, marker)
    except OSError as exc:
        _remove_ready_marker(temporary)
        raise RuntimeError(
            f"could not publish singleton readiness marker ({type(exc).__name__})"
        ) from exc


async def _connect_and_acquire(
    worker_name: str,
    *,
    database_url: str,
    retry_seconds: float,
) -> tuple[asyncpg.Connection, int]:
    lock_key = _advisory_lock_key(worker_name)
    waiting_logged = False
    while True:
        connection: asyncpg.Connection | None = None
        try:
            connection = await asyncpg.connect(
                dsn=_asyncpg_dsn(database_url),
                timeout=5.0,
                server_settings={"application_name": f"fb-agent-singleton:{worker_name}"},
            )
            acquired = bool(await connection.fetchval("SELECT pg_try_advisory_lock($1)", lock_key))
        except asyncio.CancelledError:
            if connection is not None:
                await connection.close(timeout=1.0)
            raise
        except Exception as exc:  # noqa: BLE001 - retry while PostgreSQL recovers
            if connection is not None:
                try:
                    await connection.close(timeout=1.0)
                except Exception:  # noqa: BLE001
                    pass
            logger.warning(
                "PostgreSQL singleton acquisition failed worker=%s error_type=%s",
                worker_name,
                type(exc).__name__,
            )
            await asyncio.sleep(retry_seconds)
            continue
        if acquired:
            logger.info("PostgreSQL singleton ownership acquired worker=%s", worker_name)
            return connection, lock_key
        await connection.close(timeout=1.0)
        if not waiting_logged:
            logger.info(
                "PostgreSQL singleton owned by incumbent; waiting worker=%s",
                worker_name,
            )
            waiting_logged = True
        await asyncio.sleep(retry_seconds)


async def _ownership_monitor(
    connection: asyncpg.Connection,
    *,
    connection_lost: asyncio.Event,
    check_seconds: float,
) -> None:
    while True:
        try:
            await asyncio.wait_for(connection_lost.wait(), timeout=check_seconds)
        except asyncio.TimeoutError:
            try:
                owns_lock = bool(
                    await connection.fetchval(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_catalog.pg_locks
                            WHERE pid = pg_backend_pid()
                              AND locktype = 'advisory'
                              AND granted
                        )
                        """
                    )
                )
            except Exception as exc:  # noqa: BLE001
                raise SingletonOwnershipLostError(
                    f"PostgreSQL singleton connection lost ({type(exc).__name__})"
                ) from exc
            if not owns_lock:
                raise SingletonOwnershipLostError(
                    "PostgreSQL session no longer owns its singleton advisory lock"
                ) from None
            continue
        raise SingletonOwnershipLostError("PostgreSQL singleton connection terminated")


async def run_postgres_singleton(
    worker_name: str,
    worker_factory: Callable[[], Awaitable[_T]],
    *,
    database_url: str | None = None,
    ready_marker: Path | None = None,
    retry_seconds: float = 1.0,
    check_seconds: float = 1.0,
) -> _T:
    """Run a scheduled worker only while this session owns its PG advisory lock.

    A blue/green target waits without executing the worker until the incumbent
    session exits.  The marker is written only after authoritative ownership
    is obtained, so cutover validation is based on PostgreSQL ownership.
    """
    marker = ready_marker or Path(f"{POSTGRES_SINGLETON_READY_PREFIX}{worker_name}.ready")
    _remove_ready_marker(marker)
    connection, lock_key = await _connect_and_acquire(
        worker_name,
        database_url=database_url or get_settings().database_url,
        retry_seconds=retry_seconds,
    )
    connection_lost = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _terminated(_connection: asyncpg.Connection) -> None:
        loop.call_soon_threadsafe(connection_lost.set)

    connection.add_termination_listener(_terminated)
    _publish_ready_marker(marker, worker_name)
    worker_task = asyncio.create_task(worker_factory(), name=f"{worker_name}:worker")
    monitor_task = asyncio.create_task(
        _ownership_monitor(
            connection,
            connection_lost=connection_lost,
            check_seconds=check_seconds,
        ),
        name=f"{worker_name}:ownership",
    )
    try:
        done, _ = await asyncio.wait(
            {worker_task, monitor_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if monitor_task in done:
            ownership_error = monitor_task.exception()
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            if ownership_error is not None:
                raise ownership_error
            raise SingletonOwnershipLostError("singleton ownership monitor stopped")
        monitor_task.cancel()
        await asyncio.gather(monitor_task, return_exceptions=True)
        return await worker_task
    finally:
        _remove_ready_marker(marker)
        if not worker_task.done():
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
        if not monitor_task.done():
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
        try:
            if not connection.is_closed():
                await connection.fetchval("SELECT pg_advisory_unlock($1)", lock_key)
                await connection.close(timeout=1.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "PostgreSQL singleton cleanup failed worker=%s error_type=%s",
                worker_name,
                type(exc).__name__,
            )


def try_acquire(worker_name: str, *, lock_dir: str = "/tmp") -> bool:
    """Пытается взять эксклюзивный lock воркера. True — взят, False — уже занят.

    Не завершает процесс (для тестов и гибкого использования). Идемпотентен по
    worker_name в рамках процесса: если lock уже держится этим процессом —
    возвращает True (повторный захват того же fd не делается).
    """
    if worker_name in _HELD:
        return True
    lock_path = Path(lock_dir) / f"fb_agent_{worker_name}.lock"
    fd = open(lock_path, "a+")  # noqa: SIM115 — fd живёт всё время процесса намеренно
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fd.close()
        return False
    try:
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
    except OSError:
        pass
    _HELD[worker_name] = fd
    return True


def acquire_singleton_lock(worker_name: str, *, lock_dir: str = "/tmp") -> None:
    """Берёт lock воркера; при занятом — завершает процесс (exit 0).

    Вызывать первой строкой в entrypoint воркера (run_*.py) до старта main_loop.
    exit 0 — нормальное завершение: дубликат уже работает.
    """
    if not try_acquire(worker_name, lock_dir=lock_dir):
        logger.warning("Воркер %s уже запущен (singleton-lock занят) — завершаю дубль", worker_name)
        sys.exit(0)


def release(worker_name: str) -> None:
    """Освобождает lock (закрывает fd). В основном для тестов — в проде lock живёт
    до смерти процесса и снимается ядром автоматически."""
    fd = _HELD.pop(worker_name, None)
    if fd is not None:
        try:
            fd.close()
        except OSError:
            pass


__all__ = [
    "POSTGRES_SINGLETON_READY_PREFIX",
    "SingletonOwnershipLostError",
    "acquire_singleton_lock",
    "release",
    "run_postgres_singleton",
    "try_acquire",
]
