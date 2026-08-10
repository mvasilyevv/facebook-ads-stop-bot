# -*- coding: utf-8 -*-
"""Общий HTML helper для ответов на durable Telegram updates."""

from __future__ import annotations

from core.telegram.handlers.protocol import TelegramUpdateClient


async def send_text(
    client: TelegramUpdateClient,
    *,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,
    parse_mode: str | None = "HTML",
) -> None:
    """Отправить текст и передать ошибку durable inbox policy.

    Ошибку нельзя скрывать: update помечается processed только после принятого
    Bot API ответа. ``TelegramHTMLGateway`` классифицирует неоднозначный
    ``sendMessage`` как UNKNOWN, поэтому worker не делает blind resend.
    """
    await client.send_message(
        chat_id=str(chat_id),
        text=text,
        parse_mode=parse_mode,
        reply_to_message_id=reply_to_message_id,
    )


__all__ = ["send_text"]
