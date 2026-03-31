# -*- coding: utf-8 -*-
"""Telegram Poller: long polling для обработки команд и callback-кнопок."""

from __future__ import annotations

import asyncio
import logging
import sys

from core.config import get_settings
from core.telegram.bot_handler import handle_update
from core.telegram.client import TelegramBotClient
from core.telegram.service import touch_poller_heartbeat

logger = logging.getLogger(__name__)
TOKEN_RELOAD_INTERVAL_SECONDS = 3
ERROR_RETRY_DELAY_SECONDS = 3


async def _process_updates(
    client: TelegramBotClient,
    *,
    offset: int | None,
) -> int | None:
    """Обрабатывает пачку update и всегда сдвигает offset, даже если один update оказался битым."""
    updates = await client.get_updates(offset=offset)
    for update in updates:
        update_id = update.get("update_id")
        try:
            await handle_update(client, update)
        except Exception:
            logger.exception("Ошибка обработки update %s", update_id)
        finally:
            if isinstance(update_id, int):
                offset = update_id + 1
    return offset


async def poller_loop(client: TelegramBotClient) -> None:
    """Бесконечный цикл long polling."""
    offset: int | None = None

    logger.info("Telegram poller запущен — жду сообщения и callback-и")

    while True:
        try:
            offset = await _process_updates(client, offset=offset)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка в long polling цикле")
            await asyncio.sleep(ERROR_RETRY_DELAY_SECONDS)


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


async def poller_runtime_loop(
    *,
    token_loader=_load_bot_token,
    client_factory=TelegramBotClient,
    reload_interval_seconds: int = TOKEN_RELOAD_INTERVAL_SECONDS,
) -> None:
    """Поддерживает живой poller, который ждёт появления токена и безопасно переживает ротацию."""
    offset: int | None = None
    current_token = ""
    client: TelegramBotClient | None = None
    waiting_for_token_logged = False

    logger.info("Telegram poller запущен")

    try:
        while True:
            await touch_poller_heartbeat()

            token = (await token_loader()).strip()
            if not token:
                if client is not None:
                    await client.close()
                    client = None
                    current_token = ""
                if not waiting_for_token_logged:
                    logger.info("Telegram poller ждёт bot_token и продолжает работать в фоне")
                    waiting_for_token_logged = True
                await asyncio.sleep(reload_interval_seconds)
                continue

            if token != current_token or client is None:
                if client is not None:
                    await client.close()
                client = client_factory(token)
                current_token = token
                waiting_for_token_logged = False
                logger.info("Telegram poller подключён к актуальному bot_token")

            try:
                offset = await _process_updates(client, offset=offset)
                await touch_poller_heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка в runtime-цикле Telegram poller")
                await client.close()
                client = None
                current_token = ""
                await asyncio.sleep(ERROR_RETRY_DELAY_SECONDS)
    finally:
        if client is not None:
            await client.close()


async def main() -> None:
    """Точка входа для Telegram poller."""
    settings = get_settings()
    if not settings.telegram_chat_id:
        logger.info("TELEGRAM_CHAT_ID не задан — жду первое сообщение для определения...")

    try:
        await poller_runtime_loop()
    except KeyboardInterrupt:
        logger.info("Telegram poller остановлен")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(main())
