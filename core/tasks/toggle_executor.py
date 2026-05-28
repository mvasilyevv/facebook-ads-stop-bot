# -*- coding: utf-8 -*-
"""Общий executor для disable/enable воркеров.

Оба воркера полят task_queue по своему task_type и делают один и тот же gRPC-вызов
toggle_ad с разным target_state. Отличия минимальные — выносим в одну функцию.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.writers import (
    reset_alert_state_after_disable_succeeded,
    reset_alert_state_after_enable_succeeded,
)
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

    applied = await mark_succeeded(
        engine,
        task_id=task.id,
        result={
            "fb_ad_id": fb_ad_id,
            "final_state": result.get("final_state"),
        },
    )

    if not applied:
        # Race: другой воркер (после reconciler-таймаута) уже завершил задачу.
        # Side-effect toggle_ad на browser-agent мы уже сделали (что не идеально),
        # но FSM-sync пропускаем — победитель его сделал.
        logger.warning(
            "[%s] task_id=%s ad=%s: mark_succeeded не применился "
            "(status != running) — гонка с другим воркером, пропускаю FSM-sync",
            task_type,
            task.id,
            fb_ad_id,
        )
        return "succeeded"

    # FSM-синхронизация: финальное состояние ad_alert_state должно соответствовать
    # реально применённому действию. Идемпотентно — если уже в нужном state, no-op.
    try:
        if task_type == "disable":
            await reset_alert_state_after_disable_succeeded(engine, fb_ad_id=fb_ad_id)
        else:
            await reset_alert_state_after_enable_succeeded(engine, fb_ad_id=fb_ad_id)
    except Exception as exc:
        # FSM-sync не критичен для outbox-контракта (задача уже succeeded). Логируем
        # и идём дальше — следующий observer-цикл всё равно увидит реальное состояние.
        logger.warning(
            "[%s] reset_alert_state_after_*_succeeded для fb_ad_id=%s упал: %s",
            task_type,
            fb_ad_id,
            exc,
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
    stop_event: asyncio.Event | None = None,
) -> None:
    """Основной цикл disable/enable воркера.

    gate_factory создаёт BrowserAgentClient — один раз на запуск воркера.
    Если gate.toggle_ad крашится с фатальной ошибкой (например session_recovery
    исчерпан), цикл спит error_sleep_seconds и пытается пересоздать gate.

    Args:
        stop_event: если передан — цикл проверяет его после каждого батча и
            завершается gracefully. Используется для привязки к Redis-сигналу
            fb_agent:worker:restart:*.
    """
    gate: ToggleGate | None = None
    while should_continue():
        # Проверяем внешний stop_event перед каждой итерацией.
        if stop_event is not None and stop_event.is_set():
            logger.info("[%s] stop_event выставлен — завершаю loop", task_type)
            break
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
