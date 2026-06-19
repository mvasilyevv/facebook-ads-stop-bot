# -*- coding: utf-8 -*-
"""TG-команда /autostart — управление автостартом кабинета по расписанию.

Формы:
- /autostart            — показать текущий конфиг.
- /autostart on | off   — включить / выключить фичу.
- /autostart HH:MM      — задать время (UTC).

Выбор кампаний — галочками в UI (web/mini), не через TG.

Money-критично: при включённом автостарте воркер cabinet_scheduler в заданное
время сам включит объявления СВОИХ выбранных кампаний и запустит scan.
Owner-scoping берётся из observer_config (тот же тег, что у /pause /resume).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.ext.asyncio import AsyncEngine

from core.scheduler.cabinet_autostart import read_autostart_config, write_autostart_config
from core.telegram import format as fmt
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
    count = len(config.get("campaign_ids") or [])
    camps_str = f"{count} шт." if count else "— (не выбраны)"
    return (
        f"🗓 {fmt.b('Автостарт кабинета')}: {state}\n"
        f"Время: {fmt.code(f'{hour:02d}:{minute:02d}')} UTC (ежедневно)\n"
        f"Кампаний выбрано: {fmt.esc(camps_str)}\n\n"
        "Что делает: в указанное время включает объявления выбранных кампаний и "
        "запускает скан. Выбор кампаний — галочками в UI (web/mini)."
    )


_USAGE = "\n".join(
    [
        fmt.b("Использование /autostart"),
        f"{fmt.code('/autostart')} — показать настройки.",
        f"{fmt.code('/autostart on')} — включить.",
        f"{fmt.code('/autostart off')} — выключить.",
        f"{fmt.code('/autostart HH:MM')} — задать время (UTC).",
        "",
        f"Пример: {fmt.code('/autostart 06:00')} — ежедневно в 06:00 UTC.",
        "Кампании выбираются галочками в UI (web/mini).",
    ]
)


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

    # HH:MM — задать время (UTC). Кампании выбираются в UI.
    m = _TIME_RE.match(first)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        config = await read_autostart_config(engine)
        config["hour_utc"] = hour
        config["minute_utc"] = minute
        await write_autostart_config(engine, config)
        await send_text(
            client,
            chat_id=chat_id,
            text="✅ Время обновлено.\n\n" + _format_config(config),
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
