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
   - есть подтверждённая воронка (registrations >= 1 и deposits >= 1).

`level`:
- "ok"      — выполнились ≥ 2 положительных условий.
- "warning" — выполнилось ровно одно условие (стоит обратить внимание, но не уверены).
- None      — рекомендовать не стоит.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from core.domain import EnableRecommendationLevel
from core.observer.pipeline import build_rule_context
from core.observer.queries import OfferRules
from core.rules.evaluator import determine_enable_recommendation_level, evaluate_stop_rules
from core.scanner.models import ScannedAdRow

RecommendationLevel = Literal["ok", "warning"]


@dataclass(frozen=True)
class AnalyzerThresholds:
    """Глобальные настройки анализатора."""

    spend_window_share_of_cpa: Decimal = Decimal("0.5")
    min_metrics_required: int = 1
    # Кейс куратора: «показов мало + CTR хороший» → включить и держать до цены лида.
    curator_impr_ceiling: int = 500
    curator_ctr_floor: Decimal = Decimal("3.0")  # проценты, как ad_metrics.ctr
    # Ревью M-1: grace ОБЯЗАН иметь денежную границу. Если у оффера нет
    # cpa_threshold — кап берётся отсюда, а не остаётся безлимитным на всё окно.
    curator_fallback_spend_cap: Decimal = Decimal("10.00")


DEFAULT_THRESHOLDS = AnalyzerThresholds()


@dataclass(frozen=True)
class OfferThresholds:
    """Пороги оффера для конкретного объявления."""

    cpa_threshold: Decimal | None = None
    frequency_threshold: Decimal | None = None
    stop_percent_of_rule: Decimal | None = None
    warning_percent_of_stop: Decimal | None = None


@dataclass(frozen=True)
class MetricSnapshot:
    """Снимок одной метрики из ad_metrics (нужны только используемые поля)."""

    cycle_ts: datetime
    spend: Decimal | None = None
    cost_per_lead: Decimal | None = None
    cost_per_registration: Decimal | None = None
    registrations: int | None = None
    deposits: int | None = None
    leads: int | None = None
    clicks: int | None = None
    reach: int | None = None
    # Кейс куратора: показы кумулятивны в cabinet-дне (как spend) — берём latest.
    impressions: int | None = None
    ctr: Decimal | None = None  # проценты (3.7 = 3.7%)
    cpc: Decimal | None = None
    frequency: Decimal | None = None


