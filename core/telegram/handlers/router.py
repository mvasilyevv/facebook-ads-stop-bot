# -*- coding: utf-8 -*-
"""Центральный диспетчер Telegram update → доменный handler.

Принимает `update` от long-polling, парсит команду / callback_query, делегирует
обработку модулям `onboarding.py`, `spy.py`, `ask.py`, `alerts.py`. `meta_api_client`
опционален: пробрасывается в `/ask` для работы Marketing API tools.
"""

from __future__ import annotations

import logging
import shlex
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text
from core.telegram.handlers.alerts import (
    handle_dis_callback,
    handle_enable_reco_callback,
    handle_snz_callback,
)
from core.telegram.handlers.ask import handle_ask, handle_draft_callback
from core.telegram.handlers.onboarding import handle_help, handle_start
from core.telegram.handlers.spy import handle_spy
from core.telegram.service import find_recipient

if TYPE_CHECKING:  # pragma: no cover
    from core.meta_api.client import MetaApiClient

logger = logging.getLogger(__name__)


_LEGACY_COMMANDS: frozenset[str] = frozenset(
    {
        "ads",
        "offers",
        "rules",
        "scripts",
        "status",
        "digest",
        "set",
        "app",
        "disabled",
        "settings",
    }
)


def _is_private(chat_type: str | None) -> bool:
    return (chat_type or "") == "private"


async def _dispatch_callback_query(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq: dict[str, Any],
) -> None:
    """Обработка нажатия inline-кнопки (под алертами или AI draft).

    callback_data: '<action>:<arg1>[:<arg2>]'
        action ∈ {'dis', 'snz', 'dr_ok', 'dr_cancel'}.
    """
    cq_id = str(cq.get("id", ""))
    data = str(cq.get("data") or "")
    from_user = cq.get("from") or {}
    user_id = int(from_user.get("id", 0))
    username = from_user.get("username") or str(user_id)
    chat_data = (cq.get("message") or {}).get("chat") or {}
    chat_id = int(chat_data.get("id", 0))

    parts = data.split(":", 2)
    if len(parts) < 2:
        try:
            await client.answer_callback_query(cq_id, text="Некорректный формат")
        except Exception:
            pass
        return

    action = parts[0]

    # Access control: только активный recipient может жать кнопки
    recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
    if not recipient:
        try:
            await client.answer_callback_query(cq_id, text="Доступа нет")
        except Exception:
            pass
        return

    # AI draft callbacks
    if action in ("dr_ok", "dr_cancel"):
        message_id = (cq.get("message") or {}).get("message_id")
        await handle_draft_callback(
            engine=engine,
            client=client,
            cq_id=cq_id,
            action=action,
            task_id_raw=parts[1],
            username=str(username),
            chat_id=chat_id,
            message_id=int(message_id) if message_id else None,
        )
        return

    fb_ad_id = parts[1]
    token = parts[2] if len(parts) >= 3 else ""

    if action == "dis":
        await handle_dis_callback(
            engine=engine,
            client=client,
            cq_id=cq_id,
            fb_ad_id=fb_ad_id,
            token=token,
            username=str(username),
        )
        return

    if action == "snz":
        await handle_snz_callback(
            engine=engine,
            client=client,
            cq_id=cq_id,
            fb_ad_id=fb_ad_id,
        )
        return

    if action == "ereco":
        await handle_enable_reco_callback(
            engine=engine,
            client=client,
            cq_id=cq_id,
            fb_ad_id=fb_ad_id,
            username=str(username),
        )
        return

    try:
        await client.answer_callback_query(cq_id, text="Неизвестная команда")
    except Exception:
        pass


async def handle_update(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    update: dict[str, Any],
    meta_api_client: MetaApiClient | None = None,
) -> None:
    """Обработка одного update от Telegram."""
    # Inline-кнопки под алертами
    if "callback_query" in update:
        await _dispatch_callback_query(engine=engine, client=client, cq=update["callback_query"])
        return

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return  # игнорируем edited / inline_query / etc.

    chat = msg.get("chat") or {}
    chat_id = int(chat.get("id", 0))
    chat_type = chat.get("type")
    message_id = int(msg.get("message_id", 0))
    thread_id = msg.get("message_thread_id")

    user = msg.get("from") or {}
    user_id = int(user.get("id", 0))
    username = user.get("username")
    first_name = user.get("first_name") or ""
    last_name = user.get("last_name") or ""
    display_name = f"{first_name} {last_name}".strip() or None

    text_raw = msg.get("text") or ""
    if not text_raw or not text_raw.startswith("/"):
        return

    # Парсим команду + аргументы
    parts = text_raw.split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/")
    # Убираем @botname суффикс (например /spy@my_bot)
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    args_text = parts[1] if len(parts) > 1 else ""

    # /start не требует авторизации, остальное — да
    if cmd == "start":
        try:
            args = shlex.split(args_text)
        except ValueError:
            args = args_text.split()
        await handle_start(
            engine=engine,
            client=client,
            chat_id=chat_id,
            chat_type=chat_type,
            message_id=message_id,
            thread_id=thread_id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            args=args,
        )
        return

    # Authorization check
    recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
    if not recipient and _is_private(chat_type):
        await send_text(
            client,
            chat_id=chat_id,
            text="Доступа нет. Используй `/start <код>` для подключения.",
            reply_to_message_id=message_id,
        )
        return

    if cmd == "help":
        await handle_help(
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
        )
        return

    if cmd == "spy":
        await handle_spy(
            engine=engine,
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            user_id=user_id,
            username=username,
            args_text=args_text,
        )
        return

    if cmd == "ask":
        await handle_ask(
            engine=engine,
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            user_id=user_id,
            username=username,
            args_text=args_text,
            meta_api_client=meta_api_client,
        )
        return

    # Legacy команды — заглушка
    if cmd in _LEGACY_COMMANDS:
        await send_text(
            client,
            chat_id=chat_id,
            text=f"`/{cmd}` в процессе миграции под новую схему БД. Пока доступны: /spy, /help.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # Unknown command
    await send_text(
        client,
        chat_id=chat_id,
        text=f"Неизвестная команда `/{cmd}`. /help — список доступных.",
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


__all__ = ["handle_update"]
