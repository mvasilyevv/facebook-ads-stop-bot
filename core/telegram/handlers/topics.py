# -*- coding: utf-8 -*-
"""TG-команды управления топиками супергруппы: /setup_topics, /topics.

/setup_topics — создать статические топики (стопы/предупреждения/включения/
                операции/дайджест) и сохранить их thread_id в конфиг. Идемпотентно.
/topics       — показать текущую раскладку.

Создание топиков требует, чтобы бот был админом супергруппы с правом
can_manage_topics, а у группы были включены «Темы» (Topics).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram import format as fmt
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text
from core.telegram.service import load_telegram_config
from core.telegram.topics import STATIC_TOPIC_SPECS, PgTopicStore, provision_static_topics

logger = logging.getLogger(__name__)

_STATUS_LABEL = {
    "created": "создан",
    "existing": "уже был",
    "error": "ошибка",
}


async def handle_setup_topics(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    args_text: str,
) -> None:
    """/setup_topics — создать статические топики и сохранить их thread_id."""
    _ = args_text  # аргументы не используются — оставлено для совместимости сигнатуры

    cfg = await load_telegram_config(engine)
    if cfg is None or cfg.chat_id is None:
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                "⚠️ Не задан chat_id супергруппы в настройках Telegram. "
                "Сначала укажи его, потом запусти /setup_topics."
            ),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    store = PgTopicStore(engine)
    report = await provision_static_topics(store, client, chat_id=cfg.chat_id)

    lines = [f"🧩 {fmt.b('Настройка топиков')}", ""]
    any_error = False
    for spec in STATIC_TOPIC_SPECS:
        item = report.get(spec.key, {})
        status = str(item.get("status", "error"))
        label = _STATUS_LABEL.get(status, status)
        if status == "error":
            any_error = True
            err = fmt.esc(str(item.get("error", "")))[:80]
            lines.append(f"{fmt.esc(spec.name)} — {label}: {err}")
        else:
            lines.append(f"{fmt.esc(spec.name)} — {label} (#{item.get('thread_id')})")

    lines.append("")
    if any_error:
        lines.append(
            "⚠️ Часть топиков не создалась. Проверь: бот — админ супергруппы "
            "с правом «Управление темами», и в группе включены «Темы»."
        )
    else:
        lines.append("✅ Готово. Маршрутизация алертов по топикам активна.")

    await send_text(
        client,
        chat_id=chat_id,
        text="\n".join(lines),
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


async def handle_topics(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
) -> None:
    """/topics — показать текущую раскладку топиков."""
    cfg = await load_telegram_config(engine)
    if cfg is None:
        await send_text(
            client,
            chat_id=chat_id,
            text="Telegram не настроен.",
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    thread_by_column = {
        "forum_stop_thread_id": cfg.forum_stop_thread_id,
        "forum_warning_thread_id": cfg.forum_warning_thread_id,
        "forum_enable_thread_id": cfg.forum_enable_thread_id,
        "forum_ops_thread_id": cfg.forum_ops_thread_id,
        "forum_digest_thread_id": getattr(cfg, "forum_digest_thread_id", None),
    }

    lines = [f"🧩 {fmt.b('Топики супергруппы')}", ""]
    for spec in STATIC_TOPIC_SPECS:
        tid = thread_by_column.get(spec.config_column)
        value = f"#{tid}" if tid is not None else "— не задан"
        lines.append(f"{fmt.esc(spec.name)}: {value}")

    if not any(thread_by_column.values()):
        lines.append("")
        lines.append(f"Топики ещё не созданы — запусти {fmt.code('/setup_topics')}.")

    await send_text(
        client,
        chat_id=chat_id,
        text="\n".join(lines),
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


__all__ = ["handle_setup_topics", "handle_topics"]
