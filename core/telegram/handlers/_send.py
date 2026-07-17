# -*- coding: utf-8 -*-
"""Общий helper для отправки текстовых сообщений в TG.

Глушит сетевые ошибки (логирует через logger.exception). По умолчанию клиент
отправляет HTML/Markdown через Bot API Rich Messages.
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
    parse_mode: str | None = "HTML",
) -> None:
    """Отправить текст в чат. Не падает на сетевых ошибках.

    Дефолт parse_mode=HTML — единый стиль «чистая карточка» для всех сообщений.
    Для генерируемого markdown-контента (AI-ответ, отчёт /spy, каталог /tools)
    передавай parse_mode='Markdown' явно на месте вызова.
    """
    try:
        await client.send_message(
            chat_id=str(chat_id),
            text=text,
            message_thread_id=message_thread_id,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
        )
    except Exception:
        logger.exception("send_message failed")


__all__ = ["send_text"]
