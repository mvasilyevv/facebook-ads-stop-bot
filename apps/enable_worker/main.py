# -*- coding: utf-8 -*-
"""Enable Worker: цикл обработки задач на включение объявлений."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from core.observer.runtime_status import update_worker_heartbeat
from core.worker_utils import calculate_retry_delay

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 30
ENABLE_BROWSER_TASK_TIMEOUT_SECONDS = 60


async def _heartbeat_loop(status_ref: list[str], message_ref: list[str | None]) -> None:
    """Фоновая задача: отправляет heartbeat enable worker каждые 30 секунд."""
    while True:
        await update_worker_heartbeat(
            "enable",
            status=status_ref[0],
            message=message_ref[0],
        )
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


def _build_browser_runtime_error_message(exc: Exception) -> str:
    """Формирует текст ошибки браузерной операции для retry-задачи."""
    detail = str(exc).strip()
    if not detail:
        return "Браузерная операция включения завершилась ошибкой"
    return f"Браузерная операция включения завершилась ошибкой: {detail}"


async def _process_enable_task_result(
    *,
    task,
    success: bool,
    message: str,
    tg_client,
    tg_chat_id: str,
    send_completion_callback,
    mark_succeeded,
    mark_retrying,
    mark_failed,
) -> None:
    """Фиксирует итог обработки enable-задачи и рассылает runtime-обновление."""
    from core.domain import EnableTaskStatus
    from core.telegram.delivery import render_enable_task_runtime_message

    next_retry_at = None
    if success:
        await mark_succeeded(task.id)
        status = EnableTaskStatus.SUCCEEDED
        logger.info("Объявление %s успешно включено", task.fb_ad.fb_ad_id)
    else:
        attempt = task.attempt_count
        max_attempts = task.max_attempts
        if attempt >= max_attempts:
            await mark_failed(task.id, message)
            status = EnableTaskStatus.FAILED
            logger.error(
                "Задача %s для %s провалена: исчерпаны все %s попыток",
                task.id,
                task.fb_ad.fb_ad_id,
                max_attempts,
            )
        else:
            delay = calculate_retry_delay(attempt)
            next_retry_at = datetime.now(tz=UTC) + timedelta(seconds=delay)
            await mark_retrying(task.id, message, next_retry_at)
            status = EnableTaskStatus.RETRYING
            logger.warning(
                "Не удалось включить %s: %s. Retry через %s сек",
                task.fb_ad.fb_ad_id,
                message,
                delay,
            )

    if send_completion_callback:
        await send_completion_callback(task, status.value, message, next_retry_at)
        return

    if tg_client and tg_chat_id:
        try:
            await tg_client.send_message(
                chat_id=tg_chat_id,
                text=render_enable_task_runtime_message(
                    ad_name=task.fb_ad.ad_name,
                    fb_ad_id=task.fb_ad.fb_ad_id,
                    requested_by_username=task.requested_by_username or "",
                    status=status.value,
                    detail=message,
                    next_retry_at=next_retry_at,
                ),
            )
        except Exception:
            logger.exception("Не удалось отправить уведомление в TG")


async def enable_worker_loop(
    client,
    tg_client,
    tg_chat_id: str,
    poll_interval: int = 5,
    shutdown_event: asyncio.Event | None = None,
    send_completion_callback=None,
    status_ref: list[str] | None = None,
    message_ref: list[str | None] | None = None,
    *,
    claim_next_task,
    execute_enable,
    mark_succeeded,
    mark_retrying,
    mark_failed,
    cancel_if_alert_blocked,
    reconnect_browser,
    task_timeout: int = ENABLE_BROWSER_TASK_TIMEOUT_SECONDS,
) -> None:
    """Бесконечный цикл обработки задач на включение.

    Args:
        client: BrowserAgentClient для gRPC-вызовов.
        tg_client: опциональный Telegram-клиент для fallback-уведомлений.
        tg_chat_id: chat_id для fallback-уведомлений.
        poll_interval: интервал поллинга очереди в секундах.
        shutdown_event: событие остановки воркера.
        send_completion_callback: async (task, status, detail, next_retry_at) — lifecycle-колбэк.
        status_ref: внешний контейнер статуса для heartbeat.
        message_ref: внешний контейнер сообщения для heartbeat.
        claim_next_task: async () -> task | None — берёт задачу из очереди.
        execute_enable: async (client, fb_ad_id) -> (success, message) — включает объявление.
        mark_succeeded: async (task_id) -> None.
        mark_retrying: async (task_id, error, next_retry_at) -> None.
        mark_failed: async (task_id, error) -> None.
        cancel_if_alert_blocked: async (task_id) -> str | None — отмена при активном алерте.
        reconnect_browser: async (client) -> str — переподключает браузер.
        task_timeout: таймаут одной задачи включения в секундах.
    """
    _GRPC_MARKERS = (
        "unavailable",
        "connection refused",
        "connection closed",
        "connection reset",
        "transport closed",
        "goaway",
        "stream closed",
        "deadline exceeded",
    )

    def _is_grpc_error(exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, OSError)):
            return True
        msg = str(exc).casefold()
        return any(m in msg for m in _GRPC_MARKERS)

    if status_ref is None:
        status_ref = ["idle"]
    if message_ref is None:
        message_ref = [None]

    while not (shutdown_event and shutdown_event.is_set()):
        try:
            task = await claim_next_task()
            if task is None:
                status_ref[0] = "idle"
                message_ref[0] = None
                try:
                    if shutdown_event:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval)
                        break
                except asyncio.TimeoutError:
                    pass
                continue

            status_ref[0] = "busy"
            message_ref[0] = f"Задача {task.id} для {task.fb_ad.fb_ad_id}"

            logger.info(
                "Enable worker: выполняю задачу %s для объявления %s",
                task.id,
                task.fb_ad.fb_ad_id,
            )

            blocked_message = await cancel_if_alert_blocked(task.id)
            if blocked_message:
                from core.domain import EnableTaskStatus
                from core.telegram.delivery import broadcast_enable_task_runtime_message

                logger.warning(
                    "Enable worker: задача %s для %s отменена перед включением: %s",
                    task.id,
                    task.fb_ad.fb_ad_id,
                    blocked_message,
                )
                fb_ad = task.fb_ad
                await broadcast_enable_task_runtime_message(
                    ad_name=fb_ad.ad_name if fb_ad else "",
                    fb_ad_id=fb_ad.fb_ad_id if fb_ad else "",
                    requested_by_username=task.requested_by_username or "",
                    status=EnableTaskStatus.CANCELLED.value,
                    incident_key=(
                        str(task.recommendation_event_id) if task.recommendation_event_id else ""
                    ),
                    detail=blocked_message,
                    next_retry_at=None,
                )
                continue

            try:
                success, message = await asyncio.wait_for(
                    execute_enable(client, task.fb_ad.fb_ad_id),
                    timeout=task_timeout,
                )
            except asyncio.TimeoutError:
                timeout_message = (
                    f"Браузерная операция включения превысила таймаут {task_timeout} сек"
                )
                logger.error(
                    "Enable worker: задача %s для %s зависла дольше %s сек, переподключаю браузер",
                    task.id,
                    task.fb_ad.fb_ad_id,
                    task_timeout,
                )
                await _process_enable_task_result(
                    task=task,
                    success=False,
                    message=timeout_message,
                    tg_client=tg_client,
                    tg_chat_id=tg_chat_id,
                    send_completion_callback=send_completion_callback,
                    mark_succeeded=mark_succeeded,
                    mark_retrying=mark_retrying,
                    mark_failed=mark_failed,
                )
                await reconnect_browser(client)
                continue
            except Exception as exc:
                runtime_message = _build_browser_runtime_error_message(exc)
                logger.error(
                    "Enable worker: задача %s для %s завершилась ошибкой, переподключаю браузер",
                    task.id,
                    task.fb_ad.fb_ad_id,
                    exc_info=True,
                )
                await _process_enable_task_result(
                    task=task,
                    success=False,
                    message=runtime_message,
                    tg_client=tg_client,
                    tg_chat_id=tg_chat_id,
                    send_completion_callback=send_completion_callback,
                    mark_succeeded=mark_succeeded,
                    mark_retrying=mark_retrying,
                    mark_failed=mark_failed,
                )
                await reconnect_browser(client)
                continue

            await _process_enable_task_result(
                task=task,
                success=success,
                message=message,
                tg_client=tg_client,
                tg_chat_id=tg_chat_id,
                send_completion_callback=send_completion_callback,
                mark_succeeded=mark_succeeded,
                mark_retrying=mark_retrying,
                mark_failed=mark_failed,
            )

        except Exception as exc:
            if _is_grpc_error(exc):
                logger.error(
                    "Enable worker: потеряно соединение с browser-agent, нужен reconnect: %s", exc
                )
                try:
                    await reconnect_browser(client)
                except Exception:
                    logger.exception("Enable worker: не удалось переподключить browser-agent")
                    await asyncio.sleep(poll_interval)
                continue
            logger.exception("Enable worker: ошибка в цикле")
            await asyncio.sleep(poll_interval)
