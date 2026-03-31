# -*- coding: utf-8 -*-
"""Общие helper'ы для текущего живого батча сканирования."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import AdSnapshot

LIVE_BATCH_WINDOW = timedelta(minutes=30)


def _normalize_datetime(value: datetime) -> datetime:
    """Нормализует datetime к timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def compute_live_batch_start(
    last_scan: datetime | None,
    *,
    window: timedelta = LIVE_BATCH_WINDOW,
) -> datetime:
    """Возвращает нижнюю границу актуального живого батча."""
    if last_scan is None:
        return datetime.now(UTC)
    return _normalize_datetime(last_scan) - window


def compute_live_batch_marker(
    last_scan: datetime | None,
    *,
    window: timedelta = LIVE_BATCH_WINDOW,
) -> datetime:
    """Строит стабильный маркер живого батча для дедупликации событий."""
    if last_scan is None:
        last_scan = datetime.now(UTC)
    last_scan = _normalize_datetime(last_scan)
    window_seconds = int(window.total_seconds())
    timestamp = int(last_scan.timestamp())
    aligned_timestamp = timestamp - (timestamp % window_seconds)
    return datetime.fromtimestamp(aligned_timestamp, tz=UTC)


async def load_live_batch_bounds(
    session: AsyncSession,
    *,
    window: timedelta = LIVE_BATCH_WINDOW,
) -> tuple[datetime | None, datetime | None]:
    """Возвращает верхнюю и нижнюю границу живого батча."""
    last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
    if last_scan is None:
        return None, None
    return _normalize_datetime(last_scan), compute_live_batch_start(last_scan, window=window)


def is_within_live_batch(
    observed_at: datetime | None,
    batch_start: datetime | None,
) -> bool:
    """Проверяет, что timestamp попадает в текущий живой батч."""
    if observed_at is None or batch_start is None:
        return False
    return _normalize_datetime(observed_at) >= _normalize_datetime(batch_start)
