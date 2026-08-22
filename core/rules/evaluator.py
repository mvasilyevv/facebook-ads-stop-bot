# -*- coding: utf-8 -*-
"""Движок правил: лесенка funnel-логики."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from core.domain import AlertStage
from core.money import require_exact_currency_amount
from core.rules.labels import rule_label, rule_metric_label
from core.rules.types import RuleContext, RuleEvaluation, RuleHit
from core.scanner.models import ScannedAdRow
from core.wording import deposits_ru, registrations_ru

_HUNDRED = Decimal("100")
_PERCENT_STEP = Decimal("0.01")

# M-10: множитель для эффективного потолка-выброса частоты относительно стоп-порога.
# «Абсурдный выброс» = кратно (×3) выше того burnout'а, на который настроен стоп.
_FREQUENCY_CAP_MULTIPLIER = Decimal("3")


def evaluate_stop_rules(row: ScannedAdRow, ctx: RuleContext) -> RuleEvaluation:
    """Оценивает объявление по последовательной лесенке + независимые правила.

    Независимые правила (frequency-anomaly) применяются поверх funnel-лесенки:
    если funnel ничего не нашёл, но frequency указывает на проблему — возвращаем
    соответствующий алерт. Если funnel нашёл STOP, а frequency — WARNING, то
    STOP имеет приоритет. STOP от frequency поднимает стадию до STOP в любом случае.
    """
    # Spend is a direct cumulative amount, not a derived rate. Reject evidence
    # that cannot exist in the confirmed currency instead of rounding it into
    # or out of a money-action boundary.
    require_exact_currency_amount(
        row.spend,
        currency=ctx.currency,
        exponent=ctx.currency_exponent,
        field="spend",
    )

    funnel_hit = _evaluate_funnel_ladder(row, ctx)
    freq_hit = _evaluate_frequency_anomaly(ctx)
    nearest_stop = _nearest_stop_hit(row, ctx)

    # Выбираем наивысший приоритет из двух источников
    final_hit = _pick_highest_priority_hit(funnel_hit, freq_hit)

    if final_hit is None:
        return RuleEvaluation(
            stage=None,
            warning_hits=(),
            stop_hits=(),
            nearest_stop=nearest_stop,
        )

    if final_hit.stage == AlertStage.STOP:
        return RuleEvaluation(
            stage=AlertStage.STOP,
            warning_hits=(),
            stop_hits=(final_hit,),
            nearest_stop=nearest_stop,
        )
    return RuleEvaluation(
        stage=AlertStage.WARNING,
        warning_hits=(final_hit,),
        stop_hits=(),
        nearest_stop=nearest_stop,
    )


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
            missing_event_label="кликов",
            currency=ctx.currency,
            currency_exponent=ctx.currency_exponent,
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
            currency=ctx.currency,
            currency_exponent=ctx.currency_exponent,
        ),
        _evaluate_guardrail_only(
            spend=row.spend,
            enabled=ctx.cpl_enabled,
            base_stop_threshold=ctx.cpl_base_stop_threshold,
            stop_threshold=ctx.cpl_stop_threshold,
            warning_threshold=ctx.cpl_warning_threshold,
            code="cpl_stop",
            title=rule_label("cpl_stop"),
            missing_event_label="лидов",
            currency=ctx.currency,
            currency_exponent=ctx.currency_exponent,
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
            currency=ctx.currency,
            currency_exponent=ctx.currency_exponent,
        ),
        _evaluate_guardrail_only(
            spend=row.spend,
            enabled=ctx.cpr_enabled,
            base_stop_threshold=ctx.cpr_base_stop_threshold,
            stop_threshold=ctx.cpr_stop_threshold,
            warning_threshold=ctx.cpr_warning_threshold,
            code="cpr_stop",
            title=rule_label("cpr_stop"),
            missing_event_label="регистраций",
            currency=ctx.currency,
            currency_exponent=ctx.currency_exponent,
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
        currency=ctx.currency,
        currency_exponent=ctx.currency_exponent,
    )

    regs_without_dep_hit = _evaluate_regs_without_deposits(row, ctx)

    spend_without_dep_hit = None
    if _should_apply_registration_spend_guardrail(row, ctx):
        spend_without_dep_hit = _evaluate_spend_range(
            enabled=ctx.spend_no_dep_enabled,
            current_value=_ratio_percent(row.spend, ctx.cpa_amount),
            stop_from=ctx.spend_no_dep_from_percent,
            stop_to=ctx.spend_no_dep_to_percent,
            warning_pct=ctx.effective_spend_no_dep_warning_percent_of_stop,
            stop_percent_of_base=ctx.stop_percent_of_base,
            code="spend_no_dep_range",
            title=rule_label("spend_no_dep_range"),
            summary_suffix="депозитов нет, цена регистрации в норме",
            reason_suffix="Цена регистрации ещё в рабочей зоне, но депозитов всё ещё нет.",
        )

    return _pick_highest_priority_hit(
        cpr_hit,
        regs_without_dep_hit,
        spend_without_dep_hit,
    )


def _evaluate_deposit_stage(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    # Депозиты только из AdSet.pro (источник истины); Meta row.deposits не учитываем.
    # Порог НАМЕРЕННО не масштабируется числом депозитов (решение владельца,
    # аудит 2026-07-12 H-2): кап по spend/CPA один и тот же при 1 и при N депозитах —
    # консервативный дневной потолок расхода на ад. Текст алерта не должен намекать
    # на учёт количества депозитов в формуле.
    total_deposits = ctx.external_deposits
    return _evaluate_spend_range(
        enabled=ctx.spend_with_dep_enabled,
        current_value=_ratio_percent(row.spend, ctx.cpa_amount),
        stop_from=ctx.spend_with_dep_from_percent,
        stop_to=ctx.spend_with_dep_to_percent,
        warning_pct=ctx.effective_spend_with_dep_warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="spend_with_dep_range",
        title=rule_label("spend_with_dep_range"),
        summary_suffix=f"депозиты есть ({deposits_ru(total_deposits)}), кап по расходу",
        reason_suffix=(
            f"Депозиты уже есть ({deposits_ru(total_deposits)}), но расход достиг дневного "
            "капа относительно CPA — кап не зависит от числа депозитов."
        ),
    )


def _evaluate_frequency_anomaly(ctx: RuleContext) -> RuleHit | None:
    """Правило 7: выгорание аудитории по абсолютному порогу frequency.

    STOP: frequency > stop_threshold (например 3.5) — тяжёлый burnout.
    WARNING: frequency > warning_threshold (2.5).
    Без подтверждённой frequency (см. _confirmed_frequency: нет значения, объём
    знаменателя ниже минимума или выброс выше cap) правило не срабатывает.

    LOW (аудит 02.07): историческая ветка "рост за час" (frequency_1h_ago) удалена как
    мёртвый код — build_rule_context (единственный производитель RuleContext) никогда
    не заполняет frequency_1h_ago, значение всегда None (см. докстринг build_rule_context,
    core/observer/pipeline.py: "Фаза 1: только абсолютный порог, без истории"). Ветки с
    prev были недостижимы в проде; фактическое поведение (WARNING по абсолютному порогу
    без учёта роста) не меняется этим упрощением.
    """
    current = _confirmed_frequency(ctx)
    if current is None:
        return None

    stop_thr = ctx.frequency_stop_threshold
    warn_thr = ctx.frequency_warning_threshold

    # STOP: абсолютный порог
    if current > stop_thr:
        return RuleHit(
            code="frequency_anomaly",
            title=rule_label("frequency_anomaly"),
            stage=AlertStage.STOP,
            value=current,
            threshold=stop_thr,
            summary=f"Частота показов {current:.2f} при стопе {stop_thr:.2f}",
            reason_text=(
                f"Частота показов {current:.2f} выше стоп-порога {stop_thr:.2f}: "
                "аудитория выгорела, объявление крутится по одним и тем же людям."
            ),
        )

    # WARNING: абсолютный порог
    if current > warn_thr:
        return RuleHit(
            code="frequency_anomaly",
            title=rule_label("frequency_anomaly"),
            stage=AlertStage.WARNING,
            value=current,
            threshold=warn_thr,
            summary=f"Частота показов {current:.2f} при пороге {warn_thr:.2f}",
            reason_text=(
                f"Частота показов {current:.2f} выше порога {warn_thr:.2f}: "
                "аудитория начинает выгорать."
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


def _nearest_stop_hit(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    """Выбирает максимальный подтверждённый progress к STOP среди применимых правил.

    Кандидаты повторяют текущую funnel-ступень evaluator. Метрика ``None`` не
    участвует в выборе: unknown не становится нулём и не выглядит безопасной.
    Порог в возвращаемом ``RuleHit`` всегда STOP-порог, включая WARNING/none.
    """
    candidates = [*_funnel_stop_candidates(row, ctx), _frequency_stop_candidate(ctx)]
    confirmed = [candidate for candidate in candidates if candidate is not None]
    if not confirmed:
        return None
    return max(confirmed, key=lambda hit: hit.value / hit.threshold)


def _funnel_stop_candidates(row: ScannedAdRow, ctx: RuleContext) -> tuple[RuleHit | None, ...]:
    if _has_confirmed_deposit_signal(row, ctx):
        return (_spend_with_deposit_stop_candidate(row, ctx),)
    if row.registrations >= 1:
        spend_candidate = (
            _spend_without_deposit_stop_candidate(row, ctx)
            if _should_apply_registration_spend_guardrail(row, ctx)
            else None
        )
        return (
            _money_stop_candidate(
                value=row.cost_per_registration,
                enabled=ctx.cpr_enabled,
                stop_threshold=ctx.cpr_stop_threshold,
                warning_threshold=ctx.cpr_warning_threshold,
                code="cpr_stop",
            ),
            _registrations_without_deposit_stop_candidate(row, ctx),
            spend_candidate,
        )
    if row.leads >= 1:
        return (
            _money_stop_candidate(
                value=row.cost_per_lead,
                enabled=ctx.cpl_enabled,
                stop_threshold=ctx.cpl_stop_threshold,
                warning_threshold=ctx.cpl_warning_threshold,
                code="cpl_stop",
            ),
            _decimal_stop_candidate(
                value=row.spend,
                enabled=ctx.cpr_enabled,
                stop_threshold=ctx.cpr_stop_threshold,
                warning_threshold=ctx.cpr_warning_threshold,
                code="cpr_stop",
            ),
        )
    if row.clicks >= 1:
        return (
            _money_stop_candidate(
                value=row.cpc,
                enabled=ctx.cpc_enabled,
                stop_threshold=ctx.cpc_stop_threshold,
                warning_threshold=ctx.cpc_warning_threshold,
                code="cpc_stop",
            ),
            _decimal_stop_candidate(
                value=row.spend,
                enabled=ctx.cpl_enabled,
                stop_threshold=ctx.cpl_stop_threshold,
                warning_threshold=ctx.cpl_warning_threshold,
                code="cpl_stop",
            ),
        )
    return (
        _decimal_stop_candidate(
            value=row.spend,
            enabled=ctx.cpc_enabled,
            stop_threshold=ctx.cpc_stop_threshold,
            warning_threshold=ctx.cpc_warning_threshold,
            code="cpc_stop",
        ),
    )


def _money_stop_candidate(
    *,
    value: Decimal | None,
    enabled: bool,
    stop_threshold: Decimal,
    warning_threshold: Decimal,
    code: str,
) -> RuleHit | None:
    if value is None:
        return None
    return _decimal_stop_candidate(
        value=_exact_derived_money(value),
        enabled=enabled,
        stop_threshold=stop_threshold,
        warning_threshold=warning_threshold,
        code=code,
    )


def _decimal_stop_candidate(
    *,
    value: Decimal,
    enabled: bool,
    stop_threshold: Decimal,
    warning_threshold: Decimal,
    code: str,
) -> RuleHit | None:
    if not enabled:
        return None
    current = Decimal(value)
    stage = (
        AlertStage.STOP
        if current >= stop_threshold
        else AlertStage.WARNING
        if current >= warning_threshold
        else None
    )
    return _stop_progress_hit(
        code=code,
        value=current,
        stop_threshold=Decimal(stop_threshold),
        stage=stage,
    )


def _registrations_without_deposit_stop_candidate(
    row: ScannedAdRow,
    ctx: RuleContext,
) -> RuleHit | None:
    if not ctx.regs_no_dep_enabled or ctx.external_deposits != 0:
        return None
    current = Decimal(row.registrations)
    stop_threshold = Decimal(ctx.regs_no_dep_stop_count)
    warning_threshold = _warning_count(ctx.regs_no_dep_stop_count)
    return _decimal_stop_candidate(
        value=current,
        enabled=True,
        stop_threshold=stop_threshold,
        # Ступени предупреждения при stop<=1 не существует: сравнение со стопом
        # закрывает весь диапазон, а нулевой порог дал бы предупреждение на
        # каждом объявлении с подтверждённым нулём регистраций.
        warning_threshold=warning_threshold if warning_threshold is not None else stop_threshold,
        code="regs_no_dep_stop",
    )


def _spend_without_deposit_stop_candidate(
    row: ScannedAdRow,
    ctx: RuleContext,
) -> RuleHit | None:
    return _spend_range_stop_candidate(
        enabled=ctx.spend_no_dep_enabled,
        current_value=_ratio_percent(row.spend, ctx.cpa_amount),
        stop_from=ctx.spend_no_dep_from_percent,
        warning_pct=ctx.effective_spend_no_dep_warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="spend_no_dep_range",
    )


def _spend_with_deposit_stop_candidate(
    row: ScannedAdRow,
    ctx: RuleContext,
) -> RuleHit | None:
    return _spend_range_stop_candidate(
        enabled=ctx.spend_with_dep_enabled,
        current_value=_ratio_percent(row.spend, ctx.cpa_amount),
        stop_from=ctx.spend_with_dep_from_percent,
        warning_pct=ctx.effective_spend_with_dep_warning_percent_of_stop,
        stop_percent_of_base=ctx.stop_percent_of_base,
        code="spend_with_dep_range",
    )


def _spend_range_stop_candidate(
    *,
    enabled: bool,
    current_value: Decimal,
    stop_from: Decimal,
    warning_pct: Decimal,
    stop_percent_of_base: Decimal,
    code: str,
) -> RuleHit | None:
    if not enabled:
        return None
    current = _round_percent(current_value)
    stop_threshold = _apply_downward_stop(stop_from, stop_percent_of_base)
    warning_threshold = _warning_threshold(stop_threshold, warning_pct)
    stage = (
        AlertStage.STOP
        if current >= stop_threshold
        else AlertStage.WARNING
        if current >= warning_threshold
        else None
    )
    return _stop_progress_hit(
        code=code,
        value=current,
        stop_threshold=_round_percent(stop_threshold),
        stage=stage,
    )


def _frequency_stop_candidate(ctx: RuleContext) -> RuleHit | None:
    current = _confirmed_frequency(ctx)
    if current is None:
        return None
    stage = (
        AlertStage.STOP
        if current > ctx.frequency_stop_threshold
        else AlertStage.WARNING
        if current > ctx.frequency_warning_threshold
        else None
    )
    return _stop_progress_hit(
        code="frequency_anomaly",
        value=current,
        stop_threshold=ctx.frequency_stop_threshold,
        stage=stage,
    )


def _significant_ratio_denominator(volume: int | None, min_denominator: int) -> bool:
    """Известен ли объём знаменателя настолько, что отношение вообще что-то значит.

    ``None`` — объём не подтверждён, значит и отношение неизвестно: незнание не
    превращается ни в ноль, ни в «достаточно».
    """
    return volume is not None and volume >= min_denominator


def _confirmed_frequency(ctx: RuleContext) -> Decimal | None:
    """Частота — отношение «показы / охват»; её знаменатель — reach.

    Ниже ctx.min_ratio_denominator (#260, настраивается per-offer) отношение неизвестно
    (#204): на охвате в десятки человек частота измеряет не выгорание аудитории, а
    стартовый шум Meta. Такое значение молчит целиком — и как сигнал, и как прогресс
    до стопа, — поэтому смягчение стопа по депозиту остаётся в силе.
    """
    if not ctx.frequency_anomaly_enabled or ctx.frequency_current is None:
        return None
    if not _significant_ratio_denominator(ctx.reach, ctx.min_ratio_denominator):
        return None
    current = Decimal(ctx.frequency_current)
    effective_cap = max(
        ctx.frequency_outlier_cap,
        ctx.frequency_stop_threshold * _FREQUENCY_CAP_MULTIPLIER,
    )
    return None if current > effective_cap else current


def _stop_progress_hit(
    *,
    code: str,
    value: Decimal,
    stop_threshold: Decimal,
    stage: AlertStage | None,
) -> RuleHit:
    if not value.is_finite() or value < 0:
        raise ValueError("rule progress value must be finite and non-negative")
    if not stop_threshold.is_finite() or stop_threshold <= 0:
        raise ValueError("stop threshold must be finite and positive")
    title = rule_label(code)
    metric = rule_metric_label(code)
    return RuleHit(
        code=code,
        title=title,
        stage=stage,
        value=value,
        threshold=stop_threshold,
        summary=f"{metric} {value} из {stop_threshold} до стопа",
        reason_text=f"{metric} сейчас {value}, стоп на {stop_threshold}.",
    )


def _evaluate_metric_only(
    *,
    metric_value: Decimal | None,
    enabled: bool,
    base_stop_threshold: Decimal,
    stop_threshold: Decimal,
    warning_threshold: Decimal,
    code: str,
    title: str,
    currency: str,
    currency_exponent: int,
) -> RuleHit | None:
    if not enabled or metric_value is None:
        return None

    current = _exact_derived_money(metric_value)
    metric = rule_metric_label(code)
    current_text = _format_money_with_currency(current, currency, currency_exponent)
    threshold_text = _format_threshold_value(
        stop_threshold,
        base_stop_threshold,
        currency,
        currency_exponent,
    )

    if current >= stop_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.STOP,
            value=current,
            threshold=stop_threshold,
            summary=f"{metric} {current_text} при стопе {threshold_text}",
            reason_text=(
                f"{metric} сейчас {current_text}, "
                f"а стоп для этого правила {threshold_text} — граница пройдена."
            ),
        )

    if current >= warning_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.WARNING,
            value=current,
            threshold=warning_threshold,
            summary=f"{metric} {current_text} подходит к стопу {threshold_text}",
            reason_text=(
                f"{metric} сейчас {current_text}, "
                f"а стоп для этого правила {threshold_text} — запас почти исчерпан."
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
    missing_event_label: str,
    currency: str,
    currency_exponent: int,
) -> RuleHit | None:
    # Жёсткий стоп без ожидания показов/охвата (решение байера): расход без
    # события выше стоп-порога — money-сигнал, стопаем сразу, не ждём накопления
    # показов. Мизерный спенд сам отсекается порогом spend>=stop_threshold.
    if not enabled:
        return None

    current_spend = Decimal(spend)
    metric = rule_metric_label(code).lower()
    spend_text = _format_money_with_currency(current_spend, currency, currency_exponent)
    threshold_text = _format_threshold_value(
        stop_threshold,
        base_stop_threshold,
        currency,
        currency_exponent,
    )

    if current_spend >= stop_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.STOP,
            value=current_spend,
            threshold=stop_threshold,
            summary=(
                f"Потрачено {spend_text}, а {missing_event_label} нет — "
                f"{metric} уже за стопом {threshold_text}"
            ),
            reason_text=(
                f"Потрачено {spend_text}, но {missing_event_label} так и нет. "
                f"Стоп по правилу «{metric}» — {threshold_text}, "
                "следующий шаг воронки уже будет слишком дорогим."
            ),
        )

    if current_spend >= warning_threshold:
        return RuleHit(
            code=code,
            title=title,
            stage=AlertStage.WARNING,
            value=current_spend,
            threshold=warning_threshold,
            summary=(
                f"Потрачено {spend_text}, а {missing_event_label} нет — "
                f"{metric} подходит к стопу {threshold_text}"
            ),
            reason_text=(
                f"Потрачено {spend_text}, но {missing_event_label} так и нет. "
                f"Стоп по правилу «{metric}» — {threshold_text}, запас почти закончился."
            ),
        )

    return None


def _evaluate_regs_without_deposits(row: ScannedAdRow, ctx: RuleContext) -> RuleHit | None:
    # Депозиты только из AdSet.pro: правило молчит, если внешний трекер видел депозит.
    if not ctx.regs_no_dep_enabled or ctx.external_deposits != 0:
        return None

    stop_val = Decimal(ctx.regs_no_dep_stop_count)
    warning_val = _warning_count(ctx.regs_no_dep_stop_count)
    current = Decimal(row.registrations)

    if current >= stop_val:
        return RuleHit(
            code="regs_no_dep_stop",
            title=rule_label("regs_no_dep_stop"),
            stage=AlertStage.STOP,
            value=current,
            threshold=stop_val,
            summary=(
                f"{registrations_ru(row.registrations)} без депозитов, "
                f"стоп с {registrations_ru(ctx.regs_no_dep_stop_count)}"
            ),
            reason_text=(
                f"Объявление собрало {registrations_ru(row.registrations)}, "
                "но не дало ни одного депозита. Стоп по этому правилу начинается "
                f"с {registrations_ru(ctx.regs_no_dep_stop_count)} без депозита."
            ),
        )

    if warning_val is not None and current >= warning_val:
        return RuleHit(
            code="regs_no_dep_stop",
            title=rule_label("regs_no_dep_stop"),
            stage=AlertStage.WARNING,
            value=current,
            threshold=warning_val,
            summary=(
                f"{registrations_ru(row.registrations)} без депозитов, до стопа осталось немного"
            ),
            reason_text=(
                f"Регистрации копятся без депозитов: сейчас {registrations_ru(row.registrations)} "
                f"и депозитов нет. Стоп начнётся "
                f"с {registrations_ru(ctx.regs_no_dep_stop_count)} без депозита."
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
            summary=(
                f"Расход {current:.2f}% от CPA при стоп-диапазоне {range_text}, {summary_suffix}"
            ),
            reason_text=(
                f"Расход дошёл до {current:.2f}% от CPA и вошёл в стоп-диапазон {range_text}. "
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
            summary=(
                f"Расход {current:.2f}% от CPA подходит к стоп-диапазону {range_text}, "
                f"{summary_suffix}"
            ),
            reason_text=(
                f"Расход дошёл до {current:.2f}% от CPA и подходит к стоп-диапазону {range_text}. "
                f"{reason_suffix}"
            ),
        )

    return None


def _should_apply_registration_spend_guardrail(row: ScannedAdRow, ctx: RuleContext) -> bool:
    """Запускать ли spend-guardrail (расход без депа) на registration-ступени.

    Запускаем когда:
    - цена регистрации в норме (CPR ≤ стоп) — классический случай; ИЛИ
    - цена регистрации ещё НЕ известна (CPR=None — attribution-лаг Meta: count
      регистраций есть в actions, но cost_per_action_type ещё не посчитан).
      Без этого backstop'а убыточный ад с регистрациями, без депозитов и с
      временно-NULL CPR крутит бюджет без авто-стопа, пока CPR не появится или
      регистрации не дорастут до regs_no_dep stop-порога (money-leak H1). Если
      CPR известна и ВЫШЕ стопа — cpr_hit застопит раньше и жёстче, так что
      запуск spend-guardrail тут не ослабляет защиту.

    NB: сам spend_no_dep_range стопает только при расходе ≥ 50% CPA, поэтому
    мизерный спенд при CPR=None ложных стопов не даёт.
    """
    if row.registrations <= 0:
        return False
    if row.cost_per_registration is None:
        return True
    return _exact_derived_money(row.cost_per_registration) <= ctx.cpr_stop_threshold


def _has_confirmed_deposit_signal(row: ScannedAdRow, ctx: RuleContext) -> bool:
    """Депозит подтверждаем ТОЛЬКО по данным трекера AdSet.pro (external_deposits >= 1).

    Источник истины по депозитам — один: AdSet.pro (решение пользователя). Meta-видимые
    «депозиты» (row.deposits — из колонки «Результат» / pixel purchase) НЕ доверяем и в
    решениях по депозитам не учитываем. Следствие: объявление входит в deposit_stage (и под
    защиту от no-dep guardrail'ов) только когда AdSet.pro прислал depositное событие.
    """
    return ctx.external_deposits >= 1


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


def _exact_derived_money(value: Decimal) -> Decimal:
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError("derived money metric must be finite and non-negative")
    return amount


def _round_percent(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_PERCENT_STEP, rounding=ROUND_HALF_UP)


def _format_money_value(value: Decimal, currency_exponent: int) -> str:
    step = Decimal(1).scaleb(-currency_exponent)
    rendered = Decimal(value).quantize(step, rounding=ROUND_HALF_UP)
    return format(rendered, f".{currency_exponent}f")


def _format_percent_value(value: Decimal) -> str:
    return f"{_round_percent(value):.2f}%"


def _format_money_with_currency(value: Decimal, currency: str, currency_exponent: int) -> str:
    """Сумма всегда с валютой: голое число оператор не может сравнить с планом."""
    return f"{_format_money_value(value, currency_exponent)} {currency}"


def _format_threshold_value(
    effective_value: Decimal,
    base_value: Decimal,
    currency: str,
    currency_exponent: int,
) -> str:
    if Decimal(effective_value) == Decimal(base_value):
        return _format_money_with_currency(effective_value, currency, currency_exponent)
    return (
        f"{_format_money_with_currency(effective_value, currency, currency_exponent)} "
        f"(базовый {_format_money_with_currency(base_value, currency, currency_exponent)})"
    )


def _format_percent_range(
    effective_from: Decimal,
    effective_to: Decimal,
    base_from: Decimal,
    base_to: Decimal,
) -> str:
    if Decimal(effective_from) == Decimal(base_from) and Decimal(effective_to) == Decimal(base_to):
        return f"{effective_from:.2f}-{effective_to:.2f}%"
    return f"{effective_from:.2f}-{effective_to:.2f}% (базовый {base_from:.2f}-{base_to:.2f}%)"


def _warning_count(stop_count: int) -> Decimal | None:
    # Для целого счётчика регистраций предупреждение приходит за одну единицу до
    # стопа. Процентная формула с ceil давала вырождение: при stop=5 любая
    # чувствительность от 81% округлялась обратно в 5, и пауза наступала без
    # предупреждения.
    #
    # При stop<=1 предупредить не за что: ступени между нулём и первой
    # регистрацией не существует. Это None — «не задано», а не 0: ноль
    # регистраций есть подтверждённый ноль, и предупреждение по нему полетело бы
    # на каждом объявлении, ещё не начавшем работать.
    if stop_count <= 1:
        return None
    return Decimal(stop_count - 1)
