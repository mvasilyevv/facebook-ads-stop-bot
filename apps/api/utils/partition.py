# -*- coding: utf-8 -*-
"""Вспомогательные утилиты для запросов к партиционированным таблицам."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def default_window(hours: int = 168) -> tuple[datetime, datetime]:
    """Возвращает временное окно (from_dt, to_dt) для партиционированных запросов.

    По умолчанию — последние 7 суток (168 часов).
    Всегда aware UTC.

    Args:
        hours: ширина окна в часах. По умолчанию 168 (7 дней).

    Returns:
        Кортеж (from_dt, to_dt) где оба значения aware-UTC.
    """
    now = datetime.now(UTC)
    return now - timedelta(hours=hours), now
