# -*- coding: utf-8 -*-
"""Движок правил: лесенка funnel-логики."""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from core.domain import AlertStage, EnableRecommendationLevel
from core.rules.labels import rule_label
from core.rules.types import RuleContext, RuleEvaluation, RuleHit
from core.scanner.models import ScannedAdRow

_HUNDRED = Decimal("100")
_MONEY_STEP = Decimal("0.01")
_PERCENT_STEP = Decimal("0.01")


def evaluate_stop_rules(row: ScannedAdRow, ctx: RuleContext) -> RuleEvaluation:
    """Оценивает объявление по последовательной лесенке + независимые правила.

    Независимые правила (frequency-anomaly) применяются поверх funnel-лесенки:
    если funnel ничего не нашёл, но frequency указывает на проблему — возвращаем
    соответствующий алерт. Если funnel нашёл STOP, а frequency — WARNING, то
    STOP имеет приоритет. STOP от frequency поднимает стадию до STOP в любом случае.
    """
    funnel_hit = _evaluate_funnel_ladder(row, ctx)
    freq_hit = _evaluate_frequency_anomaly(ctx)

    # Выбираем наивысший приоритет из двух источников
    final_hit = _pick_highest_priority_hit(funnel_hit, freq_hit)

    if final_hit is None:
        return RuleEvaluation(
            stage=None,
            warning_hits=(),
            stop_hits=(),
        )

    if final_hit.stage == AlertStage.STOP:
        return RuleEvaluation(
            stage=AlertStage.STOP,
            warning_hits=(),
            stop_hits=(final_hit,),
        )
    return RuleEvaluation(
        stage=AlertStage.WARNING,
        warning_hits=(final_hit,),
        stop_hits=(),
    )


def determine_enable_recommendation_level(
    row: ScannedAdRow,
    ctx: RuleContext,
    *,
    stop_evaluation: RuleEvaluation | None = None,
) -> EnableRecommendationLevel | None:
    """Возвращает безопасный уровень рекомендации на включение для OFF-объявления."""
    evaluation = stop_evaluation or evaluate_stop_rules(row, ctx)

    if _has_enable_data_gap(row):
        return None
    if evaluation.stage == AlertStage.STOP:
        return None
    if evaluation.stage == AlertStage.WARNING:
        return EnableRecommendationLevel.WARNING
    if not _has_safe_enable_recovery_signal(row):
        return None
    return EnableRecommendationLevel.OK


def _evaluate_funnel_ladder(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    if _has_confirmed_deposit_signal(row, ctx):
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
            base_stop_threshold=ctx.cpc_base_stop_threshold,
            stop_threshold=ctx.cpc_stop_threshold,
            warning_threshold=ctx.cpc_warning_threshold,
            code="cpc_stop",
            title=rule_label("cpc_stop"),
            label="CPC",
            missing_event_label="кликов",
        )

    return _pick_highest_priority_hit(
        _evaluate_metric_only(
            metric_value=row.cpc,
            enabled=ctx.cpc_enabled,
            base_stop_threshold=ctx.cpc_base_stop_threshold,
            stop_threshold=ctx.cpc_stop_threshold,
            warning_threshold=ctx.cpc_warning_threshold,
            code="cpc_stop",
            title=rule_label("cpc_stop"),
            label="CPC",
            stage_name="клика",
        ),
        _evaluate_guardrail_only(
            spend=row.spend,
            enabled=ctx.cpl_enabled,
            base_stop_threshold=ctx.cpl_base_stop_threshold,
            stop_threshold=ctx.cpl_stop_threshold,
            warning_threshold=ctx.cpl_warning_threshold,
            code="cpl_stop",
            title=rule_label("cpl_stop"),
            label="CPL",
            missing_event_label="лидов",
        ),
    )


