# -*- coding: utf-8 -*-
"""Disable Worker: выполняет задачи на отключение объявлений через Playwright-клик."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

DISABLE_BROWSER_TASK_TIMEOUT_SECONDS = 60
DISABLE_BROWSER_BATCH_TIMEOUT_SECONDS = 120


class BrowserOperationTimeoutError(RuntimeError):
    """Браузерная операция disable worker превысила допустимый таймаут."""


def _build_browser_timeout_message(timeout_seconds: int, *, batch_size: int | None = None) -> str:
    """Формирует текст ошибки таймаута браузерной операции."""
    if batch_size and batch_size > 1:
        return (
            f"Браузерная операция для пачки из {batch_size} задач "
            f"превысила таймаут {timeout_seconds} сек"
        )
    return f"Браузерная операция превысила таймаут {timeout_seconds} сек"


async def _process_disable_result(
    *,
    task,
    success: bool,
    message: str,
    mark_succeeded,
    mark_retrying,
    mark_failed,
    send_completion_callback,
) -> None:
    """Фиксирует итог обработки одной disable-задачи."""
    if success:
        await mark_succeeded(task.id)
        logger.info(
            "Клик по выключению выполнен для %s, ждём подтверждения OFF: %s",
            task.fb_ad_id,
            message,
        )
    else:
        attempt = getattr(task, "attempt_count", 0)
        max_att = getattr(task, "max_attempts", 10)
        if attempt >= max_att and mark_failed:
            await mark_failed(task.id, message)
            logger.error(
                "Задача %s для %s провалена: исчерпаны все %s попыток",
                task.id,
                task.fb_ad_id,
                max_att,
            )
        else:
            delay = min(30 * (2 ** max(attempt - 1, 0)), 300)
            next_retry = datetime.now(tz=UTC) + timedelta(seconds=delay)
            await mark_retrying(task.id, message, next_retry)
            logger.warning(
                "Не удалось отключить %s: %s. Повтор через %s сек",
                task.fb_ad_id,
                message,
                delay,
            )

    if send_completion_callback:
        await send_completion_callback(task, success, message)


async def disable_worker_loop(
    *,
    poll_interval_seconds: int = 5,
    claim_next_task,
    execute_disable,
    mark_succeeded,
    mark_retrying,
    mark_failed=None,
    send_completion_callback=None,
    claim_task_batch=None,
    execute_disable_batch=None,
    batch_size: int = 10,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
    shutdown_event=None,
    **kwargs,
) -> None:
    """Бесконечный цикл обработки disable-задач из outbox.

    Args:
        poll_interval_seconds: интервал поллинга очереди
        claim_next_task: async () -> task | None — берёт задачу
        execute_disable: async (fb_ad_id) -> (success, message) — выполняет Playwright-клик
        mark_succeeded: async (task_id) -> None
        mark_retrying: async (task_id, error, next_retry_at) -> None
        mark_failed: async (task_id, error) -> None — помечает задачу как окончательно проваленную
        send_completion_callback: async (task, success, message) -> None
        claim_task_batch: async (limit) -> list[task] — берёт пачку задач
        execute_disable_batch: async (tasks) -> {task_id: (success, message)} — пакетный обход
        telegram_bot_token: резервный токен Telegram для lifecycle-колбэка
        telegram_chat_id: резервный chat_id Telegram для lifecycle-колбэка
    """
    while not (shutdown_event and shutdown_event.is_set()):
        try:
            if claim_task_batch and execute_disable_batch:
                tasks = await claim_task_batch(batch_size)
                if not tasks:
                    try:
                        if shutdown_event:
                            await asyncio.wait_for(
                                shutdown_event.wait(), timeout=poll_interval_seconds
                            )
                            break
                    except asyncio.TimeoutError:
                        pass
                    else:
                        await asyncio.sleep(poll_interval_seconds)
                    continue

                logger.info("Disable worker: взял пачку из %s задач", len(tasks))
                try:
                    batch_results = await asyncio.wait_for(
                        execute_disable_batch(tasks),
                        timeout=DISABLE_BROWSER_BATCH_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    timeout_message = _build_browser_timeout_message(
                        DISABLE_BROWSER_BATCH_TIMEOUT_SECONDS,
                        batch_size=len(tasks),
                    )
                    logger.error(
                        "Disable worker: пакетная обработка зависла дольше %s сек, переподключаю браузер",
                        DISABLE_BROWSER_BATCH_TIMEOUT_SECONDS,
                    )
                    for task in tasks:
                        await _process_disable_result(
                            task=task,
                            success=False,
                            message=timeout_message,
                            mark_succeeded=mark_succeeded,
                            mark_retrying=mark_retrying,
                            mark_failed=mark_failed,
                            send_completion_callback=send_completion_callback,
                        )
                    raise BrowserOperationTimeoutError(timeout_message) from exc

                for task in tasks:
                    success, message = batch_results.get(
                        task.id,
                        (False, "Пакетная обработка не вернула результат по задаче"),
                    )
                    await _process_disable_result(
                        task=task,
                        success=success,
                        message=message,
                        mark_succeeded=mark_succeeded,
                        mark_retrying=mark_retrying,
                        mark_failed=mark_failed,
                        send_completion_callback=send_completion_callback,
                    )
                continue

            task = await claim_next_task()
            if task is None:
                try:
                    if shutdown_event:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval_seconds)
                        break
                except asyncio.TimeoutError:
                    pass
                else:
                    await asyncio.sleep(poll_interval_seconds)
                continue

            logger.info(
                "Disable worker: выполняю задачу %s для объявления %s",
                task.id,
                task.fb_ad_id,
            )

            try:
                success, message = await asyncio.wait_for(
                    execute_disable(task.fb_ad_id),
                    timeout=DISABLE_BROWSER_TASK_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                timeout_message = _build_browser_timeout_message(
                    DISABLE_BROWSER_TASK_TIMEOUT_SECONDS,
                )
                logger.error(
                    "Disable worker: задача %s для %s зависла дольше %s сек, переподключаю браузер",
                    task.id,
                    task.fb_ad_id,
                    DISABLE_BROWSER_TASK_TIMEOUT_SECONDS,
                )
                await _process_disable_result(
                    task=task,
                    success=False,
                    message=timeout_message,
                    mark_succeeded=mark_succeeded,
                    mark_retrying=mark_retrying,
                    mark_failed=mark_failed,
                    send_completion_callback=send_completion_callback,
                )
                raise BrowserOperationTimeoutError(timeout_message) from exc

            await _process_disable_result(
                task=task,
                success=success,
                message=message,
                mark_succeeded=mark_succeeded,
                mark_retrying=mark_retrying,
                mark_failed=mark_failed,
                send_completion_callback=send_completion_callback,
            )

        except BrowserOperationTimeoutError:
            raise
        except Exception:
            logger.exception("Disable worker: ошибка в цикле")
            await asyncio.sleep(poll_interval_seconds)
