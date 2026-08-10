# -*- coding: utf-8 -*-
"""Structural contract exposed to durable Telegram update handlers."""

from __future__ import annotations

from typing import Any, Protocol


class TelegramUpdateClient(Protocol):
    """Only Bot API operations that active webhook handlers may request."""

    async def send_message(
        self,
        *,
        chat_id: int | str,
        text: str,
        reply_markup: dict[str, Any] | None = None,
        parse_mode: str | None = "HTML",
        reply_to_message_id: int | None = None,
    ) -> object: ...

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
    ) -> None: ...

    async def set_chat_menu_button(
        self,
        *,
        web_app_url: str,
        button_text: str = "📱 Открыть",
        chat_id: int | None = None,
    ) -> None: ...


__all__ = ["TelegramUpdateClient"]
