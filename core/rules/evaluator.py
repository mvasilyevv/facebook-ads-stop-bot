# -*- coding: utf-8 -*-
"""Движок стоп-правил: оценка метрик объявления по 6 правилам с двумя порогами."""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from core.domain import AlertStage
from core.rules.types import RuleContext, RuleEvaluation, RuleHit
from core.scanner.models import ScannedAdRow

_HUNDRED = Decimal("100")


def evaluate_stop_rules(row: ScannedAdRow, ctx: RuleContext) -> RuleEvaluation:
    """Оценивает все 6 стоп-правил для одного объявления.

    Возвращает RuleEvaluation с разделением на WARNING и STOP хиты.
    Учитывает нюанс: если spend > порога и clicks/leads/regs = 0,
    то первый клик/лид/рега сразу превысит метрику → тоже стоп.
    """
    stop_hits: list[RuleHit] = []
    warning_hits: list[RuleHit] = []
    spend_percent = _ratio_percent(row.spend, ctx.cpa_amount)

    # --- Правило 1: CPC > X% CPA ---
    _check_metric_rule(
        stop_hits=stop_hits,
        warning_hits=warning_hits,
        code="cpc_stop",
        title="Клик дороже допустимого процента CPA",
        metric_value=row.cpc,
        metric_count=row.clicks,
        spend=row.spend,
        stop_percent=ctx.cpc_percent_stop,
        cpa_amount=ctx.cpa_amount,
        warning_pct=ctx.warning_percent_of_stop,
        enabled=ctx.cpc_enabled,
        label="CPC",
    )

    # --- Правило 2: CPL > X% CPA ---
    _check_metric_rule(
        stop_hits=stop_hits,
        warning_hits=warning_hits,
        code="cpl_stop",
        title="Лид дороже допустимого процента CPA",
        metric_value=row.cost_per_lead,
        metric_count=row.leads,
        spend=row.spend,
        stop_percent=ctx.cpl_percent_stop,
        cpa_amount=ctx.cpa_amount,
        warning_pct=ctx.warning_percent_of_stop,
        enabled=ctx.cpl_enabled,
        label="CPL",
    )

    # --- Правило 3: CPR > X% CPA ---
    cpr_threshold = _percent_of_cpa(ctx.cpa_amount, ctx.cpr_percent_stop)
    _check_metric_rule(
        stop_hits=stop_hits,
        warning_hits=warning_hits,
        code="cpr_stop",
        title="Регистрация дороже допустимого процента CPA",
        metric_value=row.cost_per_registration,
        metric_count=row.registrations,
        spend=row.spend,
        stop_percent=ctx.cpr_percent_stop,
        cpa_amount=ctx.cpa_amount,
        warning_pct=ctx.warning_percent_of_stop,
        enabled=ctx.cpr_enabled,
        label="CPR",
    )

    # --- Правило 4: N регистраций без депозитов ---
    registration_normal = (
        row.registrations > 0
        and row.cost_per_registration is not None
        and row.cost_per_registration <= cpr_threshold
    )
    if ctx.regs_no_dep_enabled and row.deposits == 0:
        stop_val = Decimal(ctx.regs_no_dep_stop_count)
        warn_val = _warning_count(ctx.regs_no_dep_stop_count, ctx.warning_percent_of_stop)
        current = Decimal(row.registrations)
        if current >= stop_val:
            stop_hits.append(
                RuleHit(
                    code="regs_no_dep_stop",
                    title="Регистрации без депозитов",
                    stage=AlertStage.STOP,
                    value=current,
                    threshold=stop_val,
                    summary=f"Регистраций {row.registrations}, депозитов 0 (стоп от {ctx.regs_no_dep_stop_count})",
                )
            )
        elif current >= warn_val:
            warning_hits.append(
                RuleHit(
                    code="regs_no_dep_stop",
                    title="Регистрации без депозитов",
                    stage=AlertStage.WARNING,
                    value=current,
                    threshold=warn_val,
                    summary=f"Регистраций {row.registrations}, депозитов 0 — до стопа мало",
                )
            )

    # --- Правило 5: Расход 50-70% CPA, нет депов, рега в норме ---
    if ctx.spend_no_dep_enabled and row.deposits == 0 and registration_normal:
        _check_range_rule(
            stop_hits=stop_hits,
            warning_hits=warning_hits,
            code="spend_no_dep_range",
            title="Расход без депа при нормальной реге",
            current_value=spend_percent,
            stop_from=ctx.spend_no_dep_from_percent,
            stop_to=ctx.spend_no_dep_to_percent,
            warning_pct=ctx.warning_percent_of_stop,
            summary=f"Расход {spend_percent:.2f}% CPA, депозитов 0, рега в норме",
        )

    # --- Правило 6: Есть деп, расход 70-90% CPA ---
    if ctx.spend_with_dep_enabled and row.deposits >= 1:
        _check_range_rule(
            stop_hits=stop_hits,
            warning_hits=warning_hits,
            code="spend_with_dep_range",
            title="Расход с депозитом близок к стоп-зоне",
            current_value=spend_percent,
            stop_from=ctx.spend_with_dep_from_percent,
            stop_to=ctx.spend_with_dep_to_percent,
            warning_pct=ctx.warning_percent_of_stop,
            summary=f"Расход {spend_percent:.2f}% CPA, депозитов {row.deposits}",
        )

    # Определяем итоговую стадию
    if stop_hits:
        stage = AlertStage.STOP
    elif warning_hits:
        stage = AlertStage.WARNING
    else:
        stage = None

    return RuleEvaluation(
        stage=stage,
        warning_hits=tuple(warning_hits),
        stop_hits=tuple(stop_hits),
    )


