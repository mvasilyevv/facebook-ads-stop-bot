# -*- coding: utf-8 -*-
"""Общий executor для disable/enable воркеров.

Оба воркера полят task_queue по своему task_type и делают один и тот же gRPC-вызов
toggle_ad с разным target_state. Отличия минимальные — выносим в одну функцию.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from core.tasks import (
    Task,
    claim_next_task,
    mark_succeeded,
    requeue_for_retry,
)

logger = logging.getLogger(__name__)


class ToggleGate(Protocol):
    """Интерфейс gRPC-клиента к browser-agent (минимально нужный для воркеров).

    Реализуется clients/python_grpc/client.BrowserAgentClient.toggle_ad,
    а в тестах — fake-class который не ходит в сеть.
    """

    async def toggle_ad(self, fb_ad_id: str, target_state: bool = True) -> dict[str, Any]: ...


async def execute_one_toggle_task(
    engine: AsyncEngine,
    *,
    task_type: str,  # 'disable' | 'enable'
    gate: ToggleGate,
) -> str:
    """Атомарный шаг воркера: claim → toggle → mark.

    Returns:
        'idle'       — очередь пуста
        'succeeded'  — задача выполнена, gRPC вернул success=True
        'retrying'   — gRPC упал или ad ещё не перешёл, поставлена retry
        'failed'     — исчерпан max_attempts → status='failed'

    NB: единственная функция воркера которая трогает БД + сеть. Всё остальное —
    main loop sleep/recovery вокруг.
    """
    if task_type not in ("disable", "enable"):
        raise ValueError(f"toggle worker handles only disable/enable, got {task_type!r}")

    target_state = task_type == "enable"  # enable=True → ON, disable=False → OFF

    claim = await claim_next_task(engine, task_type=task_type)
    if claim.queue_empty or claim.task is None:
        return "idle"

    task: Task = claim.task
    fb_ad_id = str(task.payload.get("fb_ad_id") or "")
    if not fb_ad_id:
        await requeue_for_retry(
            engine,
            task_id=task.id,
            error="payload не содержит fb_ad_id",
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
        )
        return "failed" if task.attempt_count + 1 >= task.max_attempts else "retrying"

    try:
        result = await gate.toggle_ad(fb_ad_id, target_state=target_state)
    except Exception as exc:
        logger.warning("[%s] toggle_ad RPC failed for fb_ad_id=%s: %s", task_type, fb_ad_id, exc)
        ok = await requeue_for_retry(
            engine,
            task_id=task.id,
            error=f"gRPC error: {exc!r}",
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
        )
        return "retrying" if ok else "failed"

    if not result.get("success", False):
        # gRPC ответил но клик не отработал — retry
        msg = f"toggle returned success=false (final_state={result.get('final_state')!r})"
        logger.info("[%s] %s for fb_ad_id=%s", task_type, msg, fb_ad_id)
        ok = await requeue_for_retry(
            engine,
            task_id=task.id,
            error=msg,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
        )
        return "retrying" if ok else "failed"

    await mark_succeeded(
        engine,
        task_id=task.id,
        result={
            "fb_ad_id": fb_ad_id,
            "final_state": result.get("final_state"),
        },
    )
    logger.info(
        "[%s] task_id=%s ad=%s → success final_state=%s",
        task_type,
        task.id,
        fb_ad_id,
        result.get("final_state"),
    )
    return "succeeded"


async def run_toggle_loop(
    engine: AsyncEngine,
    *,
    task_type: str,
    gate_factory: Callable[[], Awaitable[ToggleGate]],
    idle_sleep_seconds: float = 3.0,
    error_sleep_seconds: float = 10.0,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    """Основной цикл disable/enable воркера.

    gate_factory создаёт BrowserAgentClient — один раз на запуск воркера.
    Если gate.toggle_ad крашится с фатальной ошибкой (например session_recovery
    исчерпан), цикл спит error_sleep_seconds и пытается пересоздать gate.
    """
    import asyncio

    gate: ToggleGate | None = None
    while should_continue():
        try:
            if gate is None:
                gate = await gate_factory()

            outcome = await execute_one_toggle_task(engine, task_type=task_type, gate=gate)

            if outcome == "idle":
                await asyncio.sleep(idle_sleep_seconds)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("[%s] loop iteration crashed — пересоздаю gate", task_type)
            # пересоздадим клиент на следующей итерации
            gate = None
            await asyncio.sleep(error_sleep_seconds)
