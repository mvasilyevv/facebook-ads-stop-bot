# -*- coding: utf-8 -*-
"""Почасовые дельты «Статистики залива» (core/dashboard/stats_derived.hourly_deltas).

ad_metrics — кумулятивные снимки; hourly_deltas превращает latest-per-(час×ad)
в честные «сколько случилось в этот час». Семантика LAG per-ad — тесты с
точными значениями (money).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from core.dashboard.stats_derived import hourly_deltas


def _ts(hour: int) -> datetime:
    return datetime(2026, 7, 2, hour, 0, tzinfo=UTC)


def _row(ad: str, hour: int, spend: str, clicks: int = 0, leads: int = 0) -> dict:
    return {
        "ad_id": ad,
        "bucket_ts": _ts(hour),
        "spend": Decimal(spend),
        "impressions": 0,
        "clicks": clicks,
        "leads": leads,
        "registrations": 0,
        "deposits": 0,
    }


# Кумулятив одного ада 10→30→60 по часам → дельты ровно 10/20/30 (не лестница!)
def test_cumulative_becomes_honest_deltas():
    rows = [_row("a", 5, "10"), _row("a", 6, "30"), _row("a", 7, "60")]
    points = hourly_deltas(rows)
    assert [p["spend"] for p in points] == [Decimal("10"), Decimal("20"), Decimal("30")]
    assert [p["ts"] for p in points] == [_ts(5), _ts(6), _ts(7)]


# Новый ад появился в часе 6 (LAG нет) → его кумулятив целиком = дельта этого часа,
# при этом дельта соседнего ада не искажается (LAG строго per-ad до суммирования)
def test_new_ad_mid_day_does_not_distort():
    rows = [
        _row("a", 5, "10"),
        _row("a", 6, "15"),
        _row("b", 6, "7"),  # новый ад: весь кумулятив 7 приходит в час 6
    ]
    points = hourly_deltas(rows)
    by_ts = {p["ts"]: p for p in points}
    assert by_ts[_ts(5)]["spend"] == Decimal("10")
    assert by_ts[_ts(6)]["spend"] == Decimal("12")  # 5 (ад a) + 7 (ад b)
    assert by_ts[_ts(6)]["active_ads"] == 2


# Отрицательная дельта (Meta пересчитала лиды вниз 5→3) → клэмп в 0 для показа,
# но prev обновляется сырым значением: следующий час считается от нового кумулятива
def test_negative_delta_clamped_but_prev_raw():
    rows = [
        _row("a", 5, "10", leads=5),
        _row("a", 6, "10", leads=3),  # пересчёт вниз → дельта часа 6 = 0, не −2
        _row("a", 7, "10", leads=4),  # 4−3=1 (от сырого 3, не от клэмпнутого 5)
    ]
    points = hourly_deltas(rows)
    assert [p["leads"] for p in points] == [5, 0, 1]


# Пропуск часа (ад не сканился в 6) → накопленное приезжает в час возврата
def test_gap_hour_accumulates_into_return_hour():
    rows = [_row("a", 5, "10"), _row("a", 7, "25")]
    points = hourly_deltas(rows)
    assert [(p["ts"], p["spend"]) for p in points] == [
        (_ts(5), Decimal("10")),
        (_ts(7), Decimal("15")),
    ]


# Первый час окна = весь кумулятив первого снимка (окно стартует с нуля суток кабинета)
def test_first_hour_is_full_cumulative():
    points = hourly_deltas([_row("a", 5, "42.50", clicks=3)])
    assert points[0]["spend"] == Decimal("42.50")
    assert points[0]["clicks"] == 3


# Пустой вход → пустой выход (нет фейковых точек)
def test_empty_rows_empty_points():
    assert hourly_deltas([]) == []


# active_ads считает уникальные объявления бакета, счётчики — int, spend — Decimal
def test_types_and_active_ads():
    rows = [_row("a", 5, "1.10", clicks=2), _row("b", 5, "2.00", clicks=1)]
    points = hourly_deltas(rows)
    assert points[0]["active_ads"] == 2
    assert points[0]["clicks"] == 3
    assert isinstance(points[0]["clicks"], int)
    assert points[0]["spend"] == Decimal("3.10")
    assert isinstance(points[0]["spend"], Decimal)
