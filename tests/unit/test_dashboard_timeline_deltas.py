# -*- coding: utf-8 -*-
"""Тесты дельта-логики timeline: spend = приращения, а не нарастающий итог."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from apps.api.routers.dashboard import _build_performance_timeline_from_metric_history_rows

# Временная зона, которую вернёт _dashboard_timezone() в тестах
_TZ = ZoneInfo("Europe/Moscow")


def _row(ad_id: uuid.UUID, cycle_ts: datetime, spend: str, regs: int = 0, deps: int = 0):
    """Фабрика строки метрик (аналог ORM-Row через SimpleNamespace)."""
    return SimpleNamespace(
        ad_id=ad_id,
        fb_ad_id=str(ad_id),
        cycle_ts=cycle_ts,
        spend=Decimal(spend),
        registrations=regs,
        deposits=deps,
    )


def _build(rows, *, day: datetime, period: str = "today", now_offset_min: int = 90):
    """Вспомогательная обёртка: cutoff = начало дня, now = cutoff + offset."""
    cutoff = day.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_TZ)
    now = cutoff + timedelta(minutes=now_offset_min)
    with (
        patch(
            "apps.api.routers.dashboard.get_settings",
            return_value=type("S", (), {"app_timezone": "Europe/Moscow"})(),
        ),
        patch(
            "apps.api.routers.dashboard._dashboard_now",
            return_value=now,
        ),
    ):
        return _build_performance_timeline_from_metric_history_rows(
            rows,
            period=period,
            now=now,
            cutoff=cutoff,
        )


_DAY = datetime(2024, 6, 1, tzinfo=ZoneInfo("Europe/Moscow"))


# Базовый сценарий: 3 объявления, spend в каждом бакете = дельта, не накопительный итог
def test_spend_is_delta_not_cumulative():
    ad1 = uuid.uuid4()
    ad2 = uuid.uuid4()
    ad3 = uuid.uuid4()

    base = _DAY  # 00:00 Moscow
    rows = [
        # Бакет 0:00 — первое появление, spend считается от 0
        _row(ad1, base + timedelta(minutes=5), "10.00"),
        _row(ad2, base + timedelta(minutes=10), "5.00"),
        _row(ad3, base + timedelta(minutes=20), "3.00"),
        # Бакет 0:30 — spend вырос
        _row(ad1, base + timedelta(minutes=35), "15.00"),
        _row(ad2, base + timedelta(minutes=40), "8.00"),
        _row(ad3, base + timedelta(minutes=45), "6.00"),
        # Бакет 1:00 — ещё один рост
        _row(ad1, base + timedelta(minutes=65), "20.00"),
        _row(ad2, base + timedelta(minutes=70), "10.00"),
        _row(ad3, base + timedelta(minutes=75), "9.00"),
    ]

    # now_offset_min=89 → last_bucket = 1:00, итого 3 бакета: 0:00, 0:30, 1:00
    timeline = _build(rows, day=_DAY, now_offset_min=89)

    # Три бакета: 0:00, 0:30, 1:00
    assert len(timeline) == 3

    # Бакет 0:00 — дельта от нуля (первое появление): 10+5+3 = 18
    assert Decimal(timeline[0].spend) == Decimal("18.00")
    # Бакет 0:30 — дельта: (15-10)+(8-5)+(6-3) = 5+3+3 = 11
    assert Decimal(timeline[1].spend) == Decimal("11.00")
    # Бакет 1:00 — дельта: (20-15)+(10-8)+(9-6) = 5+2+3 = 10
    assert Decimal(timeline[2].spend) == Decimal("10.00")


# Пустой бакет не переносит накопленное значение — spend = 0
def test_empty_bucket_spend_is_zero():
    ad1 = uuid.uuid4()
    base = _DAY

    rows = [
        # Только бакет 0:00, бакет 0:30 — пустой
        _row(ad1, base + timedelta(minutes=5), "20.00"),
        # Бакет 1:00 снова появляется
        _row(ad1, base + timedelta(minutes=65), "25.00"),
    ]

    # now_offset_min=89 → last_bucket = 1:00, итого 3 бакета
    timeline = _build(rows, day=_DAY, now_offset_min=89)
    assert len(timeline) == 3
    # Бакет 0:00 — дельта 20
    assert Decimal(timeline[0].spend) == Decimal("20.00")
    # Бакет 0:30 — пустой, spend = 0
    assert Decimal(timeline[1].spend) == Decimal("0.00")
    # Бакет 1:00 — дельта от последнего (20 → 25) = 5
    assert Decimal(timeline[2].spend) == Decimal("5.00")


# Сброс: spend стал меньше предыдущего (новый день кабинета) — дельта = cur_spend
def test_reset_spend_treated_as_delta_from_zero():
    ad1 = uuid.uuid4()
    base = _DAY

    rows = [
        # Бакет 0:00 — spend 10
        _row(ad1, base + timedelta(minutes=5), "10.00"),
        # Бакет 0:30 — spend упал до 2 (сброс кабинета), дельта = 2
        _row(ad1, base + timedelta(minutes=35), "2.00"),
    ]

    # now_offset_min=59 → last_bucket = 0:30, итого 2 бакета
    timeline = _build(rows, day=_DAY, now_offset_min=59)
    assert len(timeline) == 2
    assert Decimal(timeline[0].spend) == Decimal("10.00")
    assert Decimal(timeline[1].spend) == Decimal("2.00")


# registrations и deposits тоже считаются как дельты
def test_registrations_and_deposits_are_deltas():
    ad1 = uuid.uuid4()
    base = _DAY

    rows = [
        _row(ad1, base + timedelta(minutes=5), "0", regs=3, deps=1),
        _row(ad1, base + timedelta(minutes=35), "0", regs=7, deps=4),
    ]

    # now_offset_min=59 → last_bucket = 0:30, итого 2 бакета
    timeline = _build(rows, day=_DAY, now_offset_min=59)
    assert timeline[0].registrations == 3
    assert timeline[0].deposits == 1
    # Дельта: 7-3=4 regs, 4-1=3 deps
    assert timeline[1].registrations == 4
    assert timeline[1].deposits == 3
