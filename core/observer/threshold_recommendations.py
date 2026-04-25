# -*- coding: utf-8 -*-
"""Рекомендации observer-порогов на основе исторических метрик."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import AdMetricHistory, FbAd, FbAdset, FbCampaign, Offer, OfferRuleConfig
from core.observer.thresholds import extract_observer_threshold_values
from core.settings_queries import get_observer_settings

_HUNDRED = Decimal("100")
_DEFAULT_RULE_PERCENTS = {
    "cpc": Decimal("2"),
    "cpl": Decimal("10"),
    "cpr": Decimal("20"),
}
_MIN_RECOMMENDED_STOP_PERCENT = Decimal("50")
_MAX_RECOMMENDED_STOP_PERCENT = Decimal("100")
_RECOMMENDATION_BUFFER_PERCENT = Decimal("10")
_ROUND_STEP = Decimal("5")
_VOLATILE_SPREAD_PERCENT = Decimal("25")


@dataclass(frozen=True, slots=True)
class ThresholdRecommendationStep:
    """Рекомендация по одному шагу воронки."""

    step_id: str
    code: str
    title: str
    sample_count: int
    confidence: str
    current_stop_percent: Decimal
    current_warning_percent: Decimal
    recommended_stop_percent: Decimal | None
    recommended_warning_percent: Decimal | None
    p50_ratio: Decimal | None
    p80_ratio: Decimal | None
    p90_ratio: Decimal | None
    reason: str
    can_apply: bool


@dataclass(frozen=True, slots=True)
class ThresholdRecommendations:
    """Полный ответ сервиса рекомендаций порогов."""

    generated_at: datetime
    since: datetime
    days: int
    min_samples: int
    steps: list[ThresholdRecommendationStep]


def _as_decimal(value: Any) -> Decimal | None:
    """Безопасно приводит значение к Decimal."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _round_to_step(value: Decimal, step: Decimal = _ROUND_STEP) -> Decimal:
    """Округляет процент к ближайшему шагу."""
    return (value / step).quantize(Decimal("1")) * step


def _clamp(value: Decimal, minimum: Decimal, maximum: Decimal) -> Decimal:
    """Ограничивает Decimal диапазоном."""
    return min(maximum, max(minimum, value))


def _nearest_rank(values: list[Decimal], percentile: Decimal) -> Decimal | None:
    """Считает percentile методом nearest-rank для устойчивого UI-результата."""
    if not values:
        return None
    ordered = sorted(values)
    rank = int(
        (percentile / _HUNDRED * Decimal(len(ordered))).to_integral_value(rounding=ROUND_CEILING)
    )
    index = max(0, min(len(ordered) - 1, rank - 1))
    return ordered[index].quantize(Decimal("0.01"))


def _format_ratio(value: Decimal | None) -> str:
    """Форматирует процентное отношение для текста рекомендации."""
    if value is None:
        return "нет данных"
    return f"{value.quantize(Decimal('1'))}%"


def build_threshold_recommendation_step(
    *,
    step_id: str,
    code: str,
    title: str,
    ratios: list[Decimal],
    current_stop_percent: Decimal,
    current_warning_percent: Decimal,
    min_samples: int,
) -> ThresholdRecommendationStep:
    """Строит рекомендацию по готовым ratio-замерам."""
    sample_count = len(ratios)
    if sample_count < min_samples:
        return ThresholdRecommendationStep(
            step_id=step_id,
            code=code,
            title=title,
            sample_count=sample_count,
            confidence="LOW",
            current_stop_percent=current_stop_percent,
            current_warning_percent=current_warning_percent,
            recommended_stop_percent=None,
            recommended_warning_percent=None,
            p50_ratio=None,
            p80_ratio=None,
            p90_ratio=None,
            reason=(
                f"Недостаточно истории: нужно минимум {min_samples} замеров, сейчас {sample_count}."
            ),
            can_apply=False,
        )

    p50 = _nearest_rank(ratios, Decimal("50"))
    p80 = _nearest_rank(ratios, Decimal("80"))
    p90 = _nearest_rank(ratios, Decimal("90"))
    assert p50 is not None and p80 is not None and p90 is not None

    raw_stop = p80 + _RECOMMENDATION_BUFFER_PERCENT
    recommended_stop = _clamp(
        _round_to_step(raw_stop),
        _MIN_RECOMMENDED_STOP_PERCENT,
        _MAX_RECOMMENDED_STOP_PERCENT,
    )
    spread = p90 - p50
    recommended_warning = Decimal("75") if spread >= _VOLATILE_SPREAD_PERCENT else Decimal("80")

    stop_changed = abs(recommended_stop - current_stop_percent) >= _ROUND_STEP
    warning_changed = recommended_warning != current_warning_percent
    can_apply = stop_changed or warning_changed
    confidence = "HIGH" if sample_count >= min_samples * 3 else "MEDIUM"

    if recommended_stop < current_stop_percent:
        direction = "история советует останавливать раньше"
    elif recommended_stop > current_stop_percent:
        direction = "история допускает более поздний стоп"
    else:
        direction = "текущий стоп близок к истории"

    reason = (
        f"{direction}: 80-й перцентиль фактической стоимости — {_format_ratio(p80)} "
        f"от базового лимита, запас учтён до {recommended_stop}%."
    )
    if spread >= _VOLATILE_SPREAD_PERCENT:
        reason += " Разброс высокий, предупреждение ставится раньше."

    return ThresholdRecommendationStep(
        step_id=step_id,
        code=code,
        title=title,
        sample_count=sample_count,
        confidence=confidence,
        current_stop_percent=current_stop_percent,
        current_warning_percent=current_warning_percent,
        recommended_stop_percent=recommended_stop,
        recommended_warning_percent=recommended_warning,
        p50_ratio=p50,
        p80_ratio=p80,
        p90_ratio=p90,
        reason=reason,
        can_apply=can_apply,
    )


