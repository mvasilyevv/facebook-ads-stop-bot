# -*- coding: utf-8 -*-
"""Telegram Poller: long polling для обработки команд и callback-кнопок."""

from __future__ import annotations

import asyncio
import logging
import sys

from core.config import get_settings
from core.telegram.client import TelegramBotClient
from core.telegram.bot_handler import handle_update

logger = logging.getLogger(__name__)


async def poller_loop(client: TelegramBotClient) -> None:
    """Бесконечный цикл long polling."""
    offset: int | None = None

    logger.info("Telegram poller запущен — жду сообщения и callback-и")

    while True:
        try:
            updates = await client.get_updates(offset=offset)
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    await handle_update(client, update)
                except Exception:
                    logger.exception("Ошибка обработки update %s", update.get("update_id"))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в long polling цикле")
            await asyncio.sleep(3)


async def main() -> None:
    """Точка входа для Telegram poller."""
    settings = get_settings()

    if not settings.telegram_bot_token:
        logger.error("Не задан TELEGRAM_BOT_TOKEN в .env")
        sys.exit(1)

    client = TelegramBotClient(settings.telegram_bot_token)

    # Получаем chat_id из первого сообщения, если не задан
    if not settings.telegram_chat_id:
        logger.info("TELEGRAM_CHAT_ID не задан — жду первое сообщение для определения...")

    try:
        await poller_loop(client)
    except KeyboardInterrupt:
        logger.info("Telegram poller остановлен")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(main())
