# -*- coding: utf-8 -*-
"""Центральный диспетчер Telegram update → доменный handler.

Принимает `update` от long-polling, парсит команду / callback_query, делегирует
обработку модулям `onboarding.py`, `spy.py`, `bulk.py`, `alerts.py`, `creator.py`.
`redis` опционален: пробрасывается в creator-команды для pubsub publish.
`redis_client` (data-Redis) + `meta_api_client` — зависимости AI-ассистента
(/ai + свободный текст в личке owner'а, см. ai_chat.py). Draft-кнопки
dr_ok/dr_cancel обслуживают и ручные /pause /resume, и черновики ассистента.
"""

from __future__ import annotations

import logging
import shlex
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram import format as fmt
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text
from core.telegram.handlers.ai_chat import spawn_ai_chat
from core.telegram.handlers.alerts import (
    handle_dis_callback,
    handle_enable_reco_callback,
)
from core.telegram.handlers.autostart import handle_autostart
from core.telegram.handlers.bulk import handle_bulk_toggle
from core.telegram.handlers.creator import (
    handle_list_plans,
    handle_plan_run_callback,
    handle_record_plan,
    handle_stop_record,
)
from core.telegram.handlers.draft_confirm import handle_draft_callback
from core.telegram.handlers.onboarding import handle_help, handle_start
from core.telegram.handlers.spy import handle_spy
from core.telegram.service import find_recipient

if TYPE_CHECKING:  # pragma: no cover
    from core.pubsub import RedisPubSub

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

# Owner-ACL: money-действия, доступные только role='owner'.
# Callback-кнопки под алертами/планами (трогают кабинет или боевой браузер).
# dr_ok (подтверждение money-черновика /pause /resume) — тоже owner-only (H-2):
# не-owner может СОЗДАТЬ черновик, но ИСПОЛНИТЬ его (approve → pending →
# meta_api_worker тратит деньги) вправе только владелец кабинета.
# dr_cancel НЕ здесь — отмена черновика безопасна (снимает pending-действие).
_OWNER_ONLY_CALLBACKS: frozenset[str] = frozenset({"dis", "ereco", "plan", "dr_ok"})
# Команды (autostart с аргументами проверяется отдельно — write-путь).
# /ai — owner-only: ассистент умеет создавать money-черновики (request_*).
_OWNER_ONLY_COMMANDS: frozenset[str] = frozenset(
    {"pause", "resume", "record_plan", "stop_record", "ai"}
)


