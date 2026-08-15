# -*- coding: utf-8 -*-
"""Центральный диспетчер Telegram update → доменный handler.

Принимает durable webhook update, парсит команду / callback_query и делегирует
только синхронным либо транзакционно-долговечным обработчикам.
Telegram AI execution is intentionally disabled: durable inbox rows are never
marked processed before detached work finishes. Only opaque action callbacks
are recognized; raw target/task identifiers have no route.
"""

from __future__ import annotations

import logging
import shlex
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.safe_diagnostics import safe_exception_diagnostic
from core.telegram import format as fmt
from core.telegram.action_tokens import is_claimed_action_recovery
from core.telegram.handlers._send import send_text
from core.telegram.handlers.alerts import handle_action_callback
from core.telegram.handlers.onboarding import handle_help, handle_start
from core.telegram.handlers.protocol import TelegramUpdateClient
from core.telegram.service import find_recipient

logger = logging.getLogger(__name__)


async def _dispatch_callback_query(
    *,
    engine: AsyncEngine,
    client: TelegramUpdateClient,
    cq: dict[str, Any],
    bot_generation: int,
) -> None:
    """Handle a recipient-bound opaque callback from a durable incident card."""
    cq_id = str(cq.get("id", ""))
    data = str(cq.get("data") or "")
    from_user = cq.get("from") or {}
    user_id = int(from_user.get("id", 0))
    username = from_user.get("username") or str(user_id)
    chat_data = (cq.get("message") or {}).get("chat") or {}
    chat_id = int(chat_data.get("id", 0))
    if chat_data.get("type") != "private":
        try:
            await client.answer_callback_query(cq_id, text="Действие доступно только в личке")
        except Exception:
            pass
        return

    parts = data.split(":", 2)
    if len(parts) < 2:
        try:
            await client.answer_callback_query(cq_id, text="Некорректный формат")
        except Exception:
            pass
        return

    action = parts[0]

    internal_token_id: uuid.UUID | None = None
    if action == "a":
        internal_value = cq.get("_fb_action_token_id")
        if internal_value is not None:
            try:
                internal_token_id = uuid.UUID(str(internal_value))
            except ValueError:
                internal_token_id = None

    # Access control: только активный recipient может жать кнопки
    recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
    claimed_recovery = False
    if recipient is None and action == "a" and internal_token_id is not None:
        claimed_recovery = await is_claimed_action_recovery(
            engine,
            token_id=internal_token_id,
            chat_id=chat_id,
            telegram_user_id=user_id,
            claim_key=cq_id,
        )
    if recipient is None and not claimed_recovery:
        try:
            await client.answer_callback_query(cq_id, text="Доступа нет")
        except Exception:
            pass
        return

    if action == "a":
        await handle_action_callback(
            engine=engine,
            client=client,
            cq_id=cq_id,
            raw_token=None if internal_token_id is not None else parts[1],
            token_id=internal_token_id,
            chat_id=chat_id,
            telegram_user_id=user_id,
            username=str(username),
            bot_generation=bot_generation,
        )
        return

    try:
        await client.answer_callback_query(cq_id, text="Неизвестная команда")
    except Exception:
        pass


async def handle_update(
    *,
    engine: AsyncEngine,
    client: TelegramUpdateClient,
    update: dict[str, Any],
    bot_generation: int,
) -> None:
    """Обработка одного update от Telegram."""
    # Inline-кнопки под алертами
    if "callback_query" in update:
        await _dispatch_callback_query(
            engine=engine,
            client=client,
            cq=update["callback_query"],
            bot_generation=bot_generation,
        )
        return

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return  # игнорируем edited / inline_query / etc.

    chat = msg.get("chat") or {}
    chat_id = int(chat.get("id", 0))
    chat_type = chat.get("type")
    message_id = int(msg.get("message_id", 0))
    if chat_type != "private":
        return

    user = msg.get("from") or {}
    user_id = int(user.get("id", 0))
    username = user.get("username")
    first_name = user.get("first_name") or ""
    last_name = user.get("last_name") or ""
    display_name = f"{first_name} {last_name}".strip() or None

    text_raw = msg.get("text") or ""
    if not text_raw:
        return

    # Свободный текст в owner DM получает синхронный детерминированный ответ.
    # Detached AI work is forbidden here: otherwise the durable inbox would be
    # committed before the actual response/task completed.
    if not text_raw.startswith("/"):
        try:
            recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "free-text DM: recipient lookup failed (%s)",
                safe_exception_diagnostic(exc),
            )
            return
        if not recipient or not recipient.is_owner():
            return
        await send_text(
            client,
            chat_id=chat_id,
            text="AI-ассистент доступен в веб-интерфейсе. Telegram оставлен для коротких incident-карточек и действий.",
            reply_to_message_id=message_id,
        )
        return

    # Парсим команду + аргументы
    parts = text_raw.split(maxsplit=1)
    cmd = parts[0].lower().lstrip("/")
    # Убираем @botname суффикс (например /help@my_bot)
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
            message_id=message_id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            args=args,
        )
        return

    # Authorization check
    recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
    # Безусловный ACL-гейт: любой незарегистрированный — отказ, независимо от типа чата.
    if not recipient:
        await send_text(
            client,
            chat_id=chat_id,
            text=f"Доступа нет. Используй {fmt.code('/start <код>')} для подключения.",
            reply_to_message_id=message_id,
        )
        return

    if cmd == "help":
        await handle_help(
            client=client,
            chat_id=chat_id,
            message_id=message_id,
        )
        return

    # Unknown command
    await send_text(
        client,
        chat_id=chat_id,
        text=f"Неизвестная команда {fmt.code('/' + cmd)}. /help — список доступных.",
        reply_to_message_id=message_id,
    )


__all__ = ["handle_update"]
