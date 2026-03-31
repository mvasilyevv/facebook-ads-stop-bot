# -*- coding: utf-8 -*-
"""Безопасные операции редактирования и отправки сообщений Telegram."""

from __future__ import annotations

from typing import Any

from core.telegram.client import TelegramAPIError, TelegramBotClient

_IGNORABLE_EDIT_DESCRIPTIONS = ("message is not modified",)
_FALLBACK_TO_SEND_DESCRIPTIONS = (
    "message to edit not found",
    "message can't be edited",
    "there is no text in the message to edit",
)


def _normalized_description(exc: TelegramAPIError) -> str:
    """Нормализует описание ошибки Telegram API."""
    return (exc.description or "").strip().lower()


async def safe_edit_or_send_message(
    client: TelegramBotClient,
    *,
    chat_id: str,
    message_id: int | None,
    message_thread_id: int | None = None,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> tuple[str, int | None]:
    """Пытается отредактировать сообщение и откатывается к отправке нового."""
    if message_id is None:
        result = await client.send_message(
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            text=text,
            reply_markup=reply_markup,
        )
        return "sent", result.get("message_id") if isinstance(result, dict) else None

    try:
        await client.edit_message(
            chat_id=chat_id,
            message_id=message_id,
            message_thread_id=message_thread_id,
            text=text,
            reply_markup=reply_markup,
        )
        return "edited", message_id
    except TelegramAPIError as exc:
        description = _normalized_description(exc)
        if any(item in description for item in _IGNORABLE_EDIT_DESCRIPTIONS):
            return "unchanged", message_id
        if any(item in description for item in _FALLBACK_TO_SEND_DESCRIPTIONS):
            result = await client.send_message(
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                text=text,
                reply_markup=reply_markup,
            )
            return "sent", result.get("message_id") if isinstance(result, dict) else None
        raise
