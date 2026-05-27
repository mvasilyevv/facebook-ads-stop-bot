# -*- coding: utf-8 -*-
"""/spy <slot> <country> — запуск Ad Library pipeline.

Сразу отвечает «Сканирую…», pipeline идёт в background task — main loop
poller'а остаётся отзывчивым.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncEngine

from core.ad_library.pipeline import run_pipeline
from core.ad_library.spy_handler import format_short_summary, parse_spy_args
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text

logger = logging.getLogger(__name__)


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
        await send_text(
            client,
            chat_id=chat_id,
            text=f"❌ Сканирование `{slot}` / `{country}` упало: `{exc}`",
            message_thread_id=thread_id,
        )
        return

    summary = format_short_summary(pipeline_result)
    await send_text(
        client,
        chat_id=chat_id,
        text=summary,
        message_thread_id=thread_id,
    )

    # Полный markdown-отчёт — отдельным сообщением (если есть)
    md = (pipeline_result.report or {}).get("markdown_report")
    if md and len(md) > 0:
        # TG limit 4096 — обрезаем если нужно
        if len(md) > 3800:
            md = md[:3800] + "\n\n_(отчёт обрезан, полный — в БД)_"
        await send_text(
            client,
            chat_id=chat_id,
            text=md,
            message_thread_id=thread_id,
        )


async def handle_spy(
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
    """/spy <slot> <country> — запустить Ad Library pipeline."""
    parsed = parse_spy_args(args_text)
    if isinstance(parsed, str):
        # parse_spy_args вернул строку ошибки
        await send_text(
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

    # Pipeline в background — main loop poller'а должен оставаться отзывчивым
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


__all__ = ["handle_spy"]
