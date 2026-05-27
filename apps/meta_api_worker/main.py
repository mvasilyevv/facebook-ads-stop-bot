# -*- coding: utf-8 -*-
"""meta_api_worker main loop.

Скелет Этапа 2: claim → выполнение-заглушка → mark.
На Этапе 5 execute_mutation станет реальной диспетчеризацией mutations.

Состояние процесса:
- heartbeat: Redis worker:heartbeat:meta_api TTL 60s
- reconcile: stuck running + stale drafts (своих task_type)
- idle: spinning poll с asyncio.sleep
- graceful: SIGTERM/SIGINT → завершить текущий цикл и закрыть ресурсы
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.meta_api.queue import (
    claim_pending_task,
    mark_task_failed,
    mark_task_succeeded,
    requeue_task,
)
from core.meta_api.reconciler import reconcile_all
from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import Task

logger = logging.getLogger("meta_api_worker")

WORKER_NAME = "meta_api"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60
IDLE_SLEEP_SECONDS = 5
RECONCILE_INTERVAL_SECONDS = 60


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


# ====================== mutation handler заглушка (Этап 5) ======================


async def execute_mutation(payload: MetaMutationPayload) -> dict[str, Any]:
    """Исполнить mutation. ЗАГЛУШКА — Этап 5.

    На Этапе 5 здесь будет:
        handler = MUTATION_HANDLERS[payload.mutation_kind]
        return await handler(meta_api_client, payload)

    Прямо сейчас — final fail с понятным сообщением.
    """
    raise NotImplementedError(
        f"Mutation '{payload.mutation_kind}' для target={payload.target_id} "
        "пока не реализована (см. Этап 5 META_INTEGRATION_PLAN)."
    )


async def process_one_task(engine: AsyncEngine, task: Task) -> None:
    """Полный жизненный цикл одной задачи."""
    try:
        payload = MetaMutationPayload.from_dict(task.payload)
    except (KeyError, ValueError) as exc:
        logger.error("Невалидный payload в task id=%s: %s", task.id, exc)
        await mark_task_failed(engine, task_id=task.id, error=f"invalid payload: {exc}")
        return

    logger.info(
        "meta_api: исполняю task id=%s kind=%s target=%s",
        task.id,
        payload.mutation_kind,
        payload.target_id,
    )

    try:
        result = await execute_mutation(payload)
        await mark_task_succeeded(engine, task_id=task.id, result=result)
        logger.info("meta_api: task id=%s succeeded", task.id)
    except NotImplementedError as exc:
        # Этап 5 не закончен — final fail без retry (retry не поможет).
        await mark_task_failed(engine, task_id=task.id, error=str(exc))
        logger.warning("meta_api: task id=%s — заглушка: %s", task.id, exc)
    except Exception as exc:  # noqa: BLE001
        retried = await requeue_task(engine, task=task, error=repr(exc))
        if retried:
            logger.warning("meta_api: task id=%s → retrying: %s", task.id, exc)
        else:
            logger.error("meta_api: task id=%s → final fail: %s", task.id, exc)


# ====================== sub-loops ======================


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Периодически обновляет worker:heartbeat:meta_api с TTL 60s."""
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def reconcile_loop(engine: AsyncEngine, stop: asyncio.Event) -> None:
    """Reconcile stuck/stale tasks раз в RECONCILE_INTERVAL_SECONDS."""
    while not stop.is_set():
        try:
            stats = await reconcile_all(engine)
            if stats.get("stuck_running") or stats.get("stale_drafts"):
                logger.info("reconcile: %s", stats)
        except Exception:  # noqa: BLE001
            logger.exception("reconcile loop: error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=RECONCILE_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def task_loop(engine: AsyncEngine, stop: asyncio.Event) -> None:
    """Главный цикл claim → execute → mark."""
    while not stop.is_set():
        try:
            claim = await claim_pending_task(engine)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка claim_pending_task")
            try:
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        if claim.queue_empty or claim.task is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        await process_one_task(engine, claim.task)


# ====================== entrypoint ======================


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, echo=False)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("meta_api_worker запущен")
    try:
        await asyncio.gather(
            task_loop(engine, stop),
            heartbeat_loop(redis_client, stop),
            reconcile_loop(engine, stop),
        )
    finally:
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        await engine.dispose()
        logger.info("meta_api_worker остановлен")
