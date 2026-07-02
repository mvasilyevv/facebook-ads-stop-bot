# -*- coding: utf-8 -*-
"""creator_worker main loop.

Поллит task_queue task_type='plan_run' и исполняет планы через
CreatorService.RunPlan (gRPC stream) внутри Vision-сессии browser-agent.

Состояние процесса:
- heartbeat: Redis worker:heartbeat:creator TTL 60s
- task_loop: claim → load plan из creator_plans → stream RunPlan → mark
- graceful: SIGTERM/SIGINT → завершить текущий план и закрыть ресурсы

Маршрутизация ошибок внутри process_one_task:
- ValueError / NotImplementedError → mark_failed (плохой payload или незнакомый шаг)
- "plan not found" / archived → mark_failed
- StepFailed / PlanComplete(ok=false) → mark_failed (план упал на FB)
- BrowserUnavailableError / TimeoutError / grpc.RpcError → requeue_for_retry (transient)
- любое другое Exception внутри _execute_plan_stream → requeue (защитная сетка)

Money-safety (H-3): plan_run — необратимая мутация (реальный залив FB-кампании через
Vision), входит в core.tasks.queue.IRREVERSIBLE_TASK_TYPES — reconciler НЕ переводит
зависшую в 'running' задачу обратно в 'retrying' (иначе повторный залив = дубль
кампании и двойной открут бюджета). Поэтому task_loop оборачивает вызов
process_one_task в try/except: неожиданный краш (напр. БД-сбой ДО входа во внутренние
try/except process_one_task) логируется и задача явно уводится в mark_failed —
цикл воркера не падает и задача не остаётся вечно висеть в 'running'.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from typing import Any
from uuid import UUID

import grpc
import redis.asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from clients.python_grpc.client import (
    BrowserAgentClient,
    BrowserAgentConfig,
    BrowserUnavailableError,
)
from core.db import WORKER_ENGINE_KWARGS
from core.tasks.queue import (
    Task,
    claim_next_task,
    mark_failed,
    mark_succeeded,
    requeue_for_retry,
)

logger = logging.getLogger("creator_worker")

WORKER_NAME = "creator"
TASK_TYPE = "plan_run"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60
IDLE_SLEEP_SECONDS = 5


# Permanent ошибки → mark_failed (retry не имеет смысла).
_PERMANENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ValueError,
    NotImplementedError,
    KeyError,
)

# Transient ошибки → requeue_for_retry.
_TEMPORARY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    BrowserUnavailableError,
    asyncio.TimeoutError,
    ConnectionError,
    grpc.RpcError,
)


# ====================== config helpers ======================


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


def _build_browser_client() -> BrowserAgentClient:
    """gRPC-клиент browser-agent с настройками Vision из get_settings()."""
    from core.config import get_settings, reveal_secret

    settings = get_settings()
    config = BrowserAgentConfig(
        grpc_host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        grpc_port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
        vision_x_token=reveal_secret(settings.vision_x_token),
        vision_api_url=settings.vision_api_url,
        vision_profile_id=settings.vision_profile_id,
    )
    return BrowserAgentClient(config)


# ====================== plan loader ======================


async def load_plan(engine: AsyncEngine, plan_id: str) -> dict[str, Any] | None:
    """Прочитать creator_plans запись. None если не найдена или архивирована.

    Возвращает dict с полями id/name/schema_version/steps/variables.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, name, schema_version, steps, variables, is_archived
                    FROM creator_plans
                    WHERE id = :pid
                    LIMIT 1
                    """
                ),
                {"pid": plan_id},
            )
        ).first()
    if not row:
        return None
    if bool(row[5]):  # is_archived
        return None
    steps = row[3]
    if isinstance(steps, str):
        steps = json.loads(steps)
    variables = row[4]
    if isinstance(variables, str):
        variables = json.loads(variables)
    return {
        "id": str(row[0]),
        "name": str(row[1]),
        "schema_version": int(row[2]),
        "steps": steps or [],
        "variables": variables or {},
    }


def _parse_plan_id(payload: dict[str, Any]) -> str:
    """Извлечь и провалидировать plan_id (UUID) из payload. Бросает ValueError."""
    raw = payload.get("plan_id")
    if not raw:
        raise ValueError("payload без plan_id")
    try:
        UUID(str(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"invalid plan_id: {raw!r}") from exc
    return str(raw)


# ====================== plan execution ======================


async def _execute_plan_stream(
    client: BrowserAgentClient,
    *,
    plan_json: str,
    variables_json: str,
    task_id: int,
) -> dict[str, Any]:
    """Запустить RunPlan stream и агрегировать события.

    Возвращает dict с полями ok/steps_executed/total_steps/duration_ms/error/checkpoints.
    """
    steps_executed = 0
    failed_step: str | None = None
    last_error: str | None = None
    total_steps = 0
    duration_ms = 0
    checkpoints: list[dict[str, str]] = []

    async for event in client.run_plan(plan_json, variables_json):
        if event.HasField("started"):
            logger.debug(
                "plan_run task=%s step started: %s (idx=%d)",
                task_id,
                event.started.step,
                event.started.index,
            )
        elif event.HasField("finished"):
            steps_executed += 1
            logger.info(
                "plan_run task=%s step finished: %s (idx=%d)",
                task_id,
                event.finished.step,
                event.finished.index,
            )
        elif event.HasField("failed"):
            failed_step = event.failed.step
            last_error = event.failed.error or "step failed"
            logger.warning(
                "plan_run task=%s step failed: %s: %s",
                task_id,
                event.failed.step,
                event.failed.error,
            )
        elif event.HasField("skipped"):
            logger.info(
                "plan_run task=%s step skipped: %s (%s)",
                task_id,
                event.skipped.step,
                event.skipped.reason,
            )
        elif event.HasField("checkpoint"):
            checkpoints.append({"url": event.checkpoint.url, "detail": event.checkpoint.detail})
            logger.warning(
                "plan_run task=%s CHECKPOINT detected: %s | %s",
                task_id,
                event.checkpoint.url,
                event.checkpoint.detail,
            )
        elif event.HasField("complete"):
            total_steps = int(event.complete.total_steps or 0)
            duration_ms = int(event.complete.duration_ms or 0)
            if not event.complete.ok:
                last_error = event.complete.error or last_error or "plan failed"

    ok = failed_step is None and not last_error
    return {
        "ok": ok,
        "steps_executed": steps_executed,
        "total_steps": total_steps,
        "duration_ms": duration_ms,
        "failed_step": failed_step,
        "error": last_error,
        "checkpoints": checkpoints,
    }


async def process_one_task(
    engine: AsyncEngine,
    task: Task,
    *,
    client: BrowserAgentClient | None,
) -> None:
    """Полный жизненный цикл одной plan_run задачи.

    client опционален — None используется только в тестах с моками;
    в production main_loop всегда передаёт реальный BrowserAgentClient.
    """
    payload = task.payload or {}

    try:
        plan_id = _parse_plan_id(payload)
    except ValueError as exc:
        await mark_failed(engine, task_id=task.id, error=str(exc))
        logger.warning("plan_run: task id=%s → invalid payload: %s", task.id, exc)
        return

    plan = await load_plan(engine, plan_id)
    if plan is None:
        await mark_failed(
            engine,
            task_id=task.id,
            error=f"plan not found or archived: {plan_id}",
        )
        logger.warning("plan_run: task id=%s → plan %s missing/archived", task.id, plan_id)
        return

    if client is None:
        await mark_failed(
            engine,
            task_id=task.id,
            error="BrowserAgentClient не доступен (Vision-сессия?)",
        )
        return

    plan_json = json.dumps(
        {"schema_version": plan["schema_version"], "steps": plan["steps"]},
        ensure_ascii=False,
    )
    variables_json = json.dumps(plan["variables"], ensure_ascii=False)

    logger.info("plan_run: исполняю task id=%s plan='%s' (%s)", task.id, plan["name"], plan_id)

    try:
        result = await _execute_plan_stream(
            client,
            plan_json=plan_json,
            variables_json=variables_json,
            task_id=task.id,
        )
    except _PERMANENT_EXCEPTIONS as exc:
        await mark_failed(engine, task_id=task.id, error=repr(exc))
        logger.warning("plan_run: task id=%s → permanent fail: %s", task.id, exc)
        return
    except _TEMPORARY_EXCEPTIONS as exc:
        retried = await requeue_for_retry(
            engine,
            task_id=task.id,
            error=repr(exc),
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
        )
        if retried:
            logger.warning("plan_run: task id=%s → retrying (transient): %s", task.id, exc)
        else:
            logger.error("plan_run: task id=%s → exhausted retries (transient): %s", task.id, exc)
        return
    except Exception as exc:  # noqa: BLE001 — защитная сетка на неклассифицированное
        retried = await requeue_for_retry(
            engine,
            task_id=task.id,
            error=repr(exc),
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
        )
        if retried:
            logger.warning("plan_run: task id=%s → retrying (unknown): %s", task.id, exc)
        else:
            logger.error("plan_run: task id=%s → final fail (unknown): %s", task.id, exc)
        return

    if result["ok"]:
        await mark_succeeded(engine, task_id=task.id, result=result)
        logger.info(
            "plan_run: task id=%s succeeded (steps=%d/%d, %d ms)",
            task.id,
            result["steps_executed"],
            result["total_steps"],
            result["duration_ms"],
        )
    else:
        err = result.get("error") or "plan failed"
        await mark_failed(engine, task_id=task.id, error=str(err))
        logger.warning(
            "plan_run: task id=%s → plan failed on step '%s': %s",
            task.id,
            result.get("failed_step"),
            err,
        )


# ====================== sub-loops ======================


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Периодически обновляет worker:heartbeat:creator с TTL 60s."""
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