def _evaluate_lead_stage(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    return _pick_highest_priority_hit(
        _evaluate_metric_only(
            metric_value=row.cost_per_lead,
            enabled=ctx.cpl_enabled,
            base_stop_threshold=ctx.cpl_base_stop_threshold,
            stop_threshold=ctx.cpl_stop_threshold,
            warning_threshold=ctx.cpl_warning_threshold,
            code="cpl_stop",
            title=rule_label("cpl_stop"),
            label="CPL",
            stage_name="лида",
        ),
        _evaluate_guardrail_only(
            spend=row.spend,
            enabled=ctx.cpr_enabled,
            base_stop_threshold=ctx.cpr_base_stop_threshold,
            stop_threshold=ctx.cpr_stop_threshold,
            warning_threshold=ctx.cpr_warning_threshold,
            code="cpr_stop",
            title=rule_label("cpr_stop"),
            label="CPR",
            missing_event_label="регистраций",
        ),
    )


def _evaluate_registration_stage(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    cpr_hit = _evaluate_metric_only(
        metric_value=row.cost_per_registration,
        enabled=ctx.cpr_enabled,
        base_stop_threshold=ctx.cpr_base_stop_threshold,
        stop_threshold=ctx.cpr_stop_threshold,
        warning_threshold=ctx.cpr_warning_threshold,
        code="cpr_stop",
        title=rule_label("cpr_stop"),
        label="CPR",
        stage_name="регистрации",
    )

    regs_without_dep_hit = _evaluate_regs_without_deposits(row, ctx)

    spend_without_dep_hit = None
    if _is_registration_normal(row, ctx):
        spend_without_dep_hit = _evaluate_spend_range(
            enabled=ctx.spend_no_dep_enabled,
            current_value=_ratio_percent(row.spend, ctx.cpa_amount),
            stop_from=ctx.spend_no_dep_from_percent,
            stop_to=ctx.spend_no_dep_to_percent,
            warning_pct=ctx.effective_cpr_warning_percent_of_stop,
            stop_percent_of_base=ctx.effective_cpr_stop_percent_of_base,
            code="spend_no_dep_range",
            title=rule_label("spend_no_dep_range"),
            summary_suffix="депозитов 0, цена реги в норме",
            reason_suffix="Цена регистрации ещё укладывается в рабочую зону, но депозитов всё ещё нет.",
        )

    return _pick_highest_priority_hit(
        cpr_hit,
        regs_without_dep_hit,
        spend_without_dep_hit,
    )


def _evaluate_deposit_stage(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    # Депозиты только из AdSet.pro (источник истины); Meta row.deposits не учитываем.
    total_deposits = ctx.external_deposits
    return _evaluate_spend_range(
        enabled=ctx.spend_with_dep_enabled,
        current_value=_ratio_percent(row.spend, ctx.cpa_amount),
        stop_from=ctx.spend_with_dep_from_percent,
        stop_to=ctx.spend_with_dep_to_percent,
        warning_pct=ctx.effective_cpr_warning_percent_of_stop,
        stop_percent_of_base=ctx.effective_cpr_stop_percent_of_base,
        code="spend_with_dep_range",
        title=rule_label("spend_with_dep_range"),
        summary_suffix=f"депозитов {total_deposits}",
        reason_suffix=(
            f"Депозит уже есть, но расход растёт до {total_deposits} депозита(ов) слишком быстро."
        ),
    )


def _evaluate_frequency_anomaly(ctx: RuleContext) -> RuleHit | None:
    """Правило 7: выгорание аудитории по резкому росту frequency.

    STOP: frequency > stop_threshold (например 3.5) — тяжёлый burnout.
    WARNING: frequency > warning_threshold (2.5) И
             (нет истории ИЛИ рост за час >= growth_warning_pct%).
    Без текущей frequency — правило не срабатывает.
    """
    if not ctx.frequency_anomaly_enabled:
        return None
    if ctx.frequency_current is None:
        return None

    current = ctx.frequency_current

    # Потолок-выброс: FB на старте может временно показывать frequency 50-100 из-за
    # крошечного reach (300 показов / 7 человек) — это переходный шум, не burnout.
    # Отсекаем ТОЛЬКО абсурдные выбросы выше cap. Ожидания показов/охвата убраны
    # (решение байера: стопать жёстко по порогу частоты, не ждать накопления данных).
    if current > ctx.frequency_outlier_cap:
        return None

    prev = ctx.frequency_1h_ago
    stop_thr = ctx.frequency_stop_threshold
    warn_thr = ctx.frequency_warning_threshold
    growth_pct = ctx.frequency_growth_warning_pct

    # STOP: абсолютный порог (без условия на рост)
    if current > stop_thr:
        if prev is not None and prev > 0:
            growth = ((current - prev) / prev * _HUNDRED).quantize(
                _PERCENT_STEP, rounding=ROUND_HALF_UP
            )
            reason = (
                f"Частота {current:.2f} (час назад {prev:.2f}, рост {growth:.0f}%) — "
                f"выгорание аудитории. Стоп-порог {stop_thr:.2f}."
            )
        else:
            reason = (
                f"Частота {current:.2f} превысила стоп-порог {stop_thr:.2f} — выгорание аудитории."
            )
        return RuleHit(
            code="frequency_anomaly",
            title=rule_label("frequency_anomaly"),
            stage=AlertStage.STOP,
            value=current,
            threshold=stop_thr,
            summary=f"Frequency {current:.2f} превысила стоп {stop_thr:.2f}",
            reason_text=reason,
        )

    # WARNING: абсолютный порог + условие на рост (или нет истории — WARNING по абсолюту)
    if current > warn_thr:
        if prev is None or prev <= 0:
            # Нет истории — WARNING только по абсолютному порогу
            return RuleHit(
                code="frequency_anomaly",
                title=rule_label("frequency_anomaly"),
                stage=AlertStage.WARNING,
                value=current,
                threshold=warn_thr,
                summary=f"Frequency {current:.2f} превысила порог {warn_thr:.2f}",
                reason_text=(
                    f"Частота {current:.2f} выше порога {warn_thr:.2f} — "
                    f"возможное выгорание аудитории (история не доступна)."
                ),
            )
        # Есть история — проверяем рост
        growth = ((current - prev) / prev * _HUNDRED).quantize(
            _PERCENT_STEP, rounding=ROUND_HALF_UP
        )
        if growth >= growth_pct:
            return RuleHit(
                code="frequency_anomaly",
                title=rule_label("frequency_anomaly"),
                stage=AlertStage.WARNING,
                value=current,
                threshold=warn_thr,
                summary=f"Frequency {current:.2f} (рост {growth:.0f}% за час) — выгорание",
                reason_text=(
                    f"Частота {current:.2f} (час назад {prev:.2f}, рост {growth:.0f}%) — "
                    f"выгорание аудитории. Порог роста {growth_pct:.0f}%."
                ),
            )

    return None


def _pick_highest_priority_hit(*hits: RuleHit | None) -> RuleHit | None:
    candidates = tuple(hit for hit in hits if hit is not None)
    if not candidates:
        return None

    for stage in (AlertStage.STOP, AlertStage.WARNING):
        for hit in candidates:
            if hit.stage == stage:
                return hit

    return None


def _evaluate_metric_only(
    *,
    metric_value: Decimal | None,
    enabled: bool,
    base_stop_threshold: Decimal,
    stop_threshold: Decimal,
    warning_threshold: Decimal,
    code: str,
    title: str,
    label: str,
    stage_name: str,
) -> RuleHit | None:
    if not enabled or metric_value is None:
        return None

    current = _round_money(metric_value)
    threshold_text = _format_threshold_value(stop_threshold, base_stop_threshold)

    if current >= stop_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.STOP,
            value=current,
            threshold=stop_threshold,
            summary=f"{label} {_format_money_value(current)} достиг или превысил стоп {threshold_text}",
            reason_text=(
                f"Цена {stage_name} достигла или вышла за допустимую границу: "
                f"{label} сейчас {_format_money_value(current)}. "
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
    base_stop_threshold: Decimal,
    stop_threshold: Decimal,
    warning_threshold: Decimal,
    code: str,
    title: str,
    label: str,
    missing_event_label: str,
) -> RuleHit | None:
    # Жёсткий стоп без ожидания показов/охвата (решение байера): расход без
    # события выше стоп-порога — money-сигнал, стопаем сразу, не ждём накопления
    # показов. Мизерный спенд сам отсекается порогом spend>=stop_threshold.
    if not enabled:
        return None

    current_spend = _round_money(spend)
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

    if current_spend >= warning_threshold:
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
    # Депозиты только из AdSet.pro: правило молчит, если внешний трекер видел депозит.
    if not ctx.regs_no_dep_enabled or ctx.external_deposits != 0:
        return None

    stop_val = Decimal(ctx.regs_no_dep_stop_count)
    warning_val = _warning_count(
        ctx.regs_no_dep_stop_count,
        ctx.effective_cpr_warning_percent_of_stop,
    )
    current = Decimal(row.registrations)

    if current >= stop_val:
        return RuleHit(
            code="regs_no_dep_stop",
            title=rule_label("regs_no_dep_stop"),
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
            title=rule_label("regs_no_dep_stop"),
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

    if current >= effective_from:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.STOP,
            value=current,
            threshold=_round_percent(effective_from),
            summary=f"Расход {current:.2f}% CPA достиг или превысил стоп-диапазон {range_text}, {summary_suffix}",
            reason_text=(
                f"Расход уже достиг или превысил стоп-диапазон {range_text}: сейчас {current:.2f}% от CPA. "
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
    """True если цена регистрации не выходит за стоп-порог CPR.

    КОНТРАКТ: ``ctx.cpr_stop_threshold`` — это ИТОГОВОЕ свёрнутое значение
    (cpa_amount × cpr_percent_stop% × cpr_stop_percent_of_base%), уже учтены
    все модификаторы из RuleContext.__post_init__. Здесь сравниваем напрямую,
    без повторного умножения на проценты — иначе получится double-fold
    (порог уехал бы в 0.8 от настоящего и нормальные регистрации
    ложно «выходили» бы из normal-зоны).
    """
    if row.registrations <= 0 or row.cost_per_registration is None:
        return False
    return _round_money(row.cost_per_registration) <= ctx.cpr_stop_threshold


def _has_enable_data_gap(row: ScannedAdRow) -> bool:
    if row.clicks > 0 and row.cpc is None:
        return True
    if row.leads > 0 and row.cost_per_lead is None:
        return True
    return row.registrations > 0 and row.cost_per_registration is None


def _has_confirmed_deposit_signal(row: ScannedAdRow, ctx: RuleContext) -> bool:
    """Депозит подтверждаем ТОЛЬКО по данным трекера AdSet.pro (external_deposits >= 1).

    Источник истины по депозитам — один: AdSet.pro (решение пользователя). Meta-видимые
    «депозиты» (row.deposits — из колонки «Результат» / pixel purchase) НЕ доверяем и в
    решениях по депозитам не учитываем. Следствие: объявление входит в deposit_stage (и под
    защиту от no-dep guardrail'ов) только когда AdSet.pro прислал depositное событие.
    """
    return ctx.external_deposits >= 1


def _has_safe_enable_recovery_signal(row: ScannedAdRow) -> bool:
    """Проверяет только неконсистентные сигналы; решение о resume идёт по метрикам."""
    if row.deposits > 0 and row.registrations <= 0:
        return False
    if (
        Decimal(row.spend) <= Decimal("0")
        and row.clicks <= 0
        and row.leads <= 0
        and row.registrations <= 0
        and row.deposits <= 0
    ):
        return False
    return True


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
    return f"{effective_from:.2f}-{effective_to:.2f}% (базовый {base_from:.2f}-{base_to:.2f}%)"


def _warning_count(stop_count: int, warning_pct: Decimal) -> Decimal:
    return (Decimal(stop_count) * Decimal(warning_pct) / _HUNDRED).quantize(
        Decimal("1"),
        rounding=ROUND_CEILING,
    )
