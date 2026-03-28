# -*- coding: utf-8 -*-
"""Движок правил: лесенка funnel-логики и ранние сигналы по post-click метрикам."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from core.domain import AlertStage
from core.rules.types import RuleContext, RuleEvaluation, RuleHit
from core.scanner.models import ScannedAdRow

_HUNDRED = Decimal("100")
_MONEY_STEP = Decimal("0.01")
_PERCENT_STEP = Decimal("0.01")


def evaluate_stop_rules(row: ScannedAdRow, ctx: RuleContext) -> RuleEvaluation:
    """Оценивает объявление по последовательной лесенке."""

    hit = _evaluate_funnel_ladder(row, ctx)
    if hit is None and row.leads == 0 and row.registrations == 0 and row.deposits == 0:
        hit = _evaluate_early_signals(row, ctx)

    if hit is None:
        return RuleEvaluation(
            stage=None,
            early_signal_hits=(),
            warning_hits=(),
            stop_hits=(),
        )

    if hit.stage == AlertStage.STOP:
        return RuleEvaluation(
            stage=AlertStage.STOP,
            early_signal_hits=(),
            warning_hits=(),
            stop_hits=(hit,),
        )
    if hit.stage == AlertStage.WARNING:
        return RuleEvaluation(
            stage=AlertStage.WARNING,
            early_signal_hits=(),
            warning_hits=(hit,),
            stop_hits=(),
        )
    return RuleEvaluation(
        stage=AlertStage.EARLY_SIGNAL,
        early_signal_hits=(hit,),
        warning_hits=(),
        stop_hits=(),
    )


def _evaluate_funnel_ladder(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    if row.deposits >= 1:
        return _evaluate_deposit_stage(row, ctx)
    if row.registrations >= 1:
        return _evaluate_registration_stage(row, ctx)
    if row.leads >= 1:
        return _evaluate_lead_stage(row, ctx)
    return _evaluate_click_stage(row, ctx)


def _evaluate_click_stage(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    if row.clicks == 0:
        return _evaluate_guardrail_only(
            spend=row.spend,
            enabled=ctx.cpc_enabled,
            stop_percent=ctx.cpc_percent_stop,
            cpa_amount=ctx.cpa_amount,
            warning_pct=ctx.warning_percent_of_stop,
            stop_percent_of_base=ctx.stop_percent_of_base,
            code="cpc_stop",
            title="Дорогой клик",
            label="CPC",
            missing_event_label="кликов",
        )

    hit = _evaluate_metric_only(
        metric_value=row.cpc,
        enabled=ctx.cpc_enabled,
        stop_percent=ctx.cpc_percent_stop,
        cpa_amount=ctx.cpa_amount,
        warning_pct=ctx.warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="cpc_stop",
        title="Дорогой клик",
        label="CPC",
        stage_name="клика",
    )
    if hit is not None:
        return hit

    return _evaluate_guardrail_only(
        spend=row.spend,
        enabled=ctx.cpl_enabled,
        stop_percent=ctx.cpl_percent_stop,
        cpa_amount=ctx.cpa_amount,
        warning_pct=ctx.warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="cpl_stop",
        title="Дорогой лид",
        label="CPL",
        missing_event_label="лидов",
    )


def _evaluate_lead_stage(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    hit = _evaluate_metric_only(
        metric_value=row.cost_per_lead,
        enabled=ctx.cpl_enabled,
        stop_percent=ctx.cpl_percent_stop,
        cpa_amount=ctx.cpa_amount,
        warning_pct=ctx.warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="cpl_stop",
        title="Дорогой лид",
        label="CPL",
        stage_name="лида",
    )
    if hit is not None:
        return hit

    return _evaluate_guardrail_only(
        spend=row.spend,
        enabled=ctx.cpr_enabled,
        stop_percent=ctx.cpr_percent_stop,
        cpa_amount=ctx.cpa_amount,
        warning_pct=ctx.warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="cpr_stop",
        title="Дорогая рега",
        label="CPR",
        missing_event_label="регистраций",
    )


def _evaluate_registration_stage(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    hit = _evaluate_metric_only(
        metric_value=row.cost_per_registration,
        enabled=ctx.cpr_enabled,
        stop_percent=ctx.cpr_percent_stop,
        cpa_amount=ctx.cpa_amount,
        warning_pct=ctx.warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="cpr_stop",
        title="Дорогая рега",
        label="CPR",
        stage_name="регистрации",
    )
    if hit is not None:
        return hit

    hit = _evaluate_regs_without_deposits(row, ctx)
    if hit is not None:
        return hit

    registration_normal = _is_registration_normal(row, ctx)
    if not registration_normal:
        return None

    return _evaluate_spend_range(
        enabled=ctx.spend_no_dep_enabled,
        current_value=_ratio_percent(row.spend, ctx.cpa_amount),
        stop_from=ctx.spend_no_dep_from_percent,
        stop_to=ctx.spend_no_dep_to_percent,
        warning_pct=ctx.warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="spend_no_dep_range",
        title="Расход без депа",
        summary_suffix="депозитов 0, цена реги в норме",
        reason_suffix="Цена регистрации ещё укладывается в рабочую зону, но депозитов всё ещё нет.",
    )


def _evaluate_deposit_stage(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    return _evaluate_spend_range(
        enabled=ctx.spend_with_dep_enabled,
        current_value=_ratio_percent(row.spend, ctx.cpa_amount),
        stop_from=ctx.spend_with_dep_from_percent,
        stop_to=ctx.spend_with_dep_to_percent,
        warning_pct=ctx.warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="spend_with_dep_range",
        title="Расход с депозитом",
        summary_suffix=f"депозитов {row.deposits}",
        reason_suffix=f"Депозит уже есть, но расход растёт до {row.deposits} депозита(ов) слишком быстро.",
    )


def _evaluate_early_signals(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    spend_percent = _ratio_percent(row.spend, ctx.cpa_amount)

    if (
        ctx.early_outbound_ctr_signal_enabled
        and spend_percent >= ctx.early_outbound_ctr_signal_min_spend_percent
        and row.outbound_ctr is not None
    ):
        current = _round_percent(row.outbound_ctr)
        threshold = _round_percent(ctx.early_outbound_ctr_signal_min_percent)
        if current < threshold:
            return RuleHit(
                code="early_outbound_ctr_signal",
                title="Слабый CTR исходящих кликов",
                stage=AlertStage.EARLY_SIGNAL,
                value=current,
                threshold=threshold,
                summary=f"CTR исходящих кликов {_format_percent_value(current)} < минимум {_format_percent_value(threshold)}",
                reason_text=(
                    f"Исходящий CTR слишком слабый для ранней стадии: сейчас {_format_percent_value(current)} "
                    f"при расходе {_format_money_value(row.spend)}. "
                    f"Минимум для сигнала {_format_percent_value(threshold)}, значит объявление слабо выбивает переходы на сайт."
                ),
            )

    if (
        ctx.early_lpv_ratio_signal_enabled
        and row.outbound_clicks >= ctx.early_lpv_ratio_signal_min_outbound_clicks
        and row.landing_page_views >= 0
    ):
        ratio = _landing_page_view_ratio(row)
        if ratio is not None:
            current = _round_percent(ratio)
            threshold = _round_percent(ctx.early_lpv_ratio_signal_min_percent)
            if current < threshold:
                return RuleHit(
                    code="early_lpv_ratio_signal",
                    title="Слабая доходимость до лендинга",
                    stage=AlertStage.EARLY_SIGNAL,
                    value=current,
                    threshold=threshold,
                    summary=f"Доля LPV {_format_percent_value(current)} < минимум {_format_percent_value(threshold)}",
                    reason_text=(
                        f"Переходы теряются между кликом и загрузкой страницы: доля LPV сейчас {_format_percent_value(current)} "
                        f"при {row.outbound_clicks} исходящих кликах. "
                        f"Минимум для сигнала {_format_percent_value(threshold)}, значит часть трафика не доходит до лендинга."
                    ),
                )

    if (
        ctx.early_cost_per_lpv_signal_enabled
        and row.landing_page_views >= ctx.early_cost_per_lpv_signal_min_views
        and row.cost_per_landing_page_view is not None
    ):
        current = _round_money(row.cost_per_landing_page_view)
        threshold = _round_money(
            _percent_of_cpa(ctx.cpa_amount, ctx.early_cost_per_lpv_signal_percent_of_cpa)
        )
        if current > threshold:
            return RuleHit(
                code="early_cost_per_lpv_signal",
                title="Дорогой просмотр лендинга",
                stage=AlertStage.EARLY_SIGNAL,
                value=current,
                threshold=threshold,
                summary=f"Цена LPV {_format_money_value(current)} > лимит {_format_money_value(threshold)}",
                reason_text=(
                    f"Просмотр лендинга обходится слишком дорого для ранней стадии: сейчас {_format_money_value(current)} "
                    f"при {row.landing_page_views} просмотрах страницы. "
                    f"Лимит для сигнала {_format_money_value(threshold)}, значит экономика проседает ещё до лида."
                ),
            )

    return None


def _evaluate_metric_only(
    *,
    metric_value: Decimal | None,
    enabled: bool,
    stop_percent: Decimal,
    cpa_amount: Decimal,
    warning_pct: Decimal,
    stop_percent_of_base: Decimal,
    code: str,
    title: str,
    label: str,
    stage_name: str,
) -> RuleHit | None:
    if not enabled or metric_value is None:
        return None

    current = _round_money(metric_value)
    base_stop_threshold = _round_money(_percent_of_cpa(cpa_amount, stop_percent))
    stop_threshold = _round_money(_apply_downward_stop(base_stop_threshold, stop_percent_of_base))
    warning_threshold = _round_money(_warning_threshold(stop_threshold, warning_pct))
    threshold_text = _format_threshold_value(stop_threshold, base_stop_threshold)

    if current > stop_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.STOP,
            value=current,
            threshold=stop_threshold,
            summary=f"{label} {_format_money_value(current)} > стоп {threshold_text}",
            reason_text=(
                f"Цена {stage_name} вышла за допустимую границу: {label} сейчас {_format_money_value(current)}. "
                f"Стоп для этого правила {threshold_text}."
            ),
        )

    if current >= warning_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.WARNING,
            value=current,
            threshold=warning_threshold,
            summary=f"{label} {_format_money_value(current)} приближается к стопу {threshold_text}",
            reason_text=(
                f"Цена {stage_name} уже подходит к критической зоне: {label} сейчас {_format_money_value(current)}. "
                f"Стоп для этого правила {threshold_text}, запас почти исчерпан."
            ),
        )

    return None


def _evaluate_guardrail_only(
    *,
    spend: Decimal,
    enabled: bool,
    stop_percent: Decimal,
    cpa_amount: Decimal,
    warning_pct: Decimal,
    stop_percent_of_base: Decimal,
    code: str,
    title: str,
    label: str,
    missing_event_label: str,
) -> RuleHit | None:
    if not enabled:
        return None

    current_spend = _round_money(spend)
    base_stop_threshold = _round_money(_percent_of_cpa(cpa_amount, stop_percent))
    stop_threshold = _round_money(_apply_downward_stop(base_stop_threshold, stop_percent_of_base))
    warning_threshold = _round_money(_warning_threshold(stop_threshold, warning_pct))
    threshold_text = _format_threshold_value(stop_threshold, base_stop_threshold)

    if current_spend >= stop_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.STOP,
            value=current_spend,
            threshold=stop_threshold,
            summary=f"Расход {_format_money_value(current_spend)} превысил стоп {label} {threshold_text} без {missing_event_label}",
            reason_text=(
                f"Расход уже вышел за границу следующей ступени, хотя {missing_event_label} ещё нет: "
                f"потрачено {_format_money_value(current_spend)}. "
                f"Стоп для {label} {threshold_text}, поэтому следующий шаг воронки уже будет слишком дорогим."
            ),
        )

    if current_spend > warning_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.WARNING,
            value=current_spend,
            threshold=warning_threshold,
            summary=f"Расход {_format_money_value(current_spend)} приближается к стопу {label} {threshold_text} без {missing_event_label}",
            reason_text=(
                f"Расход подошёл слишком близко к следующей ступени воронки, хотя {missing_event_label} ещё нет: "
                f"потрачено {_format_money_value(current_spend)}. "
                f"Стоп для {label} {threshold_text}, запас по экономике почти закончился."
            ),
        )

    return None


def _evaluate_regs_without_deposits(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    if not ctx.regs_no_dep_enabled or row.deposits != 0:
        return None

    stop_val = Decimal(ctx.regs_no_dep_stop_count)
    warning_val = _warning_count(ctx.regs_no_dep_stop_count, ctx.warning_percent_of_stop)
    current = Decimal(row.registrations)

    if current >= stop_val:
        return RuleHit(
            code="regs_no_dep_stop",
            title="Реги без депозитов",
            stage=AlertStage.STOP,
            value=current,
            threshold=stop_val,
            summary=f"Регистраций {row.registrations}, депозитов 0 (стоп от {ctx.regs_no_dep_stop_count})",
            reason_text=(
                f"Объявление накопило {row.registrations} регистраций, но не дало ни одного депозита. "
                f"Стоп по этому правилу начинается с {ctx.regs_no_dep_stop_count} регистраций без депа."
            ),
        )

    if current >= warning_val:
        return RuleHit(
            code="regs_no_dep_stop",
            title="Реги без депозитов",
            stage=AlertStage.WARNING,
            value=current,
            threshold=warning_val,
            summary=f"Регистраций {row.registrations}, депозитов 0 — до стопа мало",
            reason_text=(
                f"Регистрации уже накапливаются без депозитов: сейчас {row.registrations} рег(ов) и 0 депов. "
                f"Стоп начнётся с {ctx.regs_no_dep_stop_count} регистраций без депозита."
            ),
        )

    return None


def _evaluate_spend_range(
    *,
    enabled: bool,
    current_value: Decimal,
    stop_from: Decimal,
    stop_to: Decimal,
    warning_pct: Decimal,
    stop_percent_of_base: Decimal,
    code: str,
    title: str,
    summary_suffix: str,
    reason_suffix: str,
) -> RuleHit | None:
    if not enabled:
        return None

    effective_from = _apply_downward_stop(stop_from, stop_percent_of_base)
    effective_to = _apply_downward_stop(stop_to, stop_percent_of_base)
    warning_from = _warning_threshold(effective_from, warning_pct)
    range_text = _format_percent_range(effective_from, effective_to, stop_from, stop_to)
    current = _round_percent(current_value)

    if effective_from <= current <= effective_to:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.STOP,
            value=current,
            threshold=_round_percent(effective_from),
            summary=f"Расход {current:.2f}% CPA вошёл в стоп-диапазон {range_text}, {summary_suffix}",
            reason_text=(
                f"Расход уже вошёл в стоп-диапазон {range_text}: сейчас {current:.2f}% от CPA. "
                f"{reason_suffix}"
            ),
        )

    if warning_from <= current < effective_from:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.WARNING,
            value=current,
            threshold=_round_percent(warning_from),
            summary=f"Расход {current:.2f}% CPA приближается к стоп-диапазону {range_text}, {summary_suffix}",
            reason_text=(
                f"Расход подходит к стоп-диапазону {range_text}: сейчас {current:.2f}% от CPA. "
                f"{reason_suffix}"
            ),
        )

    return None


def _is_registration_normal(row: ScannedAdRow, ctx: RuleContext) -> bool:
    if row.registrations <= 0 or row.cost_per_registration is None:
        return False

    cpr_threshold = _round_money(
        _apply_downward_stop(
            _percent_of_cpa(ctx.cpa_amount, ctx.cpr_percent_stop),
            ctx.stop_percent_of_base,
        )
    )
    return _round_money(row.cost_per_registration) <= cpr_threshold


def _landing_page_view_ratio(row: ScannedAdRow) -> Decimal | None:
    if row.outbound_clicks <= 0:
        return None
    return (Decimal(row.landing_page_views) / Decimal(row.outbound_clicks)) * _HUNDRED


def _percent_of_cpa(cpa: Decimal, percent: Decimal) -> Decimal:
    return (Decimal(cpa) * Decimal(percent)) / _HUNDRED


def _ratio_percent(value: Decimal, total: Decimal) -> Decimal:
    if Decimal(total) <= 0:
        return Decimal("0")
    return (Decimal(value) / Decimal(total)) * _HUNDRED


def _warning_threshold(stop_threshold: Decimal, warning_pct: Decimal) -> Decimal:
    return (Decimal(stop_threshold) * Decimal(warning_pct)) / _HUNDRED


def _apply_downward_stop(base_value: Decimal, stop_percent_of_base: Decimal) -> Decimal:
    return (Decimal(base_value) * Decimal(stop_percent_of_base)) / _HUNDRED


def _round_money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def _round_percent(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_PERCENT_STEP, rounding=ROUND_HALF_UP)


def _format_money_value(value: Decimal) -> str:
    return f"{_round_money(value):.2f}"


def _format_percent_value(value: Decimal) -> str:
    return f"{_round_percent(value):.2f}%"


def _format_threshold_value(effective_value: Decimal, base_value: Decimal) -> str:
    if Decimal(effective_value) == Decimal(base_value):
        return _format_money_value(effective_value)
    return f"{_format_money_value(effective_value)} (базовый {_format_money_value(base_value)})"


def _format_percent_range(
    effective_from: Decimal,
    effective_to: Decimal,
    base_from: Decimal,
    base_to: Decimal,
) -> str:
    if Decimal(effective_from) == Decimal(base_from) and Decimal(effective_to) == Decimal(base_to):
        return f"{effective_from:.2f}-{effective_to:.2f}%"
    return (
        f"{effective_from:.2f}-{effective_to:.2f}% "
        f"(базовый {base_from:.2f}-{base_to:.2f}%)"
    )


def _warning_count(stop_count: int, warning_pct: Decimal) -> Decimal:
    return (Decimal(stop_count) * Decimal(warning_pct) / _HUNDRED).quantize(
        Decimal("1"),
        rounding=ROUND_CEILING,
    )
