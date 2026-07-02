# -*- coding: utf-8 -*-
"""Производные метрики «Статистики залива» (core/dashboard/stats_derived.py).

Семантические тесты с ТОЧНЫМИ значениями (урок CRIT-1: shape-тесты пропускают
money-баги) — формулы CPC/CPL/CPR/CPA, CTR, CR-ступени, ROI.
"""

from __future__ import annotations

from decimal import Decimal

from core.dashboard.stats_derived import compute_derived, compute_roi_pct


# spend=100, leads=8 → CPL ровно 12.50 (точное деление, 2 знака)
def test_cpl_exact_value():
    derived = compute_derived({"spend": Decimal("100"), "leads": 8})
    assert derived["cpl"] == Decimal("12.50")


# Полная воронка: точные значения всех производных на реалистичных числах
def test_full_funnel_exact_values():
    totals = {
        "spend": Decimal("12.34"),
        "impressions": 84210,
        "clicks": 921,
        "leads": 140,
        "registrations": 88,
        "deposits": 12,
    }
    derived = compute_derived(totals)
    assert derived["cpc"] == Decimal("0.01")  # 12.34/921 = 0.0134 → 0.01
    assert derived["cpl"] == Decimal("0.09")  # 12.34/140
    assert derived["cpr"] == Decimal("0.14")  # 12.34/88
    assert derived["cpa"] == Decimal("1.03")  # 12.34/12
    assert derived["ctr_pct"] == Decimal("1.09")  # 921/84210×100
    assert derived["cr_click_lead_pct"] == Decimal("15.20")  # 140/921×100
    assert derived["cr_lead_reg_pct"] == Decimal("62.86")  # 88/140×100
    assert derived["cr_reg_dep_pct"] == Decimal("13.64")  # 12/88×100


# clicks=0 → CPC=None и CR click→lead=None (деление на ноль, не 0 и не краш)
def test_zero_clicks_gives_none():
    derived = compute_derived({"spend": Decimal("5"), "clicks": 0, "leads": 3})
    assert derived["cpc"] is None
    assert derived["cr_click_lead_pct"] is None
    # CPL при этом считается: 5/3 = 1.67
    assert derived["cpl"] == Decimal("1.67")


# Все нули (пустое окно) → все производные None
def test_all_zero_totals_gives_all_none():
    derived = compute_derived({})
    assert all(v is None for v in derived.values())


# spend строкой из БД (Decimal-str) парсится корректно
def test_spend_as_string_parsed():
    derived = compute_derived({"spend": "10.00", "leads": 4})
    assert derived["cpl"] == Decimal("2.50")


# ROI: revenue=150, spend=100 → +50.00%; revenue=50 → −50.00%
def test_roi_pct_exact():
    assert compute_roi_pct(Decimal("150"), Decimal("100")) == Decimal("50.00")
    assert compute_roi_pct(Decimal("50"), Decimal("100")) == Decimal("-50.00")


# ROI при spend=0/None → None (нечего делить)
def test_roi_pct_zero_spend_none():
    assert compute_roi_pct(Decimal("100"), Decimal("0")) is None
    assert compute_roi_pct(Decimal("100"), None) is None
