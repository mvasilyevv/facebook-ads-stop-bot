# -*- coding: utf-8 -*-
"""Onboarding-команды: /start [code], /help.

/start не требует авторизации (это вход).
/help — короткий список доступных команд.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram import format as fmt
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text
from core.telegram.service import (
    consume_invite_and_create_recipient,
    find_active_invite,
    find_recipient,
)

logger = logging.getLogger(__name__)


def _is_private(chat_type: str | None) -> bool:
    return (chat_type or "") == "private"


async def handle_start(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    chat_type: str | None,
    message_id: int,
    thread_id: int | None,
    user_id: int,
    username: str | None,
    display_name: str | None,
    args: list[str],
) -> None:
    """/start [code] — приветствие + consume invite."""
    code = args[0].strip() if args else ""

    if _is_private(chat_type):
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

        invite = await find_active_invite(engine, code)
        if not invite:
            await send_text(
                client,
                chat_id=chat_id,
                text="Этот код не подходит — либо устарел, либо уже использован.",
                reply_to_message_id=message_id,
            )
            return

        await consume_invite_and_create_recipient(
            engine,
            invite_id=invite["id"],
            chat_id=chat_id,
            telegram_user_id=user_id,
            username=username,
            display_name=display_name,
            role=invite.get("role", "recipient"),
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
        return

    # group chat — проверяем доступ
    recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
    if recipient:
        await send_text(
            client,
            chat_id=chat_id,
            text="Ты уже подключён. /help — список команд.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
    else:
        await send_text(
            client,
            chat_id=chat_id,
            text="Доступа нет. Получи invite в личке у владельца бота.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )


async def handle_help(
    *,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
) -> None:
    """/help — список команд (HTML, стиль «чистая карточка»)."""
    txt = "\n".join(
        [
            f"📖 {fmt.b('Команды бота')}",
            "",
            fmt.b("⏯ Массовые действия"),
            f"{fmt.code('/pause <offer>')} — пауза всех объявлений оффера.",
            f"{fmt.code('/resume <offer>')} — включение. Приходит черновик с ✅ / ❌.",
            f"Пример: {fmt.code('/pause GH_CR2')}",
            "",
            fmt.b("🗓 Кабинет"),
            f"{fmt.code('/autostart')} — автостарт по расписанию (вкл по дате + скан).",
            f"Пример: {fmt.code('/autostart 06:00 22.05')} — ежедневно в 06:00 UTC.",
            "",
            fmt.b("🤖 AI-ассистент (владелец)"),
            f"{fmt.code('/ai <вопрос>')} — статусы, аналитика, черновики действий.",
            f"В личке можно писать без команды. {fmt.code('/ai reset')} — сброс контекста.",
            "",
            fmt.b("🔍 Разведка"),
            f"{fmt.code('/spy <слот> <country>')} — конкуренты в Ad Library.",
            f"Пример: {fmt.code('/spy chicken road 2 KE')}",
            "",
            fmt.b("🎬 Планы кампаний"),
            f"{fmt.code('/record_plan <название>')} — начать запись (боевой браузер).",
            f"{fmt.code('/stop_record')} — остановить и сохранить.",
            f"{fmt.code('/plans')} — список с кнопкой «Запустить».",
            "",
            f"{fmt.code('/help')} — эта справка.",
        ]
    )
    await send_text(
        client,
        chat_id=chat_id,
        text=txt,
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


__all__ = ["handle_help", "handle_start"]
