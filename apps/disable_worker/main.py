# -*- coding: utf-8 -*-
"""Disable Worker: выполняет задачи на отключение объявлений через Playwright-клик."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from core.domain import DisableTaskStatus
from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


async def disable_worker_loop(
    *,
    poll_interval_seconds: int = 5,
    claim_next_task,
    execute_disable,
    mark_succeeded,
    mark_retrying,
    send_completion_callback=None,
    telegram_bot_token: str = "",
    telegram_chat_id: str = "",
) -> None:
    """Бесконечный цикл обработки disable-задач из outbox.

    Args:
        poll_interval_seconds: интервал поллинга очереди
        claim_next_task: async () -> task | None — берёт задачу
        execute_disable: async (fb_ad_id) -> (success, message) — выполняет Playwright-клик
        mark_succeeded: async (task_id) -> None
        mark_retrying: async (task_id, error, next_retry_at) -> None
        send_completion_callback: async (task, success, message) -> None
        telegram_bot_token: для отбивки в TG
        telegram_chat_id: для отбивки в TG
    """
    tg_client = None
    if telegram_bot_token and telegram_chat_id:
        tg_client = TelegramBotClient(telegram_bot_token)

    while True:
        try:
            task = await claim_next_task()
            if task is None:
                await asyncio.sleep(poll_interval_seconds)
                continue

            logger.info(
                "Disable worker: выполняю задачу %s для объявления %s",
                task.id,
                task.fb_ad_id,
            )

            success, message = await execute_disable(task.fb_ad_id)

            if success:
                await mark_succeeded(task.id)
                logger.info("Объявление %s успешно отключено", task.fb_ad_id)

                # Отбивка в TG
                if tg_client and telegram_chat_id:
                    try:
                        await tg_client.send_message(
                            chat_id=telegram_chat_id,
                            text=(
                                f"✅ Объявление выключено\n\n"
                                f"Объявление: {task.ad_name}\n"
                                f"Ad ID: {task.fb_ad_id}\n"
                                f"Запросил: @{task.requested_by_username or 'неизвестно'}"
                            ),
                        )
                    except Exception:
                        logger.exception("Не удалось отправить отбивку в TG")
            else:
                # Retry с exponential backoff
                attempt = getattr(task, "attempt_count", 0) + 1
                delay = min(30 * (2 ** max(attempt - 1, 0)), 300)
                next_retry = datetime.now(tz=UTC) + timedelta(seconds=delay)
                await mark_retrying(task.id, message, next_retry)
                logger.warning(
                    "Не удалось отключить %s: %s. Retry через %s сек",
                    task.fb_ad_id,
                    message,
                    delay,
                )

            if send_completion_callback:
                await send_completion_callback(task, success, message)

        except Exception:
            logger.exception("Disable worker: ошибка в цикле")
            await asyncio.sleep(poll_interval_seconds)
