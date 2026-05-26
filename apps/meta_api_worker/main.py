# -*- coding: utf-8 -*-
"""Worker для исполнения MetaApiMutationTask (outbox pattern).

Workflow (полная реализация — Этап 5):
1. Claim PENDING task (SELECT FOR UPDATE SKIP LOCKED)
2. По mutation_kind вызвать соответствующий метод MetaApiHighLevelClient
3. mark_succeeded / mark_failed
4. Reconcile expired drafts каждые 60 секунд

Сейчас — только скелет с loop, heartbeat и reconcile.
Execution mutations реализуется на Этапе 5.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import signal
import sys
from datetime import UTC, datetime

from core.db import get_session_factory
from core.logging import setup_logging
from core.meta_api.queue import claim_pending_task, mark_failed
from core.meta_api.reconciler import reconcile_all
from core.observer.runtime_status import update_worker_heartbeat
from core.sentry import setup_sentry
from core.worker_utils import PidFileLock, wait_for_shutdown_or_timeout

setup_logging("meta_api_worker")
logger = logging.getLogger(__name__)

# Интервал поллинга очереди в секундах
_POLL_INTERVAL_SECONDS = 3
# Интервал запуска reconcile в секундах
_RECONCILE_INTERVAL_SECONDS = 60
# Имя воркера для heartbeat
_WORKER_NAME = "meta_api_worker"


async def _heartbeat_loop(
    status_ref: list[str],
    message_ref: list[str | None],
    *,
    interval_seconds: int = 30,
) -> None:
    """Фоновая задача: отправляет heartbeat каждые N секунд."""
    while True:
        await update_worker_heartbeat(
            _WORKER_NAME,
            status=status_ref[0],
            message=message_ref[0],
        )
        await asyncio.sleep(interval_seconds)


async def meta_api_worker_loop(
    *,
    shutdown_event: asyncio.Event | None = None,
    poll_interval_seconds: int = _POLL_INTERVAL_SECONDS,
    reconcile_interval_seconds: int = _RECONCILE_INTERVAL_SECONDS,
) -> None:
    """Главный цикл meta_api_worker.

    Структура цикла:
    1. Heartbeat — раз в 30 сек (фоновая задача)
    2. Reconcile — раз в reconcile_interval_seconds
    3. Claim pending task → если есть, логируем TODO + mark_failed("Not implemented yet")
    4. Sleep poll_interval_seconds

    Args:
        shutdown_event: asyncio.Event для graceful shutdown по SIGTERM/SIGINT
        poll_interval_seconds: интервал поллинга очереди
        reconcile_interval_seconds: интервал запуска reconcile
    """
    if shutdown_event is None:
        shutdown_event = asyncio.Event()

    status_ref: list[str] = ["starting"]
    message_ref: list[str | None] = [None]

    # Запускаем фоновый heartbeat
    heartbeat_task = asyncio.create_task(_heartbeat_loop(status_ref, message_ref))

    last_reconcile_at: datetime | None = None
    session_factory = get_session_factory()

    logger.info("meta_api_worker: запущен (PID=%d)", os.getpid())
    status_ref[0] = "idle"

    try:
        while not shutdown_event.is_set():
            now = datetime.now(UTC)

            # ── Reconcile раз в N секунд ─────────────────────────────────────
            run_reconcile = (
                last_reconcile_at is None
                or (now - last_reconcile_at).total_seconds() >= reconcile_interval_seconds
            )
            if run_reconcile:
                try:
                    async with session_factory() as db:
                        counts = await reconcile_all(db)
                        await db.commit()
                    last_reconcile_at = now
                    if any(counts.values()):
                        logger.info(
                            "meta_api_worker: reconcile завершён — %s",
                            counts,
                        )
                except Exception:
                    logger.exception("meta_api_worker: ошибка reconcile")

            # ── Claim pending task ───────────────────────────────────────────
            try:
                async with session_factory() as db:
                    task = await claim_pending_task(db)
                    if task is None:
                        await db.commit()
                        status_ref[0] = "idle"
                        message_ref[0] = None
                        # Ждём следующий poll или shutdown
                        if await wait_for_shutdown_or_timeout(
                            shutdown_event, poll_interval_seconds
                        ):
                            break
                        continue

                    # Задача захвачена — логируем и возвращаем в FAILED (скелет)
                    status_ref[0] = "busy"
                    message_ref[0] = (
                        f"Задача {task.id} ({task.mutation_kind}): execution не реализован (Этап 5)"
                    )
                    logger.info(
                        "meta_api_worker: задача %s kind=%s target=%s — "
                        "TODO: execution реализуется на Этапе 5",
                        task.id,
                        task.mutation_kind,
                        task.target_id,
                    )

                    # Помечаем как FAILED с поясняющим сообщением
                    await mark_failed(
                        db,
                        task_id=task.id,
                        error_message="Not implemented: execution реализуется на Этапе 5",
                    )
                    await db.commit()

            except Exception:
                logger.exception("meta_api_worker: ошибка в цикле claim/execute")
                if await wait_for_shutdown_or_timeout(shutdown_event, poll_interval_seconds):
                    break

            status_ref[0] = "idle"
            message_ref[0] = None

    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        logger.info("meta_api_worker: остановлен")


async def main() -> None:
    """Точка входа: настройка signal handlers, Sentry и запуск loop."""
    from core.config import get_settings

    settings = get_settings()
    setup_sentry(dsn=settings.sentry_dsn, environment=settings.sentry_environment)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)
    loop.add_signal_handler(signal.SIGINT, shutdown_event.set)

    await meta_api_worker_loop(shutdown_event=shutdown_event)


if __name__ == "__main__":
    _PID_FILE = pathlib.Path("/tmp/fb_meta_api_worker.pid")
    try:
        with PidFileLock(_PID_FILE):
            asyncio.run(main())
    except RuntimeError as e:
        logger.error("%s", e)
        sys.exit(1)