def _append_ratio(
    ratios: dict[str, list[Decimal]],
    *,
    step_id: str,
    value: Any,
    base_percent: Any,
    cpa_amount: Any,
) -> None:
    """Добавляет ratio фактической стоимости к базовому лимиту правила."""
    metric_value = _as_decimal(value)
    rule_percent = _as_decimal(base_percent) or _DEFAULT_RULE_PERCENTS[step_id]
    cpa = _as_decimal(cpa_amount)
    if metric_value is None or cpa is None or metric_value <= 0 or cpa <= 0 or rule_percent <= 0:
        return

    base_limit = (cpa * rule_percent) / _HUNDRED
    if base_limit <= 0:
        return

    ratio = (metric_value / base_limit) * _HUNDRED
    if Decimal("0") < ratio <= Decimal("200"):
        ratios[step_id].append(ratio)


async def collect_threshold_recommendations(
    session: AsyncSession,
    *,
    days: int = 14,
    min_samples: int = 10,
) -> ThresholdRecommendations:
    """Собирает рекомендации порогов из истории метрик."""
    now = datetime.now(UTC)
    since = now - timedelta(days=days)
    settings = await get_observer_settings(session)
    current = extract_observer_threshold_values(settings)
    ratios: dict[str, list[Decimal]] = {"cpc": [], "cpl": [], "cpr": []}

    result = await session.execute(
        select(
            AdMetricHistory.cpc,
            AdMetricHistory.cost_per_lead,
            AdMetricHistory.cost_per_registration,
            AdMetricHistory.clicks,
            AdMetricHistory.leads,
            AdMetricHistory.registrations,
            Offer.cpa_amount,
            OfferRuleConfig.cpc_percent_enabled,
            OfferRuleConfig.cpc_percent_stop,
            OfferRuleConfig.cpl_percent_enabled,
            OfferRuleConfig.cpl_percent_stop,
            OfferRuleConfig.cpr_percent_enabled,
            OfferRuleConfig.cpr_percent_stop,
        )
        .select_from(AdMetricHistory)
        .join(FbAd, AdMetricHistory.ad_id == FbAd.id)
        .join(FbAdset, FbAd.adset_id == FbAdset.id)
        .join(FbCampaign, FbAdset.campaign_id == FbCampaign.id)
        .join(Offer, FbCampaign.offer_id == Offer.id)
        .join(OfferRuleConfig, OfferRuleConfig.offer_id == Offer.id, isouter=True)
        .where(AdMetricHistory.cycle_ts >= since, Offer.is_active.is_(True))
    )

    for row in result.all():
        if row.clicks and row.cpc_percent_enabled is not False:
            _append_ratio(
                ratios,
                step_id="cpc",
                value=row.cpc,
                base_percent=row.cpc_percent_stop,
                cpa_amount=row.cpa_amount,
            )
        if row.leads and row.cpl_percent_enabled is not False:
            _append_ratio(
                ratios,
                step_id="cpl",
                value=row.cost_per_lead,
                base_percent=row.cpl_percent_stop,
                cpa_amount=row.cpa_amount,
            )
        if row.registrations and row.cpr_percent_enabled is not False:
            _append_ratio(
                ratios,
                step_id="cpr",
                value=row.cost_per_registration,
                base_percent=row.cpr_percent_stop,
                cpa_amount=row.cpa_amount,
            )

    steps = [
        build_threshold_recommendation_step(
            step_id="cpc",
            code="CPC",
            title="Клик",
            ratios=ratios["cpc"],
            current_stop_percent=current["cpc_stop_percent_of_base"],
            current_warning_percent=current["cpc_warning_percent_of_stop"],
            min_samples=min_samples,
        ),
        build_threshold_recommendation_step(
            step_id="cpl",
            code="CPL",
            title="Лид",
            ratios=ratios["cpl"],
            current_stop_percent=current["cpl_stop_percent_of_base"],
            current_warning_percent=current["cpl_warning_percent_of_stop"],
            min_samples=min_samples,
        ),
        build_threshold_recommendation_step(
            step_id="cpr",
            code="CPR",
            title="Регистрация",
            ratios=ratios["cpr"],
            current_stop_percent=current["cpr_stop_percent_of_base"],
            current_warning_percent=current["cpr_warning_percent_of_stop"],
            min_samples=min_samples,
        ),
    ]
    return ThresholdRecommendations(
        generated_at=now,
        since=since,
        days=days,
        min_samples=min_samples,
        steps=steps,
    )
