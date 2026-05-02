# -*- coding: utf-8 -*-
"""Хранение runtime-статуса observer worker для UI и диагностики."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.db import get_session_factory
from core.models import WorkerHeartbeat
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

    if "автоперезапуск профиля для восстановления cdp-порта отключён" in lowered:
        return (
            "Vision запустил профиль без CDP-порта. "
            "Автоперезапуск выключен, поэтому профиль нужно перезапустить вручную "
            "или убрать VISION_AUTO_RESTART_ON_MISSING_CDP=false."
        )
    if "не удалось восстановить cdp-порт автоперезапуском профиля" in lowered:
        return (
            "Vision не смог восстановить CDP-порт автоматическим перезапуском профиля. "
            "Проверьте профиль вручную и запустите его заново."
        )
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
    current_scan_interval_seconds: int | None = None,
    current_scan_jitter_seconds: int | None = None,
    current_scan_threat_level: str | None = None,
    next_scan_at: datetime | None = None,
    clear_scan_schedule: bool = False,
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

            if clear_scan_schedule:
                row.current_scan_interval_seconds = None
                row.current_scan_jitter_seconds = None
                row.current_scan_threat_level = None
                row.next_scan_at = None
            else:
                if current_scan_interval_seconds is not None:
                    row.current_scan_interval_seconds = int(current_scan_interval_seconds)
                if current_scan_jitter_seconds is not None:
                    row.current_scan_jitter_seconds = int(current_scan_jitter_seconds)
                if current_scan_threat_level is not None:
                    row.current_scan_threat_level = _truncate_text(
                        current_scan_threat_level,
                        _MAX_STATUS_LEN,
                    )
                if next_scan_at is not None:
                    row.next_scan_at = next_scan_at

            await session.commit()
    except Exception:
        logger.debug("Не удалось обновить runtime-статус observer", exc_info=True)


async def update_worker_heartbeat(
    worker_name: str,
    *,
    status: str = "running",
    message: str | None = None,
) -> None:
    """Записывает heartbeat воркера в таблицу worker_heartbeats (upsert).

    Не падает при ошибках БД — используй в finally-блоках воркеров.
    Значения: worker_name — строковый идентификатор воркера,
    например "disable", "enable", "enable_recommendation", "health_watchdog".
    """
    factory = get_session_factory()
    now = datetime.now(UTC)
    pid = os.getpid()

    try:
        async with factory() as session:
            stmt = (
                pg_insert(WorkerHeartbeat)
                .values(
                    worker_name=worker_name,
                    last_heartbeat_at=now,
                    pid=pid,
                    status=status,
                    message=message,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["worker_name"],
                    set_={
                        "last_heartbeat_at": now,
                        "pid": pid,
                        "status": status,
                        "message": message,
                        "updated_at": now,
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()
    except Exception:
        logger.debug("Не удалось записать heartbeat воркера '%s'", worker_name, exc_info=True)
