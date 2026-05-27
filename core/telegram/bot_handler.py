# -*- coding: utf-8 -*-
"""Минимальный bot handler под v2 схему.

Поддерживает только:
- /start [code]   — приветствие + consume invite
- /help           — список доступных команд
- /spy <slot> <country>  — запуск Ad Library scan + ответ markdown'ом

Любая другая команда → ответ "Команда в процессе миграции под новую схему".

Acceс контроль:
- private chat: только активные recipient'ы.
- группа: все участники могут писать /spy (privacy mode не блокирует).

NB: TG /spy запускает run_pipeline в asyncio.Task — long-running операция (60-180 сек).
Бот сразу отвечает «Сканирую…», по готовности — финальное сообщение.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.ad_library.pipeline import run_pipeline
from core.ad_library.spy_handler import (
    format_short_summary,
    parse_spy_args,
)
from core.telegram.client import TelegramBotClient
from core.telegram.service import (
    consume_invite_and_create_recipient,
    find_active_invite,
    find_recipient,
)

logger = logging.getLogger(__name__)


# В личном чате chat.id == user.id для приватных. Для групп — отрицательный.
def _is_private(chat_type: str | None) -> bool:
    return (chat_type or "") == "private"


async def _send(
    client: TelegramBotClient,
    *,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None = None,  # принимается для совместимости, но не передаётся
    message_thread_id: int | None = None,
) -> None:
    """Тонкая обёртка над client.send_message с глушением сетевых ошибок.

    reply_to_message_id принимаем в сигнатуре для удобства call-сайтов (документирует
    «это реплай на X»), но клиент его не поддерживает — игнорируем.
    """
    _ = reply_to_message_id
    try:
        await client.send_message(
            chat_id=str(chat_id),
            text=text,
            message_thread_id=message_thread_id,
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("send_message failed")


# ====== /start ======


async def _handle_start(
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
    code = args[0].strip() if args else ""

    if _is_private(chat_type):
        if not code:
            await _send(
                client,
                chat_id=chat_id,
                text=(
                    "Привет! Для подключения нужен код-приглашение.\n\n"
                    "Спроси у владельца бота и пришли так: `/start <код>`."
                ),
                reply_to_message_id=message_id,
            )
            return

        invite = await find_active_invite(engine, code)
        if not invite:
            await _send(
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
            role="recipient",
        )
        await _send(
            client,
            chat_id=chat_id,
            text=(
                f"Подключено как @{username or display_name or user_id}. Доступные команды: /help."
            ),
            reply_to_message_id=message_id,
        )
        return

    # group chat
    recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
    if recipient:
        await _send(
            client,
            chat_id=chat_id,
            text="Ты уже подключён. /help — список команд.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
    else:
        await _send(
            client,
            chat_id=chat_id,
            text="Доступа нет. Получи invite в личке у владельца бота.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )


# ====== /help ======


async def _handle_help(
    *,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
) -> None:
    txt = (
        "*Доступные команды:*\n\n"
        "/spy `<слот> <country>` — поиск конкурентов в Ad Library\n"
        "  Пример: `/spy chicken road 2 KE`\n\n"
        "/help — эта справка\n\n"
        "_Остальные команды (/ads, /offers, /rules, /scripts, /status, /digest, /ask) "
        "в процессе миграции под новую схему БД._"
    )
    await _send(
        client,
        chat_id=chat_id,
        text=txt,
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


# ====== /spy ======


async def _run_spy_pipeline_background(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    progress_message_id: int,
    thread_id: int | None,
    slot: str,
    country: str,
    triggered_by: str,
) -> None:
    """Запускается в отдельной Task. По готовности — отправляет финальный markdown."""
    try:
        pipeline_result = await run_pipeline(
            engine,
            slot=slot,
            country=country,
            triggered_by=triggered_by,
        )
    except Exception as exc:
        logger.exception("Pipeline crashed")
        await _send(
            client,
            chat_id=chat_id,
            text=f"❌ Сканирование `{slot}` / `{country}` упало: `{exc}`",
            message_thread_id=thread_id,
        )
        return

    summary = format_short_summary(pipeline_result)
    await _send(
        client,
        chat_id=chat_id,
        text=summary,
        message_thread_id=thread_id,
    )

    # Если есть полный markdown отчёт — отправляем отдельным сообщением
    md = (pipeline_result.report or {}).get("markdown_report")
    if md and len(md) > 0:
        # TG limit 4096 — обрезаем если нужно
        if len(md) > 3800:
            md = md[:3800] + "\n\n_(отчёт обрезан, полный — в БД)_"
        await _send(
            client,
            chat_id=chat_id,
            text=md,
            message_thread_id=thread_id,
        )


async def _handle_spy(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    user_id: int,
    username: str | None,
    args_text: str,
) -> None:
    parsed = parse_spy_args(args_text)
    if isinstance(parsed, str):
        # parse_spy_args вернул строку ошибки
        await _send(
            client,
            chat_id=chat_id,
            text=(
                f"⚠️ {parsed}\n\n"
                "Использование: `/spy <слот> <ISO-2 country>`\n"
                "Пример: `/spy chicken road 2 KE`"
            ),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # parsed — SpyRequest
    triggered_by = f"tg:{username or user_id}"
    parsed_slot = parsed.slot
    parsed_country = parsed.country

    # Сразу отвечаем «Сканирую…»
    _ = message_id  # клиент не поддерживает reply_to — оставляем для документации
    progress = await client.send_message(
        chat_id=str(chat_id),
        text=(
            f"🔍 Сканирую Ad Library: `{parsed_slot}` / `{parsed_country}`…\n"
            "Это займёт 60–180 сек, дождись финального отчёта."
        ),
        message_thread_id=thread_id,
        parse_mode="Markdown",
    )
    progress_message_id = (progress or {}).get("message_id", 0) if isinstance(progress, dict) else 0

    # Запускаем pipeline в background — main loop poller'а должен оставаться отзывчивым
    asyncio.create_task(
        _run_spy_pipeline_background(
            engine=engine,
            client=client,
            chat_id=chat_id,
            progress_message_id=int(progress_message_id),
            thread_id=thread_id,
            slot=parsed_slot,
            country=parsed_country,
            triggered_by=triggered_by,
        )
    )


# ====== handle_update ======


async def _handle_callback_query(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq: dict[str, Any],
) -> None:
    """Обработка нажатия inline-кнопки под алертом.

    callback_data формат: '<action>:<fb_ad_id>:<token>' (см. renderer.render_inline_keyboard).
    action ∈ {'dis', 'snz', 'clm'}.
    """
    from datetime import datetime, timedelta, timezone

    from core.tasks import create_task

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
    fb_ad_id = parts[1]
    token = parts[2] if len(parts) >= 3 else ""

    # Access control: только активный recipient может жать кнопки
    recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
    if not recipient:
        try:
            await client.answer_callback_query(cq_id, text="Доступа нет")
        except Exception:
            pass
        return

    requested_by = f"tg:{username}"

    if action == "dis":
        idem_key = f"manual:disable:{fb_ad_id}:{token or 'no-token'}"
        try:
            task_id = await create_task(
                engine,
                task_type="disable",
                idempotency_key=idem_key,
                payload={
                    "fb_ad_id": fb_ad_id,
                    "open_state_token": token or None,
                },
                requested_by=requested_by,
            )
            ack = "Задача на отключение принята" if task_id else "Уже в очереди"
        except Exception:
            logger.exception("create_task disable failed")
            ack = "Ошибка"
        try:
            await client.answer_callback_query(cq_id, text=ack)
        except Exception:
            pass
        return

    if action == "snz":
        # snooze на 2 часа — UPDATE ad_alert_state.snoozed_until
        snooze_until = datetime.now(timezone.utc) + timedelta(hours=2)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    sa_text(
                        """
                        UPDATE ad_alert_state s
                        SET snoozed_until = :until, updated_at = NOW()
                        FROM fb_ads a
                        WHERE s.ad_id = a.id AND a.fb_ad_id = :fbid
                        """
                    ),
                    {"until": snooze_until, "fbid": fb_ad_id},
                )
            ack = "Снуз на 2 часа"
        except Exception:
            logger.exception("snooze failed")
            ack = "Ошибка"
        try:
            await client.answer_callback_query(cq_id, text=ack)
        except Exception:
            pass
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
) -> None:
    """Обработка одного update от Telegram."""
    # Inline-кнопки под алертами
    if "callback_query" in update:
        await _handle_callback_query(engine=engine, client=client, cq=update["callback_query"])
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
        await _handle_start(
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
        await _send(
            client,
            chat_id=chat_id,
            text="Доступа нет. Используй `/start <код>` для подключения.",
            reply_to_message_id=message_id,
        )
        return

    if cmd == "help":
        await _handle_help(
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
        )
        return

    if cmd == "spy":
        await _handle_spy(
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

    # Legacy команды — заглушка
    if cmd in {
        "ads",
        "offers",
        "rules",
        "scripts",
        "status",
        "digest",
        "ask",
        "set",
        "app",
        "disabled",
        "settings",
    }:
        await _send(
            client,
            chat_id=chat_id,
            text=f"`/{cmd}` в процессе миграции под новую схему БД. Пока доступны: /spy, /help.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # Unknown command
    await _send(
        client,
        chat_id=chat_id,
        text=f"Неизвестная команда `/{cmd}`. /help — список доступных.",
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


__all__ = ["handle_update"]
