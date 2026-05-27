# -*- coding: utf-8 -*-
"""Маппинг статусов TaskQueue между v2-БД (lowercase) и фронтом (UPPERCASE).

В v2-схеме `task_queue.status` хранится в lowercase:
    draft, pending, running, succeeded, failed, retrying, cancelled

Фронт ожидает UPPERCASE-значения:
    PENDING, RUNNING, SUCCEEDED, FAILED, RETRYING, CANCELLED

`draft` маппится в `PENDING` — фронт не имеет отдельного состояния для черновиков.
"""

from __future__ import annotations

# Маппинг db → frontend (forward).
_DB_TO_FRONTEND: dict[str, str] = {
    "draft": "PENDING",
    "pending": "PENDING",
    "running": "RUNNING",
    "succeeded": "SUCCEEDED",
    "failed": "FAILED",
    "retrying": "RETRYING",
    "cancelled": "CANCELLED",
}

# Маппинг frontend → db (reverse).
# `PENDING` → `pending` (draft — внутренний статус, не экспонируется).
_FRONTEND_TO_DB: dict[str, str] = {
    "PENDING": "pending",
    "RUNNING": "running",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "RETRYING": "retrying",
    "CANCELLED": "cancelled",
}


def to_frontend_task_status(db_status: str) -> str:
    """Конвертирует lowercase v2-статус в UPPERCASE для фронта.

    Args:
        db_status: значение `task_queue.status` из БД (например, ``"draft"``).

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


def from_frontend_task_status(frontend_status: str) -> str:
    """Конвертирует UPPERCASE frontend-статус в lowercase для БД.

    Args:
        frontend_status: статус из запроса фронта (например, ``"PENDING"``).

    Returns:
        lowercase-статус для записи в `task_queue.status` (например, ``"pending"``).

    Raises:
        ValueError: если статус неизвестен.
    """
    try:
        return _FRONTEND_TO_DB[frontend_status]
    except KeyError as exc:
        raise ValueError(
            f"Неизвестный frontend-статус задачи: {frontend_status!r}. "
            f"Допустимые значения: {list(_FRONTEND_TO_DB)}"
        ) from exc
