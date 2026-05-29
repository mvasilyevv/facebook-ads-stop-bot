# -*- coding: utf-8 -*-
"""TG-команда /autostart — управление автостартом кабинета по расписанию.

Формы:
- /autostart                       — показать текущий конфиг.
- /autostart on | off              — включить / выключить фичу.
- /autostart HH:MM 22.05,25.05     — задать время (UTC) + список дат кампаний.

Money-критично: при включённом автостарте воркер cabinet_scheduler в заданное
время сам включит объявления СВОИХ кампаний с указанной датой и запустит scan.
Owner-scoping берётся из observer_config (тот же тег, что у /pause /resume).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncEngine

from core.scheduler.cabinet_autostart import read_autostart_config, write_autostart_config
from core.telegram.client import TelegramBotClient
from core.telegram.handlers._send import send_text

logger = logging.getLogger(__name__)

# HH:MM (00:00..23:59).
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]?\d)$")


def _format_config(config: dict) -> str:
    """Человекочитаемое представление текущего конфига для TG."""
    state = "🟢 включён" if config.get("enabled") else "🔴 выключен"
    hour = int(config.get("hour_utc", 6))
    minute = int(config.get("minute_utc", 0))
    dates = config.get("dates") or []
    dates_str = ", ".join(dates) if dates else "— (не заданы)"
    return (
        f"*Автостарт кабинета*: {state}\n"
        f"Время: `{hour:02d}:{minute:02d}` UTC (ежедневно)\n"
        f"Даты кампаний: {dates_str}\n\n"
        "Что делает: в указанное время включает объявления твоих кампаний с "
        "этими датами в названии и запускает скан."
    )


_USAGE = (
    "*Использование /autostart:*\n"
    "`/autostart` — показать настройки.\n"
    "`/autostart on` — включить.\n"
    "`/autostart off` — выключить.\n"
    "`/autostart HH:MM 22.05,25.05` — время (UTC) + даты кампаний.\n\n"
    "Пример: `/autostart 06:00 22.05` — каждый день в 06:00 UTC включать "
    "объявления кампаний с «22.05» в названии."
)


def _parse_dates(raw: str) -> list[str]:
    """Разбирает CSV дат: "22.05,25.05" → ["22.05", "25.05"]."""
    return [d.strip() for d in raw.replace(";", ",").split(",") if d.strip()]


async def handle_autostart(
    *,
    engine: AsyncEngine,
    client: TelegramBotClient,
    chat_id: int,
    message_id: int,
    thread_id: int | None,
    args_text: str,
) -> None:
    """/autostart — показать/изменить конфиг автостарта кабинета."""
    args = (args_text or "").strip()

    # Без аргументов — показать текущий конфиг.
    if not args:
        config = await read_autostart_config(engine)
        await send_text(
            client,
            chat_id=chat_id,
            text=_format_config(config),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    parts = args.split()
    first = parts[0].lower()

    # on / off — переключить флаг, остальные поля не трогаем.
    if first in ("on", "off"):
        config = await read_autostart_config(engine)
        config["enabled"] = first == "on"
        await write_autostart_config(engine, config)
        await send_text(
            client,
            chat_id=chat_id,
            text="✅ Автостарт обновлён.\n\n" + _format_config(config),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # HH:MM [dates] — задать время + (опционально) даты.
    m = _TIME_RE.match(first)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        dates = _parse_dates(parts[1]) if len(parts) > 1 else []
        # Если даты не переданы — сохраняем прежние (не обнуляем случайно).
        config = await read_autostart_config(engine)
        config["hour_utc"] = hour
        config["minute_utc"] = minute
        if dates:
            config["dates"] = dates
        await write_autostart_config(engine, config)
        await send_text(
            client,
            chat_id=chat_id,
            text="✅ Время и даты обновлены.\n\n" + _format_config(config),
            reply_to_message_id=message_id,
            message_thread_id=thread_id,
        )
        return

    # Не распознали — подсказка.
    await send_text(
        client,
        chat_id=chat_id,
        text=_USAGE,
        reply_to_message_id=message_id,
        message_thread_id=thread_id,
    )


__all__ = ["handle_autostart"]
