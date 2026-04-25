# -*- coding: utf-8 -*-
"""Тесты рекомендаций observer-порогов по истории."""

from __future__ import annotations

from decimal import Decimal


# Проверяем, что история с дешёвыми событиями рекомендует более ранний стоп.
def test_threshold_recommendation_tightens_stop_when_history_is_cheaper():
    from core.observer.threshold_recommendations import build_threshold_recommendation_step

    result = build_threshold_recommendation_step(
        step_id="cpl",
        code="CPL",
        title="Лид",
        ratios=[Decimal("45"), Decimal("50"), Decimal("55"), Decimal("60"), Decimal("65")],
        current_stop_percent=Decimal("100"),
        current_warning_percent=Decimal("80"),
        min_samples=3,
    )

    assert result.can_apply is True
    assert result.recommended_stop_percent == Decimal("70")
    assert result.recommended_warning_percent == Decimal("80")
    assert result.p80_ratio == Decimal("60.00")


# Проверяем, что волатильная история оставляет более раннее предупреждение.
def test_threshold_recommendation_moves_warning_earlier_for_volatile_history():
    from core.observer.threshold_recommendations import build_threshold_recommendation_step

    result = build_threshold_recommendation_step(
        step_id="cpc",
        code="CPC",
        title="Клик",
        ratios=[Decimal("30"), Decimal("45"), Decimal("60"), Decimal("90"), Decimal("120")],
        current_stop_percent=Decimal("100"),
        current_warning_percent=Decimal("80"),
        min_samples=3,
    )

    assert result.recommended_stop_percent == Decimal("100")
    assert result.recommended_warning_percent == Decimal("75")
    assert "Разброс высокий" in result.reason


# Проверяем, что при недостатке замеров рекомендация не применяется.
def test_threshold_recommendation_requires_minimum_samples():
    from core.observer.threshold_recommendations import build_threshold_recommendation_step

    result = build_threshold_recommendation_step(
        step_id="cpr",
        code="CPR",
        title="Регистрация",
        ratios=[Decimal("70"), Decimal("75")],
        current_stop_percent=Decimal("100"),
        current_warning_percent=Decimal("80"),
        min_samples=3,
    )

    assert result.can_apply is False
    assert result.confidence == "LOW"
    assert result.recommended_stop_percent is None
