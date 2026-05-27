# -*- coding: utf-8 -*-
"""Общий helper для отправки текстовых сообщений в TG.

Глушит сетевые ошибки (логирует через logger.exception). reply_to_message_id
принимается, но клиент его не передаёт — оставлено в сигнатуре для документации
вызывающего кода.
"""

from __future__ import annotations

import logging

from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)


async def send_text(
    client: TelegramBotClient,
    *,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    message_thread_id: int | None = None,
    parse_mode: str | None = "Markdown",
) -> None:
    """Отправить текст в чат. Не падает на сетевых ошибках."""
    _ = reply_to_message_id  # клиент не поддерживает — оставлено для документации
    try:
        await client.send_message(
            chat_id=str(chat_id),
            text=text,
            message_thread_id=message_thread_id,
            parse_mode=parse_mode,
        )
    except Exception:
        logger.exception("send_message failed")


__all__ = ["send_text"]
