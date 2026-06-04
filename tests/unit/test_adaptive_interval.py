# -*- coding: utf-8 -*-
"""Тесты адаптивного интервала сканирования (core/observer/adaptive_interval.py)."""

from __future__ import annotations

import pytest

from core.observer.adaptive_interval import (
    MAX_INTERVAL_SECONDS,
    MIN_INTERVAL_SECONDS,
    SCAN_MODE_MULTIPLIERS,
    clamp_interval,
    compute_adaptive_interval,
    resolve_scan_mode,
    select_scan_mode,
)

# ── select_scan_mode: выбор режима по итогу цикла ────────────────────────────


# Stop-хит → CRITICAL даже если есть warning и офферные ads (наивысший приоритет).
def test_select_mode_stop_wins():
    assert (
        select_scan_mode(alerts_stop=1, alerts_warning=3, rows_with_offer=10) == "CRITICAL"
    )


# Warning без stop → ELEVATED.
def test_select_mode_warning():
    assert (
        select_scan_mode(alerts_stop=0, alerts_warning=2, rows_with_offer=5) == "ELEVATED"
    )


# Есть офферные объявления, но алертов нет → CALM (штатный темп).
def test_select_mode_calm():
    assert select_scan_mode(alerts_stop=0, alerts_warning=0, rows_with_offer=4) == "CALM"


# Нет объявлений с офферами → IDLE (сканировать незачем часто).
def test_select_mode_idle():
    assert select_scan_mode(alerts_stop=0, alerts_warning=0, rows_with_offer=0) == "IDLE"


# ── compute_adaptive_interval: база × множитель режима ───────────────────────


# При базе 90 каждый режим даёт ожидаемый интервал (×0.2/0.5/1.0/1.5).
@pytest.mark.parametrize(
    "mode,expected",
    [
        ("CRITICAL", 18.0),  # 90 × 0.2
        ("ELEVATED", 45.0),  # 90 × 0.5
        ("CALM", 90.0),  # 90 × 1.0
        ("IDLE", 135.0),  # 90 × 1.5
    ],
)
def test_compute_interval_multipliers(mode, expected):
    assert compute_adaptive_interval(90.0, mode) == expected


# Базовый слайдер = CALM: ровно значение interval_seconds.
def test_calm_equals_base():
    assert compute_adaptive_interval(120.0, "CALM") == 120.0


# Малая база: CRITICAL опускается ниже минимума → зажимается до MIN.
def test_compute_interval_clamps_to_min():
    # 30 × 0.2 = 6 < MIN(10) → 10
    assert compute_adaptive_interval(30.0, "CRITICAL") == MIN_INTERVAL_SECONDS


# Большая база: IDLE превышает максимум → зажимается до MAX.
def test_compute_interval_clamps_to_max():
    # 600 × 1.5 = 900 > MAX(600) → 600
    assert compute_adaptive_interval(600.0, "IDLE") == MAX_INTERVAL_SECONDS


# Неизвестный режим трактуется как CALM (множитель 1.0) — без падения.
def test_compute_interval_unknown_mode_is_calm():
    assert compute_adaptive_interval(75.0, "WHATEVER") == 75.0


# ── clamp_interval: границы ──────────────────────────────────────────────────


# Значение ниже MIN поднимается до MIN (защита от перекрута после jitter).
def test_clamp_below_min():
    assert clamp_interval(3.0) == MIN_INTERVAL_SECONDS


# Значение выше MAX опускается до MAX.
def test_clamp_above_max():
    assert clamp_interval(5000.0) == MAX_INTERVAL_SECONDS


# Значение внутри диапазона не меняется.
def test_clamp_within_range():
    assert clamp_interval(42.0) == 42.0


# Множители режимов упорядочены: чем выше угроза — тем короче интервал.
def test_multipliers_monotonic():
    assert (
        SCAN_MODE_MULTIPLIERS["CRITICAL"]
        < SCAN_MODE_MULTIPLIERS["ELEVATED"]
        < SCAN_MODE_MULTIPLIERS["CALM"]
        < SCAN_MODE_MULTIPLIERS["IDLE"]
    )


# ── resolve_scan_mode: режим по summary цикла с учётом нештатных исходов ──────


# Пауза (юзер выключил скан) → IDLE (спим реже, trigger разбудит).
def test_resolve_paused_idle():
    assert resolve_scan_mode({"outcome": "paused", "scan_id": None}) == "IDLE"


# Ошибка скана → CALM (ретрай в штатном темпе, НЕ замедляемся до IDLE).
def test_resolve_error_calm():
    assert resolve_scan_mode({"outcome": "error", "error": "boom"}) == "CALM"


# Успех со stop-хитами → CRITICAL (угроза важнее, чем сам факт success).
def test_resolve_success_stop_critical():
    summary = {"outcome": "success", "alerts_stop": 2, "alerts_warning": 0, "rows_with_offer": 7}
    assert resolve_scan_mode(summary) == "CRITICAL"


# Успех с warning → ELEVATED.
def test_resolve_success_warning_elevated():
    summary = {"outcome": "success", "alerts_stop": 0, "alerts_warning": 1, "rows_with_offer": 7}
    assert resolve_scan_mode(summary) == "ELEVATED"


# Успех без алертов, есть офферные ads → CALM.
def test_resolve_success_calm():
    summary = {"outcome": "success", "alerts_stop": 0, "alerts_warning": 0, "rows_with_offer": 3}
    assert resolve_scan_mode(summary) == "CALM"


# Пустой скан (нет строк) → IDLE: rows_with_offer=0, сканировать нечего.
def test_resolve_empty_idle():
    summary = {"outcome": "empty", "alerts_stop": 0, "alerts_warning": 0, "rows_with_offer": 0}
    assert resolve_scan_mode(summary) == "IDLE"


# Отсутствие ключей в summary не роняет (дефолты 0 → IDLE).
def test_resolve_missing_keys_defaults_idle():
    assert resolve_scan_mode({"outcome": "success"}) == "IDLE"