@dataclass(frozen=True)
class RecommendationDecision:
    """Решение анализатора."""

    recommend: bool
    level: RecommendationLevel | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)
    skip_reason: str | None = None
    snapshot: dict[str, object] = field(default_factory=dict)
    # Кейс куратора: включить и ДЕРЖАТЬ до цены лида (grace-окно после включения).
    hold_until_cpl: bool = False


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
    allow_curator: bool = True,
    tracker_registrations: int = 0,
    tracker_confirmed_deposits: int = 0,
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
        allow_curator: разрешена ли curator-ветка для этого кандидата.

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

    cpa = offer.cpa_threshold if offer else None

    total_spend = _latest_spend(metrics)
    latest = _latest(metrics)

    # --- Кейс куратора (отдельная ветка, НЕ смешивается с recovery-сигналами) ---
    # Показов мало при хорошем CTR: данных для вердикта недостаточно, ранний стоп
    # мог убить потенциального виннера. Рекомендуем включить и держать до ~1×CPA
    # спенда — судить по реальной цене лида (правило байера/куратора).
    if (
        allow_curator
        and latest is not None
        and latest.impressions is not None
        and latest.impressions < thresholds.curator_impr_ceiling
        and latest.ctr is not None
        and latest.ctr >= thresholds.curator_ctr_floor
    ):
        snapshot = _snapshot_summary(metrics, total_spend, latest)
        snapshot["hold_until_cpl"] = True
        # Денежная граница grace всегда есть: 1×CPA оффера, а без CPA — фолбэк (M-1).
        cap = cpa if (cpa is not None and cpa > 0) else thresholds.curator_fallback_spend_cap
        snapshot["grace_spend_cap"] = str(cap)
        return RecommendationDecision(
            recommend=True,
            level="warning",
            hold_until_cpl=True,
            reasons=(
                f"показов мало ({latest.impressions} < {thresholds.curator_impr_ceiling}) "
                f"при хорошем CTR ({latest.ctr}% ≥ {thresholds.curator_ctr_floor}%) — "
                f"дать открутить до ~1×CPA и судить по цене лида",
            ),
            snapshot=snapshot,
        )

    if latest is None:
        return RecommendationDecision(
            recommend=False,
            skip_reason="нет свежего снимка для канонической проверки",
            snapshot=_snapshot_summary(metrics, total_spend, latest),
        )

    if cpa is None or cpa <= 0:
        return RecommendationDecision(
            recommend=False,
            skip_reason="у оффера не задан CPA для канонической проверки",
            snapshot=_snapshot_summary(metrics, total_spend, latest),
        )

    tracker_registrations = max(tracker_registrations, int(latest.registrations or 0))
    tracker_confirmed_deposits = max(
        tracker_confirmed_deposits,
        int(latest.deposits or 0) if tracker_registrations > 0 else 0,
    )
    offer_rules = OfferRules(
        offer_id=uuid.uuid4(),
        code="enable-recovery",
        name="enable-recovery",
        cpa_threshold=cpa,
        frequency_threshold=offer.frequency_threshold if offer else None,
        stop_percent_of_rule=offer.stop_percent_of_rule if offer else None,
        warning_percent_of_stop=offer.warning_percent_of_stop if offer else None,
    )
    row = ScannedAdRow(
        fb_ad_id="enable-recovery",
        campaign_name="",
        adset_name="",
        ad_name="",
        delivery_status="OFF",
        spend=Decimal(latest.spend or 0),
        reach=int(latest.reach or 0),
        impressions=int(latest.impressions or 0),
        clicks=int(latest.clicks or 0),
        cpc=latest.cpc,
        ctr=latest.ctr,
        frequency=latest.frequency,
        leads=int(latest.leads or 0),
        cost_per_lead=latest.cost_per_lead,
        registrations=tracker_registrations,
        cost_per_registration=latest.cost_per_registration,
        deposits=tracker_confirmed_deposits,
    )
    ctx = build_rule_context(
        offer_rules,
        external_deposits=tracker_confirmed_deposits,
        frequency_current=latest.frequency,
        impressions=latest.impressions,
        reach=latest.reach,
    )
    evaluation = evaluate_stop_rules(row, ctx)
    canonical_level = determine_enable_recommendation_level(
        row,
        ctx,
        stop_evaluation=evaluation,
    )
    snapshot = _snapshot_summary(metrics, total_spend, latest)
    snapshot["canonical_rule_stage"] = evaluation.stage.value if evaluation.stage else None
    snapshot["canonical_rule_codes"] = evaluation.matched_rule_codes
    snapshot["tracker_registrations"] = tracker_registrations
    snapshot["tracker_confirmed_deposits"] = tracker_confirmed_deposits
    if canonical_level is None:
        return RecommendationDecision(
            recommend=False,
            skip_reason=evaluation.reason_text or "канонический evaluator не разрешил включение",
            snapshot=snapshot,
        )
    level: RecommendationLevel = (
        "ok" if canonical_level == EnableRecommendationLevel.OK else "warning"
    )
    reason = evaluation.reason_text or "Канонические стоп-правила больше не срабатывают"
    return RecommendationDecision(
        recommend=True,
        level=level,
        reasons=(reason,),
        snapshot=snapshot,
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
        if latest.registrations is not None:
            summary["latest_registrations"] = int(latest.registrations)
        if latest.deposits is not None:
            summary["latest_deposits"] = int(latest.deposits)
        if latest.clicks is not None:
            summary["latest_clicks"] = int(latest.clicks)
        if latest.leads is not None:
            summary["latest_leads"] = int(latest.leads)
    return summary
