# -*- coding: utf-8 -*-
"""Unit: catch-up окна tracker_aggregator по last_run_at (M-7, аудит 2026-07-12).

Простой воркера > lookback через полночь UTC терял хвост прошлого дня при
фикс-окне [now-2h, now]. run_once тянет окно до last_run_at из аудита, но не
глубже MAX_CATCHUP. Проверяем расчёт window_start без живой БД (fake engine).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

import apps.tracker_aggregator_worker.worker as w
from core.adset_pro.aggregator import AggregationResult

_NOW = datetime(2026, 5, 28, 3, 0, tzinfo=timezone.utc)


async def _run_capturing(last_run: datetime | None) -> datetime:
    """Гоняет run_once с замоканными агрегатором/аудитом, возвращает window_start."""
    captured: dict[str, datetime] = {}

    async def _fake_agg(engine, *, window_start, window_end):
        captured["ws"] = window_start
        return AggregationResult(
            window_start, window_end, window_start, window_end, 0, 0, 0, 0, Decimal(0), 0
        )

    with (
        patch.object(w, "aggregate_postback_events", _fake_agg),
        patch.object(w, "_read_last_run_at", AsyncMock(return_value=last_run)),
        patch.object(w, "_write_audit", AsyncMock()),
    ):
        await w.run_once(object(), now=_NOW)
    return captured["ws"]


# last_run в прошлом дне (23:00) при now=03:00 → окно расширяется до 23:00 (через полночь).
@pytest.mark.asyncio
async def test_catchup_extends_window_across_midnight() -> None:
    ws = await _run_capturing(datetime(2026, 5, 27, 23, 0, tzinfo=timezone.utc))
    assert ws == datetime(2026, 5, 27, 23, 0, tzinfo=timezone.utc)


# Свежий last_run (1ч назад, позже now-2h) → фикс-окно 2ч не трогаем.
@pytest.mark.asyncio
async def test_fresh_last_run_keeps_fixed_window() -> None:
    ws = await _run_capturing(_NOW - timedelta(hours=1))
    assert ws == _NOW - timedelta(hours=2)


# last_run в далёком прошлом (10 дней) → cap MAX_CATCHUP.
@pytest.mark.asyncio
async def test_catchup_capped_at_max() -> None:
    ws = await _run_capturing(_NOW - timedelta(days=10))
    assert ws == _NOW - w.MAX_CATCHUP


# Аудита нет (None) → фикс-окно (первый запуск / потеря аудита).
@pytest.mark.asyncio
async def test_no_audit_keeps_fixed_window() -> None:
    ws = await _run_capturing(None)
    assert ws == _NOW - timedelta(hours=2)