async def task_loop(
    engine: AsyncEngine,
    stop: asyncio.Event,
    *,
    client: BrowserAgentClient,
) -> None:
    """Главный цикл claim → execute → mark."""
    while not stop.is_set():
        try:
            claim = await claim_next_task(engine, task_type=TASK_TYPE)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка claim_next_task")
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

        try:
            await process_one_task(engine, claim.task, client=client)
        except Exception:  # noqa: BLE001 — защитная сетка: неожиданный краш не должен ронять цикл
            # process_one_task сам маршрутизирует ожидаемые ошибки (mark_failed/requeue),
            # но здесь ловим то, что вылетело мимо (напр. БД-сбой в load_plan/mark_*).
            # Без этого try/except задача осталась бы в 'running' навсегда: plan_run —
            # необратимая мутация (залив FB-кампании), reconciler её не ретраит
            # (IRREVERSIBLE_TASK_TYPES), поэтому единственный шанс закрыть задачу — здесь.
            logger.exception(
                "plan_run: непредвиденная ошибка обработки task id=%s — помечаю failed",
                claim.task.id,
            )
            try:
                await mark_failed(
                    engine,
                    task_id=claim.task.id,
                    error="unexpected crash in task_loop — см. логи creator_worker",
                )
            except Exception:  # noqa: BLE001 — даже mark_failed не должен ронять воркер
                logger.exception(
                    "plan_run: mark_failed после краша тоже не удался task id=%s",
                    claim.task.id,
                )


# ====================== entrypoint ======================


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    browser_client = _build_browser_client()
    await browser_client.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("creator_worker запущен (BrowserAgentClient ready)")
    try:
        await asyncio.gather(
            task_loop(engine, stop, client=browser_client),
            heartbeat_loop(redis_client, stop),
        )
    finally:
        try:
            await browser_client.close()
        except Exception:  # noqa: BLE001
            logger.exception("browser_client.close() упал")
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        await engine.dispose()
        logger.info("creator_worker остановлен")


__all__ = [
    "WORKER_NAME",
    "TASK_TYPE",
    "HEARTBEAT_KEY",
    "load_plan",
    "process_one_task",
    "heartbeat_loop",
    "task_loop",
    "main_loop",
]
