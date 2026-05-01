# -*- coding: utf-8 -*-
"""Тесты адаптивного интервала observer: уровень угрозы → частота сканирования."""

from __future__ import annotations

from decimal import Decimal

from apps.observer_worker.main import (
    _ADAPTIVE_INTERVAL_ACTIVE,
    _ADAPTIVE_INTERVAL_CALM,
    _ADAPTIVE_INTERVAL_CRITICAL,
    _ADAPTIVE_INTERVAL_ELEVATED,
    _ADAPTIVE_INTERVAL_IDLE,
    compute_adaptive_interval,
)
from core.domain import AlertStage


def _snap(
    *,
    stage: AlertStage | None = None,
    offer_code: str | None = None,
    delivery_status: str = "ACTIVE",
    spend: Decimal | str | int = Decimal("0"),
    impressions: int = 0,
    clicks: int = 0,
) -> dict:
    """Создаёт минимальный snapshot для тестов."""
    return {
        "current_stage": stage,
        "resolved_offer_code": offer_code,
        "delivery_status": delivery_status,
        "spend": spend,
        "impressions": impressions,
        "clicks": clicks,
    }


# Тест: немедленный ре-скан при наличии STOP-алертов
def test_immediate_rescan_on_stop_alerts():
    """При has_stop_alerts=True интервал должен быть 0 (немедленный ре-скан)."""
    interval, level = compute_adaptive_interval(
        [_snap(stage=AlertStage.WARNING, offer_code="DRC")],
        has_stop_alerts=True,
    )
    assert interval == 0
    assert level == "IMMEDIATE"


# Тест: ELEVATED при WARNING-стадии в батче
def test_elevated_on_warning_stage():
    """Если есть объявление с WARNING — интервал повышенной частоты."""
    interval, level = compute_adaptive_interval(
        [
            _snap(stage=AlertStage.WARNING, offer_code="DRC"),
            _snap(stage=None, offer_code="ABC"),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_ELEVATED
    assert level == "ELEVATED"


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


# Тест: ACTIVE при активных объявлениях без сигналов
def test_active_with_monitored_ads():
    """Объявления с офферами и трафиком, но без сигналов — ACTIVE."""
    interval, level = compute_adaptive_interval(
        [
            _snap(offer_code="DRC", spend=Decimal("1.23")),
            _snap(offer_code="ABC"),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_ACTIVE
    assert level == "ACTIVE"


# Тест: CALM при включённых объявлениях с офферами, но полностью нулевыми метриками
def test_calm_with_zero_metric_monitored_ads():
    """Включённые объявления без трафика не должны считаться активным заливом."""
    interval, level = compute_adaptive_interval(
        [
            _snap(offer_code="DRC"),
            _snap(offer_code="ABC"),
        ]
    )
    assert interval == _ADAPTIVE_INTERVAL_CALM
    assert level == "CALM"


# Тест: CALM при выключенных объявлениях с офферами
def test_calm_with_disabled_monitored_ads():
    """Выключенные объявления с офферами оставляют спокойный режим."""
    interval, level = compute_adaptive_interval(
        [
            _snap(offer_code="DRC", delivery_status="OFF"),
            _snap(offer_code="ABC", delivery_status="OFF"),
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


# Тест: конкретные значения интервалов
def test_interval_values():
    """Проверяем конкретные значения интервалов из спецификации."""
    assert _ADAPTIVE_INTERVAL_CRITICAL == 10
    assert _ADAPTIVE_INTERVAL_ELEVATED == 13
    assert _ADAPTIVE_INTERVAL_ACTIVE == 15
    assert _ADAPTIVE_INTERVAL_CALM == 30
    assert _ADAPTIVE_INTERVAL_IDLE == 55
