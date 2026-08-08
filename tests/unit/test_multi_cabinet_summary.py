# -*- coding: utf-8 -*-
"""Unit: агрегация per-account summary в свод цикла (мульти-кабинет M3).

Семантика outcome критична для двух потребителей:
- Layer 3 degraded-трекинг: "error" должен означать ПОЛНЫЙ провал цикла;
- адаптивный интервал: stop-хит в любом кабинете → CRITICAL всему циклу
  (через суммирование alerts_stop, resolve_scan_mode считает по сумме).
"""

from __future__ import annotations

from apps.observer_worker.main import _aggregate_cycle_summary
from core.observer.adaptive_interval import resolve_scan_mode


def _acc(outcome: str, *, stop=0, warning=0, with_offer=0, error=None, acc_id="111") -> dict:
    """Шаблон per-account summary."""
    return {
        "outcome": outcome,
        "scan_id": 1,
        "ad_account_id": acc_id,
        "duration_ms": 100,
        "rows_total": with_offer,
        "rows_with_offer": with_offer,
        "alerts_warning": warning,
        "alerts_stop": stop,
        "tg_dispatched": None,
        "error": error,
    }


# Все кабинеты упали → outcome=error (полный провал, Layer 3 должен видеть деградацию).
def test_all_error_gives_error() -> None:
    agg = _aggregate_cycle_summary([_acc("error", error="boom"), _acc("error", error="boom2")])
    assert agg["outcome"] == "error"
    assert agg["error"] == "boom"


# Один success среди ошибок → explicit partial, а не false-green.
def test_success_plus_error_gives_partial() -> None:
    agg = _aggregate_cycle_summary([_acc("error", error="x"), _acc("success", with_offer=3)])
    assert agg["outcome"] == "partial"
    assert agg["rows_with_offer"] == 3


# Смесь empty+error не является подтверждённым нулём.
def test_empty_plus_error_gives_partial() -> None:
    agg = _aggregate_cycle_summary([_acc("empty"), _acc("error", error="x")])
    assert agg["outcome"] == "partial"


def test_empty_only_is_confirmed_empty() -> None:
    agg = _aggregate_cycle_summary([_acc("empty"), _acc("empty", acc_id="222")])
    assert agg["outcome"] == "empty"


def test_timeout_or_skipped_never_becomes_success() -> None:
    assert _aggregate_cycle_summary([_acc("success"), _acc("timeout")])["outcome"] == "partial"
    assert _aggregate_cycle_summary([_acc("empty"), _acc("skipped")])["outcome"] == "partial"


def test_lock_skip_without_scan_id_does_not_crash_aggregation() -> None:
    summary = _aggregate_cycle_summary(
        [
            _acc("success"),
            {
                "ad_account_id": "222",
                "outcome": "skipped",
                "error": "cabinet_actor_lock_held",
            },
        ]
    )

    assert summary["outcome"] == "partial"
    assert summary["accounts"][1]["scan_id"] is None


# Worst-case агрегация для адаптивного интервала: stop в ОДНОМ кабинете → CRITICAL цикла.
def test_stop_in_one_account_gives_critical_mode() -> None:
    agg = _aggregate_cycle_summary(
        [
            _acc("success", with_offer=5, acc_id="111"),
            _acc("success", with_offer=4, stop=1, acc_id="222"),
        ]
    )
    assert resolve_scan_mode(agg) == "CRITICAL"


# Warning в одном из кабинетов (без stop) → ELEVATED.
def test_warning_in_one_account_gives_elevated_mode() -> None:
    agg = _aggregate_cycle_summary(
        [
            _acc("success", with_offer=5),
            _acc("success", with_offer=4, warning=2, acc_id="222"),
        ]
    )
    assert resolve_scan_mode(agg) == "ELEVATED"


# Счётчики суммируются, а scan identity остаётся привязанной к кабинету.
def test_counters_summed_and_scan_ids_stay_per_account() -> None:
    a1 = _acc("success", with_offer=2)
    a1["scan_id"] = 10
    a2 = _acc("success", with_offer=3, acc_id="222")
    a2["scan_id"] = 11
    agg = _aggregate_cycle_summary([a1, a2])
    assert agg["rows_total"] == 5
    assert [account["scan_id"] for account in agg["accounts"]] == [10, 11]
    assert agg["duration_ms"] == 200
