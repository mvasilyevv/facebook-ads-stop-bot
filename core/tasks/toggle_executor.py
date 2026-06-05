# -*- coding: utf-8 -*-
"""Общий executor для disable/enable воркеров.

Оба воркера полят task_queue по своему task_type и делают один и тот же gRPC-вызов
toggle_ad с разным target_state. Отличия минимальные — выносим в одну функцию.

После успешного завершения задачи (mark_succeeded) публикует событие
в Redis-канал fb_agent:task:changed (best-effort, опциональный redis_client).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.ownership import check_ad_ownership, load_owner_tag
from core.observer.queries import load_scanning_enabled
from core.observer.writers import (
    reset_alert_state_after_disable_succeeded,
    reset_alert_state_after_enable_succeeded,
)
from core.pubsub import CHANNEL_TASK_CHANGED
from core.tasks import (
    Task,
    claim_next_task,
    mark_failed,
    mark_succeeded,
    requeue_for_retry,
)

logger = logging.getLogger(__name__)

# TTL heartbeat-ключа (должен совпадать с ожиданием health_watchdog).
_HEARTBEAT_TTL_SECONDS = 60


class ToggleGate(Protocol):
    """Интерфейс gRPC-клиента к browser-agent (минимально нужный для воркеров).

    Реализуется clients/python_grpc/client.BrowserAgentClient.toggle_ad,
    а в тестах — fake-class который не ходит в сеть.
    """

    async def toggle_ad(self, fb_ad_id: str, target_state: bool = True) -> dict[str, Any]: ...


async def _publish_task_changed(
    redis_client: Any,
    *,
    task_id: int,
    task_type: str,
    status: str,
) -> None:
    """Best-effort publish изменения статуса задачи в fb_agent:task:changed."""
    if redis_client is None:
        return
    try:
        payload = json.dumps(
            {
                "task_id": task_id,
                "task_type": task_type,
                "status": status,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_TASK_CHANGED, payload)
    except Exception:
        # Publish не критичен — не роняем основной flow
        logger.warning("toggle_executor: не удалось publish task:changed task_id=%s", task_id)


async def execute_one_toggle_task(
    engine: AsyncEngine,
    *,
    task_type: str,  # 'disable' | 'enable'
    gate: ToggleGate,
    redis_client: Any = None,
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

    # Асимметричный стоп: на паузе сканирования НЕ включаем объявления (enable),
    # но выключающие действия (disable) разрешены — они снижают риск открута.
    # Задачу НЕ клеймим (return до claim_next_task) — она остаётся в очереди и
    # исполнится после снятия паузы, не теряя попыток и не фейлясь.
    if task_type == "enable" and not await load_scanning_enabled(engine):
        return "idle"

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

    # Owner-scoping на исполнении: не трогаем чужие объявления в шаренном кабинете.
    # Строгая политика: чужое (found, не owner) → permanent fail; своё, но ещё не в
    # каталоге (скан отстал) → disable в requeue (выключение подождёт каталог),
    # enable → fail (включающее не ждёт). owner_tag пуст → гейт пропускает всё.
    owner_tag = await load_owner_tag(engine)
    ownership = await check_ad_ownership(engine, fb_ad_id, owner_tag=owner_tag)
    if not ownership.allowed:
        if ownership.not_found and task_type == "disable":
            ok = await requeue_for_retry(
                engine,
                task_id=task.id,
                error=f"owner_scoping_not_found: {ownership.reason}",
                attempt_count=task.attempt_count,
                max_attempts=task.max_attempts,
            )
            logger.info(
                "[%s] task_id=%s ad=%s owner_scoping not_found → %s",
                task_type,
                task.id,
                fb_ad_id,
                "retry" if ok else "failed",
            )
            return "retrying" if ok else "failed"
        # Чужое объявление ИЛИ enable+not_found → окончательный отказ (не трогаем).
        await mark_failed(
            engine, task_id=task.id, error=f"owner_scoping_reject: {ownership.reason}"
        )
        logger.warning(
            "[%s] task_id=%s ad=%s ОТКЛОНЕНА owner-scoping: %s",
            task_type,
            task.id,
            fb_ad_id,
            ownership.reason,
        )
        return "failed"

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

    # Publish изменения статуса в Redis-канал (best-effort)
    await _publish_task_changed(
        redis_client,
        task_id=task.id,
        task_type=task_type,
        status="succeeded",
    )

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


async def _heartbeat_loop(redis_client: Any, worker_name: str, stop: asyncio.Event) -> None:
    """Периодически пишет worker:heartbeat:<worker_name> с TTL 60s.

    Фоновый таск — не блокирует основной цикл toggle.
    """
    key = f"worker:heartbeat:{worker_name}"
    interval = _HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(key, "alive", ex=_HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("[%s] heartbeat: ошибка записи в Redis", worker_name)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def run_toggle_loop(
    engine: AsyncEngine,
    *,
    task_type: str,
    gate_factory: Callable[[], Awaitable[ToggleGate]],
    idle_sleep_seconds: float = 3.0,
    error_sleep_seconds: float = 10.0,
    should_continue: Callable[[], bool] = lambda: True,
    stop_event: asyncio.Event | None = None,
    redis_client: Any = None,
) -> None:
    """Основной цикл disable/enable воркера.

    gate_factory создаёт BrowserAgentClient — один раз на запуск воркера.
    Если gate.toggle_ad крашится с фатальной ошибкой (например session_recovery
    исчерпан), цикл спит error_sleep_seconds и пытается пересоздать gate.

    Args:
        stop_event: если передан — цикл проверяет его после каждого батча и
            завершается gracefully. Используется для привязки к Redis-сигналу
            fb_agent:worker:restart:*.
        redis_client: опциональный Redis-клиент для heartbeat. Имя heartbeat-ключа
            берётся из task_type (disable → worker:heartbeat:disable).
            Должно совпадать с EXPECTED_WORKERS в health_watchdog.
    """
    # Запускаем heartbeat в фоне если передан Redis-клиент.
    hb_stop = asyncio.Event()
    hb_task: asyncio.Task | None = None
    if redis_client is not None:
        # task_type уже совпадает с именами в EXPECTED_WORKERS ("disable" / "enable")
        hb_task = asyncio.create_task(_heartbeat_loop(redis_client, task_type, hb_stop))

    gate: ToggleGate | None = None
    try:
        while should_continue():
            # Проверяем внешний stop_event перед каждой итерацией.
            if stop_event is not None and stop_event.is_set():
                logger.info("[%s] stop_event выставлен — завершаю loop", task_type)
                break
            try:
                if gate is None:
                    gate = await gate_factory()

                outcome = await execute_one_toggle_task(
                    engine,
                    task_type=task_type,
                    gate=gate,
                    redis_client=redis_client,
                )

                if outcome == "idle":
                    await asyncio.sleep(idle_sleep_seconds)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[%s] loop iteration crashed — пересоздаю gate", task_type)
                # пересоздадим клиент на следующей итерации
                gate = None
                await asyncio.sleep(error_sleep_seconds)
    finally:
        # Останавливаем heartbeat-таск при выходе из loop.
        hb_stop.set()
        if hb_task is not None:
            hb_task.cancel()
            try:
                await hb_task
            except asyncio.CancelledError:
                pass
