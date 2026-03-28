# -*- coding: utf-8 -*-
"""Telegram Poller: long polling для обработки команд и callback-кнопок."""

from __future__ import annotations

import asyncio
import logging
import sys

from core.config import get_settings
from core.telegram.bot_handler import handle_update
from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


async def poller_loop(client: TelegramBotClient) -> None:
    """Бесконечный цикл long polling."""
    offset: int | None = None

    logger.info("Telegram poller запущен — жду сообщения и callback-и")

    while True:
        try:
            updates = await client.get_updates(offset=offset)
            for update in updates:
                try:
                    await handle_update(client, update)
                except Exception:
                    logger.exception("Ошибка обработки update %s", update.get("update_id"))
                    continue
                offset = update["update_id"] + 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в long polling цикле")
            await asyncio.sleep(3)


async def _load_bot_token() -> str:
    """Загружает bot_token из БД (расшифровывая) с fallback на .env."""
    from sqlalchemy import select

    from core.crypto import decrypt
    from core.db import get_session_factory
    from core.models import TelegramSettings

    factory = get_session_factory()
    try:
        async with factory() as session:
            result = await session.execute(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            row = result.scalar_one_or_none()
            if row and row.bot_token_encrypted:
                token = decrypt(row.bot_token_encrypted)
                if token:
                    logger.info("Telegram bot_token загружен из БД")
                    return token
    except Exception:
        logger.debug("Не удалось загрузить TG токен из БД", exc_info=True)

    settings = get_settings()
    return settings.telegram_bot_token


async def main() -> None:
    """Точка входа для Telegram poller."""
    bot_token = await _load_bot_token()

    if not bot_token:
        logger.error("Не задан TELEGRAM_BOT_TOKEN ни в БД, ни в .env")
        sys.exit(1)

    client = TelegramBotClient(bot_token)

    settings = get_settings()
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
