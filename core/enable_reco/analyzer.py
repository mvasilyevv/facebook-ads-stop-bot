# -*- coding: utf-8 -*-
"""Pure-функции анализа: стоит ли рекомендовать включение объявления обратно.

Контракт: принимает FSM-состояние, набор недавних метрик, пороги оффера —
возвращает RecommendationDecision (recommend + level + reasons). I/O нет.

Логика «выправилось ли»:
1. FSM в STOP_SENT (не CLAIMED/DISABLED — это уже занятые состояния, действует юзер).
2. Не заснужен.
3. Метрик ≥ MIN_METRICS_REQUIRED (по умолчанию 1) — иначе данных мало.
4. Хотя бы одно правило «выправилось»:
   - spend за окно < 50% от cpa_threshold (запас бюджета восстановился);
   - cost_per_lead снова в норме (≤ cpa_threshold);
   - cost_per_registration ≤ cpa_threshold;
   - есть свежие deposits (deposits > 0 в последней метрике).

`level`:
- "ok"      — выполнились ≥ 2 положительных условий.
- "warning" — выполнилось ровно одно условие (стоит обратить внимание, но не уверены).
- None      — рекомендовать не стоит.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

RecommendationLevel = Literal["ok", "warning"]


@dataclass(frozen=True)
class AnalyzerThresholds:
    """Глобальные настройки анализатора."""

    spend_window_share_of_cpa: Decimal = Decimal("0.5")
    min_metrics_required: int = 1


DEFAULT_THRESHOLDS = AnalyzerThresholds()


@dataclass(frozen=True)
class OfferThresholds:
    """Пороги оффера для конкретного объявления."""

    cpa_threshold: Decimal | None = None


@dataclass(frozen=True)
class MetricSnapshot:
    """Снимок одной метрики из ad_metrics (нужны только используемые поля)."""

    cycle_ts: datetime
    spend: Decimal | None = None
    cost_per_lead: Decimal | None = None
    cost_per_registration: Decimal | None = None
    deposits: int | None = None


@dataclass(frozen=True)
class RecommendationDecision:
    """Решение анализатора."""

    recommend: bool
    level: RecommendationLevel | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    skip_reason: str | None = None
    snapshot: dict[str, object] = field(default_factory=dict)


# Допустимые состояния FSM, из которых имеет смысл рекомендовать включение
_RECOMMENDABLE_STATES = frozenset({"stop_sent", "disabled"})


def _latest_spend(metrics: list[MetricSnapshot]) -> Decimal:
    """Spend из самого свежего снимка.

    ad_metrics.spend — кумулятивный (нарастающий с начала cabinet-дня), поэтому
    суммировать снимки нельзя (раздуло бы значение в N раз). Берём последний
    снимок по cycle_ts; на паузе он держит финальное значение до сброса дня.
    """
    latest = _latest(metrics)
    if latest is None or latest.spend is None:
        return Decimal("0")
    return latest.spend


def _latest(metrics: list[MetricSnapshot]) -> MetricSnapshot | None:
    """Самая свежая метрика по cycle_ts."""
    if not metrics:
        return None
    return max(metrics, key=lambda m: m.cycle_ts)


def should_recommend(
    *,
    alert_state: str,
    snoozed_until: datetime | None,
    now: datetime,
    metrics: list[MetricSnapshot],
    offer: OfferThresholds | None,
    thresholds: AnalyzerThresholds = DEFAULT_THRESHOLDS,
) -> RecommendationDecision:
    """Главная функция анализатора.

    Args:
        alert_state: текущее значение ad_alert_state.alert_state.
        snoozed_until: дата окончания снуза (None = не снужено).
        now: текущее время (для проверки snooze).
        metrics: список MetricSnapshot после момента отключения (старшие сначала
            или новые — порядок не важен, мы сами агрегируем).
        offer: пороги оффера (cpa_threshold обязателен для большинства правил).
        thresholds: глобальные настройки анализатора.

    Returns:
        RecommendationDecision. Если recommend=False — в skip_reason причина.
    """
    if alert_state not in _RECOMMENDABLE_STATES:
        return RecommendationDecision(
            recommend=False,
            skip_reason=f"state={alert_state} вне рекомендуемых",
        )

    if snoozed_until is not None and snoozed_until > now:
        return RecommendationDecision(
            recommend=False,
            skip_reason="snoozed",
        )

    if len(metrics) < thresholds.min_metrics_required:
        return RecommendationDecision(
            recommend=False,
            skip_reason=f"мало метрик: {len(metrics)} < {thresholds.min_metrics_required}",
        )

    reasons: list[str] = []
    cpa = offer.cpa_threshold if offer else None

    total_spend = _latest_spend(metrics)
    latest = _latest(metrics)

    # Правило 1: текущий (последний кумулятивный) spend < (порог * share)
    if cpa is not None and cpa > 0:
        spend_cap = cpa * thresholds.spend_window_share_of_cpa
        if total_spend <= spend_cap:
            reasons.append(
                f"spend {total_spend} ≤ {spend_cap} ({thresholds.spend_window_share_of_cpa} × CPA={cpa})"
            )

    # Правило 2: cost_per_lead вернулся в норму
    if latest is not None and cpa is not None and latest.cost_per_lead is not None:
        if latest.cost_per_lead <= cpa:
            reasons.append(f"cost_per_lead={latest.cost_per_lead} ≤ CPA={cpa}")

    # Правило 3: cost_per_registration в норме
    if latest is not None and cpa is not None and latest.cost_per_registration is not None:
        if latest.cost_per_registration <= cpa:
            reasons.append(f"cost_per_registration={latest.cost_per_registration} ≤ CPA={cpa}")

    # Правило 4: появились deposits в последней метрике
    if latest is not None and (latest.deposits or 0) > 0:
        reasons.append(f"свежие deposits={latest.deposits}")

    if not reasons:
        return RecommendationDecision(
            recommend=False,
            skip_reason="ни одно положительное условие не выполнено",
            snapshot=_snapshot_summary(metrics, total_spend, latest),
        )

    level: RecommendationLevel = "ok" if len(reasons) >= 2 else "warning"
    return RecommendationDecision(
        recommend=True,
        level=level,
        reasons=tuple(reasons),
        snapshot=_snapshot_summary(metrics, total_spend, latest),
    )


def _snapshot_summary(
    metrics: list[MetricSnapshot],
    total_spend: Decimal,
    latest: MetricSnapshot | None,
) -> dict[str, object]:
    """Компактная сводка метрик для записи в enable_recommendations.snapshot_metrics."""
    summary: dict[str, object] = {
        "metrics_count": len(metrics),
        "total_spend": str(total_spend),
    }
    if latest is not None:
        summary["latest_cycle_ts"] = latest.cycle_ts.isoformat()
        if latest.cost_per_lead is not None:
            summary["latest_cost_per_lead"] = str(latest.cost_per_lead)
        if latest.cost_per_registration is not None:
            summary["latest_cost_per_registration"] = str(latest.cost_per_registration)
        if latest.deposits is not None:
            summary["latest_deposits"] = int(latest.deposits)
    return summary
