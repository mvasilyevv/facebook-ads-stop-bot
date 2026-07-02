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
    assert select_scan_mode(alerts_stop=1, alerts_warning=3, rows_with_offer=10) == "CRITICAL"


# Warning без stop → ELEVATED.
def test_select_mode_warning():
    assert select_scan_mode(alerts_stop=0, alerts_warning=2, rows_with_offer=5) == "ELEVATED"


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


# ── Удержание режима по стоячему инциденту (не только по переходам) ───────────


# Ад сидит в warning_sent (переход был в прошлом цикле, новых нет) → ELEVATED
# удерживается, а не откатывается к CALM (дефект инцидента 02.07).
def test_select_mode_sustains_elevated_while_warning_state():
    mode = select_scan_mode(
        alerts_stop=0, alerts_warning=0, rows_with_offer=5, ads_in_warning_state=1
    )
    assert mode == "ELEVATED"


# Ад в stop_sent/claimed (пауза ещё не подтверждена Meta) → CRITICAL удерживается.
def test_select_mode_sustains_critical_while_stop_state():
    mode = select_scan_mode(alerts_stop=0, alerts_warning=0, rows_with_offer=5, ads_in_stop_state=1)
    assert mode == "CRITICAL"


# Стоячий stop важнее стоячего warning (приоритет как у переходов).
def test_select_mode_standing_stop_beats_standing_warning():
    mode = select_scan_mode(
        alerts_stop=0,
        alerts_warning=0,
        rows_with_offer=5,
        ads_in_stop_state=1,
        ads_in_warning_state=3,
    )
    assert mode == "CRITICAL"


# Инцидент закрылся (счётчики состояний 0, переходов нет) → возврат к CALM.
def test_select_mode_returns_to_calm_after_incident_closed():
    mode = select_scan_mode(
        alerts_stop=0,
        alerts_warning=0,
        rows_with_offer=5,
        ads_in_stop_state=0,
        ads_in_warning_state=0,
    )
    assert mode == "CALM"


# resolve_scan_mode читает счётчики состояний из summary (reader-сторона контракта).
def test_resolve_reads_state_counters_from_summary():
    summary = {
        "outcome": "success",
        "alerts_stop": 0,
        "alerts_warning": 0,
        "rows_with_offer": 7,
        "ads_in_warning_state": 2,
        "ads_in_stop_state": 0,
    }
    assert resolve_scan_mode(summary) == "ELEVATED"


# ── Контракт writer↔reader: pipeline → observer summary → resolve_scan_mode ───
# Урок CRIT-2 (Round 10): счётчик, который writer не пишет или reader не читает,
# молча превращает фичу в no-op. Прогоняем цепочку целиком на фейковых данных.


# _bump_state_counters: warning_sent → warning-счётчик, stop_sent/claimed → stop-счётчик,
# normal/disabled — инцидента нет, счётчики не растут.
def test_bump_state_counters_by_state():
    from core.observer.pipeline import CycleResult, _bump_state_counters

    result = CycleResult()
    for state in ("warning_sent", "stop_sent", "claimed", "normal", "disabled"):
        _bump_state_counters(result, state)
    assert result.ads_in_warning_state == 1
    assert result.ads_in_stop_state == 2


# Агрегация мульти-кабинетного цикла суммирует счётчики состояний, и итоговый
# summary даёт удержание ELEVATED через resolve_scan_mode (E2E контракта).
def test_aggregate_summary_carries_state_counters_to_resolver():
    from apps.observer_worker.main import _aggregate_cycle_summary

    per_account = [
        {
            "outcome": "success",
            "scan_id": 1,
            "duration_ms": 100,
            "rows_total": 5,
            "rows_with_offer": 5,
            "alerts_warning": 0,
            "alerts_stop": 0,
            "ads_in_warning_state": 1,
            "ads_in_stop_state": 0,
            "tg_dispatched": None,
            "error": None,
            "ad_account_id": "act_1",
        },
        {
            "outcome": "success",
            "scan_id": 2,
            "duration_ms": 100,
            "rows_total": 3,
            "rows_with_offer": 3,
            "alerts_warning": 0,
            "alerts_stop": 0,
            "ads_in_warning_state": 1,
            "ads_in_stop_state": 0,
            "tg_dispatched": None,
            "error": None,
            "ad_account_id": "act_2",
        },
    ]
    summary = _aggregate_cycle_summary(per_account)
    assert summary["ads_in_warning_state"] == 2
    assert summary["ads_in_stop_state"] == 0
    assert resolve_scan_mode(summary) == "ELEVATED"


# Writer-сторона per-account summary: _run_account_scan обязан прокидывать оба
# счётчика из CycleResult в dict (анти-регресс имён ключей, стиль Round 11).
def test_account_summary_source_contains_state_counters():
    import inspect

    import apps.observer_worker.main as ow

    src = inspect.getsource(ow._run_account_scan)
    assert '"ads_in_warning_state": cycle_result.ads_in_warning_state' in src
    assert '"ads_in_stop_state": cycle_result.ads_in_stop_state' in src
