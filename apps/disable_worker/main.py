# -*- coding: utf-8 -*-
"""Disable Worker: выполняет задачи на отключение объявлений через Playwright-клик."""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import UTC, datetime, timedelta

from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


async def disable_worker_loop(
    *,
    poll_interval_seconds: int = 5,
    claim_next_task,
    execute_disable,
    mark_succeeded,
    mark_retrying,
    mark_failed=None,
    send_completion_callback=None,
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
        telegram_bot_token: для отбивки в TG
        telegram_chat_id: для отбивки в TG
    """
    tg_client = None
    if telegram_bot_token and telegram_chat_id:
        tg_client = TelegramBotClient(telegram_bot_token)

    while not (shutdown_event and shutdown_event.is_set()):
        try:
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

            success, message = await execute_disable(task.fb_ad_id)

            if success:
                await mark_succeeded(task.id)
                logger.info(
                    "Клик по выключению выполнен для %s, ждём подтверждения OFF: %s",
                    task.fb_ad_id,
                    message,
                )

                # Отбивка в TG
                if tg_client and telegram_chat_id:
                    try:
                        await tg_client.send_message(
                            chat_id=telegram_chat_id,
                            text=(
                                f"⏳ <b>Клик по выключению выполнен</b>\n\n"
                                f"📢 {html.escape(task.ad_name)}\n"
                                f"🆔 <code>{task.fb_ad_id}</code>\n"
                                f"👤 Запросил: @{task.requested_by_username or 'неизвестно'}\n"
                                "🔎 Ждём подтверждения статуса OFF в следующем скане"
                            ),
                        )
                    except Exception:
                        logger.exception("Не удалось отправить отбивку в TG")
            else:
                # Проверяем лимит попыток
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
                    # Retry с exponential backoff (30с → 5мин макс)
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
