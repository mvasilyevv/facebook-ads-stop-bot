# -*- coding: utf-8 -*-
"""Тесты адаптивного интервала observer: уровень угрозы → частота сканирования."""

from __future__ import annotations

from apps.observer_worker.main import (
    _ADAPTIVE_INTERVAL_CALM,
    _ADAPTIVE_INTERVAL_CRITICAL,
    _ADAPTIVE_INTERVAL_ELEVATED,
    _ADAPTIVE_INTERVAL_IDLE,
    compute_adaptive_interval,
)
from core.domain import AlertStage


def _snap(*, stage: AlertStage | None = None, offer_code: str | None = None) -> dict:
    """Создаёт минимальный snapshot для тестов."""
    return {"current_stage": stage, "resolved_offer_code": offer_code}


# Тест: немедленный ре-скан при наличии STOP-алертов
def test_immediate_rescan_on_stop_alerts():
    """При has_stop_alerts=True интервал должен быть 0 (немедленный ре-скан)."""
    interval, level = compute_adaptive_interval(
        [_snap(stage=AlertStage.WARNING, offer_code="DRC")],
        has_stop_alerts=True,
    )
    assert interval == 0
    assert level == "IMMEDIATE"


# Тест: CRITICAL при WARNING-стадии в батче
def test_critical_on_warning_stage():
    """Если есть объявление с WARNING — интервал минимальный (CRITICAL)."""
    interval, level = compute_adaptive_interval(
        [
            _snap(stage=AlertStage.WARNING, offer_code="DRC"),
            _snap(stage=None, offer_code="ABC"),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_CRITICAL
    assert level == "CRITICAL"


# Тест: CRITICAL при STOP-стадии в батче (без stop_alerts)
def test_critical_on_stop_stage_in_batch():
    """STOP-стадия в батче (FSM заблокировал алерт) тоже даёт CRITICAL."""
    interval, level = compute_adaptive_interval(
        [
            _snap(stage=AlertStage.STOP, offer_code="X"),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_CRITICAL
    assert level == "CRITICAL"


# Тест: ELEVATED при EARLY_SIGNAL
def test_elevated_on_early_signal():
    """EARLY_SIGNAL даёт ELEVATED — повышенное внимание."""
    interval, level = compute_adaptive_interval(
        [
            _snap(stage=AlertStage.EARLY_SIGNAL, offer_code="DRC"),
            _snap(stage=None, offer_code="ABC"),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_ELEVATED
    assert level == "ELEVATED"


# Тест: CALM при активных объявлениях без сигналов
def test_calm_with_monitored_ads():
    """Объявления с офферами, но без сигналов — CALM."""
    interval, level = compute_adaptive_interval(
        [
            _snap(offer_code="DRC"),
            _snap(offer_code="ABC"),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_CALM
    assert level == "CALM"


# Тест: IDLE при пустом батче
def test_idle_on_empty_batch():
    """Пустой батч (нет объявлений) — IDLE."""
    interval, level = compute_adaptive_interval([])
    assert interval == _ADAPTIVE_INTERVAL_IDLE
    assert level == "IDLE"


# Тест: IDLE при объявлениях без офферов и стадий
def test_idle_when_no_offers():
    """Объявления без офферов и стадий — IDLE."""
    interval, level = compute_adaptive_interval(
        [
            _snap(),
            _snap(),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_IDLE
    assert level == "IDLE"


# Тест: WARNING приоритетнее EARLY_SIGNAL
def test_warning_overrides_early_signal():
    """Если есть и WARNING и EARLY_SIGNAL — выбирается CRITICAL."""
    interval, level = compute_adaptive_interval(
        [
            _snap(stage=AlertStage.EARLY_SIGNAL, offer_code="A"),
            _snap(stage=AlertStage.WARNING, offer_code="B"),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_CRITICAL
    assert level == "CRITICAL"


# Тест: конкретные значения интервалов
def test_interval_values():
    """Проверяем конкретные значения интервалов из спецификации."""
    assert _ADAPTIVE_INTERVAL_CRITICAL == 15
    assert _ADAPTIVE_INTERVAL_ELEVATED == 30
    assert _ADAPTIVE_INTERVAL_CALM == 45
    assert _ADAPTIVE_INTERVAL_IDLE == 60
