# -*- coding: utf-8 -*-
"""Хранение runtime-статуса observer worker для UI и диагностики."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from core.db import get_session_factory
from core.settings_queries import get_or_create_observer_settings

logger = logging.getLogger(__name__)

_MAX_STATUS_LEN = 32
_MAX_MESSAGE_LEN = 500


def _truncate_text(value: str | None, max_length: int) -> str | None:
    """Ограничивает длину текста под размер колонки."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def format_observer_runtime_message(error: Exception | str | None) -> str | None:
    """Преобразует техническую ошибку в короткую понятную причину для UI."""
    if error is None:
        return None

    text = str(error).strip()
    if not text:
        return None

    lowered = text.casefold()

    if "cdp-порт" in lowered:
        return "Vision запустил профиль без CDP-порта. Воркер не может подключиться к браузеру."
    if "не задан vision x-token" in lowered:
        return "Не настроен Vision X-Token. Укажите токен в настройках Vision."
    if "не найден среди запущенных" in lowered and "профиль" in lowered:
        return "Профиль Vision не найден среди запущенных. Проверьте, что открыт нужный профиль."
    if "patchright" in lowered and "не установлен" in lowered:
        return "В окружении не установлен patchright. Воркер не может подключиться к браузеру."

    return _truncate_text(text, _MAX_MESSAGE_LEN)


async def update_observer_runtime_status(
    *,
    status: str,
    message: str | None = None,
    last_error: str | None = None,
    clear_last_error: bool = False,
    heartbeat_at: datetime | None = None,
) -> None:
    """Сохраняет текущий runtime-статус observer worker в singleton-настройках."""
    factory = get_session_factory()
    now = heartbeat_at or datetime.now(UTC)

    try:
        async with factory() as session:
            row = await get_or_create_observer_settings(session)

            row.worker_status = _truncate_text(status, _MAX_STATUS_LEN)
            row.worker_message = _truncate_text(message, _MAX_MESSAGE_LEN)
            row.worker_heartbeat_at = now

            if clear_last_error:
                row.worker_last_error = None
                row.worker_last_error_at = None
            elif last_error:
                row.worker_last_error = _truncate_text(last_error, _MAX_MESSAGE_LEN)
                row.worker_last_error_at = now

            await session.commit()
    except Exception:
        logger.debug("Не удалось обновить runtime-статус observer", exc_info=True)
