# -*- coding: utf-8 -*-
"""Onboarding-команды: /start [code], /help.

/start не требует авторизации (это вход).
/help — короткий список доступных команд.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram import format as fmt
from core.telegram.handlers._send import send_text
from core.telegram.handlers.protocol import TelegramUpdateClient
from core.telegram.menu_button import sync_menu_buttons
from core.telegram.service import consume_invite_and_create_recipient

logger = logging.getLogger(__name__)


async def handle_start(
    *,
    engine: AsyncEngine,
    client: TelegramUpdateClient,
    chat_id: int,
    message_id: int,
    user_id: int,
    username: str | None,
    display_name: str | None,
    args: list[str],
) -> None:
    """/start [code] — private-DM invite onboarding."""
    code = args[0].strip() if args else ""

    if not code:
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                "Привет! Для подключения нужен код-приглашение.\n\n"
                f"Спроси у владельца бота и пришли так: {fmt.code('/start <код>')}."
            ),
            reply_to_message_id=message_id,
        )
        return

    recipient = await consume_invite_and_create_recipient(
        engine,
        code=code,
        chat_id=chat_id,
        telegram_user_id=user_id,
        username=username,
        display_name=display_name,
    )
    if recipient is None:
        await send_text(
            client,
            chat_id=chat_id,
            text="Этот код не подходит — либо устарел, либо уже использован.",
            reply_to_message_id=message_id,
        )
        return
    await sync_menu_buttons(
        engine,
        client,
        chat_ids=[chat_id],
        include_default=False,
    )
    await send_text(
        client,
        chat_id=chat_id,
        text=(
            f"Подключено как @{fmt.esc(username or display_name or user_id)}. "
            "Доступные команды: /help."
        ),
        reply_to_message_id=message_id,
    )


async def handle_help(
    *,
    client: TelegramUpdateClient,
    chat_id: int,
    message_id: int,
) -> None:
    """/help — список команд (HTML, стиль «чистая карточка»)."""
    txt = "\n".join(
        [
            "<b>📖 Telegram</b>",
            "Бот присылает короткие incident-карточки и показывает статус действий.",
            "Мониторинг, сканирование, аналитика и настройки доступны в веб-интерфейсе.",
            f"{fmt.code('/help')} — эта справка.",
        ]
    )
    await send_text(
        client,
        chat_id=chat_id,
        text=txt,
        reply_to_message_id=message_id,
    )


__all__ = ["handle_help", "handle_start"]
