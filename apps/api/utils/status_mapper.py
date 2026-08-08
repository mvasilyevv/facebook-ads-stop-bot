# -*- coding: utf-8 -*-
"""Маппинг статусов TaskQueue между БД (lowercase) и фронтом (UPPERCASE).

В схеме `task_queue.status` хранится в lowercase:
    pending, running, succeeded, failed, retrying, cancelled

Фронт ожидает UPPERCASE-значения:
    PENDING, RUNNING, SUCCEEDED, FAILED, RETRYING, CANCELLED

"""

from __future__ import annotations

# Маппинг db → frontend (forward).
_DB_TO_FRONTEND: dict[str, str] = {
    "pending": "PENDING",
    "running": "RUNNING",
    "succeeded": "SUCCEEDED",
    "failed": "FAILED",
    "retrying": "RETRYING",
    "cancelled": "CANCELLED",
}


def to_frontend_task_status(db_status: str) -> str:
    """Конвертирует lowercase статус в UPPERCASE для фронта.

    Args:
        db_status: значение `task_queue.status` из БД (например, ``"pending"``).

    Returns:
        UPPERCASE-статус для фронта (например, ``"PENDING"``).

    Raises:
        ValueError: если статус неизвестен.
    """
    try:
        return _DB_TO_FRONTEND[db_status]
    except KeyError as exc:
        raise ValueError(
            f"Неизвестный db-статус задачи: {db_status!r}. "
            f"Допустимые значения: {list(_DB_TO_FRONTEND)}"
        ) from exc
