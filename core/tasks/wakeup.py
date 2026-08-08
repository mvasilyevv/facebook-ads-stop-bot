"""PostgreSQL queue wakeup accelerator with mandatory polling reconciliation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import asyncpg

TASK_QUEUE_NOTIFY_CHANNEL = "fb_task_queue"
DEFAULT_RECONCILE_SECONDS = 1.0

logger = logging.getLogger(__name__)


def asyncpg_dsn(database_url: str) -> str:
    """Convert the SQLAlchemy asyncpg URL into a native asyncpg DSN."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


class TaskQueueWakeup:
    """Reconnectable LISTEN client that only accelerates DB-authoritative claims.

    Notifications are coalesced in a one-item queue. Missing, malformed or lost
    notifications are harmless because every waiter times out into a normal
    PostgreSQL claim after ``reconcile_seconds``.
    """

    def __init__(
        self,
        database_url: str,
        *,
        task_type: str,
        lanes: Sequence[str],
        reconcile_seconds: float = DEFAULT_RECONCILE_SECONDS,
        connect: Callable[..., Awaitable[Any]] = asyncpg.connect,
    ) -> None:
        if not task_type:
            raise ValueError("task_type must not be empty")
        normalized_lanes = tuple(str(lane).strip() for lane in lanes if str(lane).strip())
        if not normalized_lanes:
            raise ValueError("lanes must not be empty")
        if reconcile_seconds <= 0:
            raise ValueError("reconcile_seconds must be positive")
        self._dsn = asyncpg_dsn(database_url)
        self._task_type = task_type
        self._lanes = frozenset(normalized_lanes)
        self._reconcile_seconds = float(reconcile_seconds)
        self._connect = connect
        self._signals: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self.ready = asyncio.Event()

    def _wake(self) -> None:
        try:
            self._signals.put_nowait(None)
        except asyncio.QueueFull:
            pass

    def _notification(
        self,
        _connection: Any,
        _pid: int,
        _channel: str,
        raw_payload: str,
    ) -> None:
        try:
            payload = json.loads(raw_payload)
        except (TypeError, json.JSONDecodeError):
            # Unknown payloads still trigger a DB claim; NOTIFY is never trusted
            # as a task record.
            self._wake()
            return
        if not isinstance(payload, dict):
            self._wake()
            return
        task_type = str(payload.get("task_type") or "")
        lane = str(payload.get("lane") or "")
        if (not task_type or task_type == self._task_type) and (not lane or lane in self._lanes):
            self._wake()

    async def wait_for_work(self, stop: asyncio.Event) -> bool:
        """Wait for a relevant notification or the reconciliation timeout.

        Returns ``False`` only when shutdown was requested. ``True`` always
        means "query PostgreSQL now", never "a task definitely exists".
        """
        if stop.is_set():
            return False
        signal_task = asyncio.create_task(self._signals.get())
        stop_task = asyncio.create_task(stop.wait())
        try:
            done, pending = await asyncio.wait(
                {signal_task, stop_task},
                timeout=self._reconcile_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            return not (stop_task in done and stop.is_set())
        finally:
            for task in (signal_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(signal_task, stop_task, return_exceptions=True)

    async def run(self, stop: asyncio.Event) -> None:
        """Maintain LISTEN registration until shutdown, reconnecting on loss."""
        reconnect_delay = 0.25
        loop = asyncio.get_running_loop()
        while not stop.is_set():
            connection: Any | None = None
            terminated = asyncio.Event()

            def _terminated(
                _connection: Any,
                termination_event: asyncio.Event = terminated,
            ) -> None:
                loop.call_soon_threadsafe(termination_event.set)
                loop.call_soon_threadsafe(self._wake)

            try:
                connection = await self._connect(dsn=self._dsn, timeout=5)
                await connection.add_listener(
                    TASK_QUEUE_NOTIFY_CHANNEL,
                    self._notification,
                )
                if hasattr(connection, "add_termination_listener"):
                    connection.add_termination_listener(_terminated)
                self.ready.set()
                reconnect_delay = 0.25

                stop_task = asyncio.create_task(stop.wait())
                terminated_task = asyncio.create_task(terminated.wait())
                done, pending = await asyncio.wait(
                    {stop_task, terminated_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                if stop_task in done and stop.is_set():
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - polling remains available
                logger.warning(
                    "task queue LISTEN unavailable; polling reconciliation remains active: %s",
                    type(exc).__name__,
                )
            finally:
                if connection is not None:
                    try:
                        await connection.remove_listener(
                            TASK_QUEUE_NOTIFY_CHANNEL,
                            self._notification,
                        )
                    except Exception:  # noqa: BLE001
                        pass
                    if hasattr(connection, "remove_termination_listener"):
                        try:
                            connection.remove_termination_listener(_terminated)
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        await connection.close()
                    except Exception:  # noqa: BLE001
                        pass

            if stop.is_set():
                return
            try:
                await asyncio.wait_for(stop.wait(), timeout=reconnect_delay)
            except TimeoutError:
                reconnect_delay = min(reconnect_delay * 2, 5.0)
