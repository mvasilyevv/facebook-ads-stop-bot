# -*- coding: utf-8 -*-
"""/spy <slot> <country> — запуск Ad Library pipeline.

Сразу отвечает «Сканирую…», pipeline идёт в background task — main loop
poller'а остаётся отзывчивым.

MID-9 (аудит 02.07): /spy навигирует ЖИВОЙ Vision-браузер (не изолированный
сканер) — без лимита любой recipient мог задудосить его частыми вызовами
(гонка вкладок/страниц, потенциальный конфликт со сканом observer'а). Два
независимых предохранителя:
  - per-user Redis-cooldown (SET NX EX) — один и тот же chat_id не может
    запускать /spy чаще, чем раз в SPY_COOLDOWN_SECONDS;
  - глобальный asyncio.Semaphore(1) — на исполнение (не на приём команды):
    если pipeline уже крутится для кого-то другого, новый запрос получает
    вежливый отказ вместо второго параллельного захода в браузер.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.ext.asyncio import AsyncEngine

from core.ad_library.pipeline import run_pipeline
from core.ad_library.spy_handler import format_short_summary, parse_spy_args
from core.telegram import format as fmt
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text

logger = logging.getLogger(__name__)

# Cooldown на одного пользователя между запусками /spy.
SPY_COOLDOWN_SECONDS = int(os.environ.get("SPY_COOLDOWN_SECONDS", "120"))
_SPY_COOLDOWN_KEY_PREFIX = "spy:cooldown:"

# Глобальный лимит одновременных pipeline-исполнений: живой Vision-браузер один,
# второй параллельный /spy будет драться за ту же вкладку/сессию.
_SPY_EXECUTION_SEMAPHORE = asyncio.Semaphore(1)


async def _get_redis_client():
    """Ленивый module-level redis-клиент для cooldown (SET NX EX).

    Отдельный от RedisPubSub (тот заточен под publish/subscribe, не под
    произвольные команды). Недоступность Redis не должна ронять /spy —
    при ошибке подключения rate-limit просто пропускается (best-effort).
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as redis_asyncio

        from core.config import get_settings

        _redis_client = redis_asyncio.from_url(get_settings().redis_url, decode_responses=True)
        return _redis_client
    except Exception:  # noqa: BLE001
        logger.exception("spy: не удалось создать redis-клиент для rate-limit")
        return None


_redis_client = None


async def _check_and_set_cooldown(user_id: int) -> bool:
    """True, если пользователю МОЖНО запускать /spy сейчас (и cooldown уже поставлен).

    Redis недоступен → пропускаем лимит (лучше выполнить команду, чем
    заблокировать пользователя из-за инфраструктурного сбоя).
    """
    client = await _get_redis_client()
    if client is None:
        return True
    key = f"{_SPY_COOLDOWN_KEY_PREFIX}{user_id}"
    try:
        ok = await client.set(key, "1", ex=SPY_COOLDOWN_SECONDS, nx=True)
        return bool(ok)
    except Exception:  # noqa: BLE001
        logger.exception("spy: ошибка проверки cooldown в Redis")
        return True


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
    """Запускается в отдельной Task. По готовности — отправляет финальный markdown.

    Захват семафора неблокирующий (try-acquire): если pipeline уже крутится для
    кого-то другого — вежливый отказ, а не молчаливая постановка в очередь на
    захват общего живого Vision-браузера (иначе пользователь ждёт непонятно сколько
    без обратной связи).
    """
    if _SPY_EXECUTION_SEMAPHORE.locked():
        logger.info("spy: pipeline уже выполняется — отказ для chat_id=%s", chat_id)
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                "⏳ Сейчас уже выполняется другое сканирование Ad Library. "
                "Подожди, пока оно закончится, и попробуй снова."
            ),
            message_thread_id=thread_id,
        )
        return

    async with _SPY_EXECUTION_SEMAPHORE:
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
                text=(
                    f"❌ Сканирование {fmt.code(slot)} / {fmt.code(country)} "
                    f"упало: {fmt.code(str(exc))}"
                ),
                message_thread_id=thread_id,
            )
            return

    # summary/отчёт генерируются в Markdown — шлём их явно как Markdown
    # (жирный — одинарные *, не ** иначе видны буквально).
    summary = format_short_summary(pipeline_result).replace("**", "*")
    await send_text(
        client,
        chat_id=chat_id,
        text=summary,
        message_thread_id=thread_id,
        parse_mode="Markdown",
    )

    # Полный markdown-отчёт — отдельным сообщением (если есть)
    md = (pipeline_result.report or {}).get("markdown_report")
    if md and len(md) > 0:
        # TG limit 4096 — обрезаем если нужно
        if len(md) > 3800:
            md = md[:3800] + "\n\n_(отчёт обрезан, полный — в БД)_"
        # TG legacy Markdown: ** → * (БД-версия markdown_report остаётся стандартной).
        await send_text(
            client,
            chat_id=chat_id,
            text=md.replace("**", "*"),
            message_thread_id=thread_id,
            parse_mode="Markdown",
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
                f"⚠️ {fmt.esc(parsed)}\n\n"
                f"Использование: {fmt.code('/spy <слот> <ISO-2 country>')}\n"
                f"Пример: {fmt.code('/spy chicken road 2 KE')}"
            ),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # MID-9: per-user cooldown — ловим до отправки «Сканирую…», чтобы не плодить
    # мусорных прогресс-сообщений на каждый повторный вызов в пределах окна.
    allowed = await _check_and_set_cooldown(user_id)
    if not allowed:
        await send_text(
            client,
            chat_id=chat_id,
            text=(
                f"⏳ Слишком часто. Подожди {SPY_COOLDOWN_SECONDS} сек между вызовами "
                f"{fmt.code('/spy')} и попробуй снова."
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
            f"🔍 Сканирую Ad Library: {fmt.code(parsed_slot)} / {fmt.code(parsed_country)}…\n"
            "Это займёт 60–180 сек, дождись финального отчёта."
        ),
        message_thread_id=thread_id,
        parse_mode="HTML",
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