async def _dispatch_callback_query(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    cq: dict[str, Any],
    redis_client: Any | None = None,
) -> None:
    """Обработка нажатия inline-кнопки (под алертами или draft-превью /pause).

    callback_data: '<action>:<arg1>[:<arg2>]'
        action ∈ {'dis', 'ereco', 'dr_ok', 'dr_cancel', 'plan'}.
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

    # Owner-ACL: money-кнопки (отключение/включение/запуск плана, подтверждение
    # money-черновика dr_ok) доступны только role='owner'. dr_cancel
    # (отмена черновика) — не money, доступна любому активному recipient.
    if action in _OWNER_ONLY_CALLBACKS and not recipient.is_owner():
        logger.warning(
            "ACL отказ (callback): action=%s chat_id=%s role=%s", action, chat_id, recipient.role
        )
        try:
            await client.answer_callback_query(cq_id, text="⛔ Только владелец кабинета")
        except Exception:
            pass
        return

    # Draft-подтверждение (под /pause /resume превью)
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

    # Creator plan run callback
    if action == "plan":
        await handle_plan_run_callback(
            callback_query=cq,
            engine=engine,
            client=client,
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

    # action == "snz" (snooze) убран (решение владельца): старые snz-кнопки под уже
    # отправленными алертами просто проигнорируются (no-op), не падают.

    if action == "ereco":
        await handle_enable_reco_callback(
            engine=engine,
            client=client,
            cq_id=cq_id,
            fb_ad_id=fb_ad_id,
            username=str(username),
            redis_client=redis_client,
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
    redis: RedisPubSub | None = None,
    redis_client: Any | None = None,
    meta_api_client: Any | None = None,
) -> None:
    """Обработка одного update от Telegram."""
    # Inline-кнопки под алертами
    if "callback_query" in update:
        await _dispatch_callback_query(
            engine=engine, client=client, cq=update["callback_query"], redis_client=redis_client
        )
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
    if not text_raw:
        return

    # Свободный текст (не /-команда): в личке owner'а — вопрос AI-ассистенту,
    # везде остальное — молчаливый игнор (как раньше). Ошибка проверки доступа
    # тоже молчание: свободный текст не должен ронять поллер/спамить отказами.
    if not text_raw.startswith("/"):
        if chat_type != "private":
            return
        try:
            recipient = await find_recipient(engine, chat_id=chat_id, telegram_user_id=user_id)
        except Exception:  # noqa: BLE001
            logger.exception("free-text DM: find_recipient упал — игнорирую сообщение")
            return
        if not recipient or not recipient.is_owner():
            return
        # Фоновым таском (H-1): AI-цикл длится до минут, поллер не должен ставить
        # money-кнопки следующих updates в очередь за ответом ассистента.
        spawn_ai_chat(
            engine=engine,
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            username=username,
            args_text=text_raw,
            redis_client=redis_client,
            meta_api_client=meta_api_client,
        )
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
    # Безусловный ACL-гейт: любой незарегистрированный — отказ, независимо от типа чата.
    if not recipient:
        await send_text(
            client,
            chat_id=chat_id,
            text=f"Доступа нет. Используй {fmt.code('/start <код>')} для подключения.",
            reply_to_message_id=message_id,
        )
        return

    # Owner-ACL: money-команды (трогают кабинет / боевой браузер) — только role='owner'.
    # autostart с аргументами = запись расписания (money); без аргументов = чтение (любому).
    needs_owner = cmd in _OWNER_ONLY_COMMANDS or (cmd == "autostart" and bool(args_text.strip()))
    if needs_owner and not recipient.is_owner():
        logger.warning(
            "ACL отказ (команда): cmd=%s chat_id=%s role=%s",
            cmd,
            chat_id,
            getattr(recipient, "role", None),
        )
        await send_text(
            client,
            chat_id=chat_id,
            text="⛔ Только владелец кабинета может выполнить это действие.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    if cmd == "ai":
        # Фоновым таском (H-1) — см. комментарий у free-text ветки выше.
        spawn_ai_chat(
            engine=engine,
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            username=username,
            args_text=args_text,
            redis_client=redis_client,
            meta_api_client=meta_api_client,
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

    if cmd in ("pause", "resume"):
        await handle_bulk_toggle(
            engine=engine,
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            username=username,
            command=cmd,
            args_text=args_text,
        )
        return

    if cmd == "autostart":
        await handle_autostart(
            engine=engine,
            client=client,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            args_text=args_text,
        )
        return

    if cmd == "record_plan":
        if redis is None:
            await send_text(
                client,
                chat_id=chat_id,
                text="❌ Redis недоступен — команда не работает.",
                reply_to_message_id=message_id,
                message_thread_id=thread_id,
            )
            return
        await handle_record_plan(
            engine=engine,
            client=client,
            redis=redis,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
            args_text=args_text,
        )
        return

    if cmd == "stop_record":
        if redis is None:
            await send_text(
                client,
                chat_id=chat_id,
                text="❌ Redis недоступен — команда не работает.",
                reply_to_message_id=message_id,
                message_thread_id=thread_id,
            )
            return
        await handle_stop_record(
            engine=engine,
            client=client,
            redis=redis,
            chat_id=chat_id,
            message_id=message_id,
            thread_id=thread_id,
        )
        return

    if cmd == "plans":
        await handle_list_plans(
            engine=engine,
            client=client,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        return

    # Legacy команды — заглушка
    if cmd in _LEGACY_COMMANDS:
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                f"{fmt.code('/' + cmd)} в процессе миграции под новую схему БД. "
                "Пока доступны: /spy, /help."
            ),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # Unknown command
    await send_text(
        client,
        chat_id=chat_id,
        text=f"Неизвестная команда {fmt.code('/' + cmd)}. /help — список доступных.",
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


__all__ = ["handle_update"]