def _check_metric_rule(
    *,
    stop_hits: list[RuleHit],
    warning_hits: list[RuleHit],
    code: str,
    title: str,
    metric_value: Decimal | None,
    metric_count: int,
    spend: Decimal,
    stop_percent: Decimal,
    cpa_amount: Decimal,
    warning_pct: Decimal,
    enabled: bool,
    label: str,
) -> None:
    """Проверяет процентное правило (CPC/CPL/CPR).

    Ключевой нюанс: если metric_count == 0, но spend > порога,
    то первый клик/лид/рега сразу превысит метрику → стоп.
    """
    if not enabled:
        return

    stop_threshold = _percent_of_cpa(cpa_amount, stop_percent)
    warning_threshold = _warning_threshold(stop_threshold, warning_pct)

    # Нюанс: расход превышает порог, но событий (кликов/лидов/рег) нет
    if metric_count == 0 and spend > stop_threshold:
        stop_hits.append(
            RuleHit(
                code=code,
                title=title,
                stage=AlertStage.STOP,
                value=spend,
                threshold=stop_threshold,
                summary=f"Расход {spend:.4f} превысил порог {label} {stop_threshold:.4f} без единого события",
            )
        )
        return

    if metric_count == 0 and spend > warning_threshold:
        warning_hits.append(
            RuleHit(
                code=code,
                title=title,
                stage=AlertStage.WARNING,
                value=spend,
                threshold=warning_threshold,
                summary=f"Расход {spend:.4f} приближается к порогу {label} {stop_threshold:.4f} без событий",
            )
        )
        return

    if metric_value is None:
        return

    current = Decimal(metric_value)
    if current > stop_threshold:
        stop_hits.append(
            RuleHit(
                code=code,
                title=title,
                stage=AlertStage.STOP,
                value=current,
                threshold=stop_threshold,
                summary=f"{label} {current:.4f} выше стопа {stop_threshold:.4f}",
            )
        )
    elif current > warning_threshold:
        warning_hits.append(
            RuleHit(
                code=code,
                title=title,
                stage=AlertStage.WARNING,
                value=current,
                threshold=warning_threshold,
                summary=f"{label} {current:.4f} приближается к стопу {stop_threshold:.4f}",
            )
        )


def _check_range_rule(
    *,
    stop_hits: list[RuleHit],
    warning_hits: list[RuleHit],
    code: str,
    title: str,
    current_value: Decimal,
    stop_from: Decimal,
    stop_to: Decimal,
    warning_pct: Decimal,
    summary: str,
) -> None:
    """Проверяет диапазонное правило (расход в процентах CPA)."""
    warning_from = _warning_threshold(stop_from, warning_pct)
    if stop_from <= current_value <= stop_to:
        stop_hits.append(
            RuleHit(
                code=code,
                title=title,
                stage=AlertStage.STOP,
                value=current_value,
                threshold=stop_from,
                summary=summary,
            )
        )
    elif warning_from <= current_value < stop_from:
        warning_hits.append(
            RuleHit(
                code=code,
                title=title,
                stage=AlertStage.WARNING,
                value=current_value,
                threshold=warning_from,
                summary=summary,
            )
        )


# --- Вспомогательные функции ---


def _percent_of_cpa(cpa: Decimal, percent: Decimal) -> Decimal:
    return (Decimal(cpa) * Decimal(percent)) / _HUNDRED


def _ratio_percent(value: Decimal, total: Decimal) -> Decimal:
    if Decimal(total) <= 0:
        return Decimal("0")
    return (Decimal(value) / Decimal(total)) * _HUNDRED


def _warning_threshold(stop_threshold: Decimal, warning_pct: Decimal) -> Decimal:
    return (Decimal(stop_threshold) * Decimal(warning_pct)) / _HUNDRED


def _warning_count(stop_count: int, warning_pct: Decimal) -> Decimal:
    return (Decimal(stop_count) * Decimal(warning_pct) / _HUNDRED).quantize(
        Decimal("1"),
        rounding=ROUND_CEILING,
    )
