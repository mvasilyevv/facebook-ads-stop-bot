# -*- coding: utf-8 -*-
"""Инвариант: ступень предупреждения не схлопывается со стопом при целом счётчике.

Правило «регистрации без депозитов» использует целочисленный счётчик. При малом
стопе (5) и чувствительности > 80% формула ceil(stop * pct / 100) возвращает то же
число, что и stop — ступень предупреждения исчезает, объявление уходит в паузу
без предшествующего алерта.

Фиксируем ожидаемое поведение: за одну регистрацию до стопа должно быть WARNING,
а не None. Тесты падают по ассерту до фикса и проходят после.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from core.domain import AlertStage
from core.rules.evaluator import _warning_count, evaluate_stop_rules
from core.rules.types import REGS_NO_DEP_STOP_COUNT, RuleContext
from core.scanner.models import ScannedAdRow

_STOP_COUNT = REGS_NO_DEP_STOP_COUNT  # == 5, зафиксировано владельцем


def _make_row(registrations: int) -> ScannedAdRow:
    return ScannedAdRow(
        fb_ad_id="111",
        campaign_name="TEST | 111",
        adset_name="TEST_ADSET",
        ad_name="TEST_AD",
        delivery_status="ACTIVE",
        spend=Decimal("0.00"),
        registrations=registrations,
        cost_per_registration=None,
    )


def _make_ctx(warning_pct: Decimal) -> RuleContext:
    return RuleContext(
        currency="USD",
        currency_exponent=2,
        cpa_amount=Decimal("100.00"),
        warning_percent_of_stop=warning_pct,
        stop_percent_of_base=Decimal("80"),
        frequency_anomaly_enabled=False,
    )


# Конфигурации, при которых старая формула ceil(stop * pct / 100) == stop (вырождение).
# stop=5: любой pct > 80 даёт ceil(...) == 5. Проверяем несколько граничных значений.
_COLLAPSE_CASES = [
    Decimal("81"),  # ceil(5 × 81 / 100) = ceil(4.05) = 5 == stop
    Decimal("90"),  # ceil(5 × 90 / 100) = ceil(4.50) = 5 == stop  ← текущий дефект
    Decimal("95"),  # ceil(5 × 95 / 100) = ceil(4.75) = 5 == stop
    Decimal("99"),  # ceil(5 × 99 / 100) = ceil(4.95) = 5 == stop
    Decimal("100"),  # ceil(5 × 100 / 100) = ceil(5.00) = 5 == stop
]


@pytest.mark.parametrize("warning_pct", _COLLAPSE_CASES)
def test_warning_fires_one_step_before_stop(warning_pct: Decimal) -> None:
    """За одну регистрацию до стопа stage должен быть WARNING, а не None.

    До фикса: _warning_count схлопывается в stop_count, порог предупреждения
    никогда не достигается при registrations = stop_count - 1 → stage = None.
    """
    row = _make_row(registrations=_STOP_COUNT - 1)
    result = evaluate_stop_rules(row, _make_ctx(warning_pct))

    assert result.stage == AlertStage.WARNING, (
        f"warning_pct={warning_pct}%, stop={_STOP_COUNT}, "
        f"registrations={_STOP_COUNT - 1}: "
        f"ожидается WARNING, получен stage={result.stage}. "
        "Ступень предупреждения схлопнулась со стопом."
    )
    assert any(h.code == "regs_no_dep_stop" for h in result.warning_hits), (
        f"warning_pct={warning_pct}%: WARNING должен быть от regs_no_dep_stop, "
        f"но warning_hits={result.warning_hits}"
    )


def test_stop_fires_at_stop_count() -> None:
    """При stop_count регистрациях без депозитов — STOP (инвариант не трогает стоп)."""
    row = _make_row(registrations=_STOP_COUNT)
    result = evaluate_stop_rules(row, _make_ctx(Decimal("90")))

    assert result.stage == AlertStage.STOP
    assert any(h.code == "regs_no_dep_stop" for h in result.stop_hits)


def test_no_alert_below_warning_count() -> None:
    """Меньше stop_count - 1 регистраций — нет алерта (пороги не занижены)."""
    row = _make_row(registrations=_STOP_COUNT - 2)
    result = evaluate_stop_rules(row, _make_ctx(Decimal("90")))

    assert result.stage is None, (
        f"registrations={_STOP_COUNT - 2} не должен триггерить алерт, но stage={result.stage}"
    )


@pytest.mark.parametrize("warning_pct", [Decimal(p) for p in range(50, 101)])
def test_warning_step_strictly_below_stop_for_all_sensitivity_values(
    warning_pct: Decimal,
) -> None:
    """При любом легальном значении чувствительности (50..100%) stage на stop-1
    регистрациях не равен STOP (ступени не схлопываются)."""
    row = _make_row(registrations=_STOP_COUNT - 1)
    result = evaluate_stop_rules(row, _make_ctx(warning_pct))

    assert result.stage != AlertStage.STOP, (
        f"warning_pct={warning_pct}%, registrations={_STOP_COUNT - 1}: "
        "stage=STOP — ступень предупреждения схлопнулась со стопом"
    )


@pytest.mark.parametrize("stop_count", [0, 1])
def test_no_warning_step_exists_below_two_registrations(stop_count: int) -> None:
    """При стопе на первой регистрации предупреждать не за что.

    Ступени между нулём и первой регистрацией не существует, поэтому порог
    предупреждения — «не задано», а не ноль. Ноль регистраций есть
    подтверждённый ноль: нулевой порог дал бы предупреждение на каждом
    объявлении, ещё не начавшем работать.
    """
    assert _warning_count(stop_count) is None


def test_warning_step_stays_below_stop_for_every_reasonable_threshold() -> None:
    """Ступень предупреждения строго ниже стопа везде, где она существует."""
    for stop_count in range(2, 51):
        warning = _warning_count(stop_count)
        assert warning is not None
        assert warning < stop_count, f"ступень схлопнулась при stop={stop_count}"
        assert warning >= 1, f"ступень ушла в ноль при stop={stop_count}"
