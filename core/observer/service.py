# -*- coding: utf-8 -*-
"""Основной сервис observer: обрабатывает строки объявлений и создаёт алерты."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from core.domain import AlertStage
from core.observer.thresholds import (
    DEFAULT_STOP_PERCENT_OF_BASE,
    DEFAULT_WARNING_PERCENT_OF_STOP,
    extract_observer_threshold_values,
)
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext, RuleEvaluation
from core.scanner.models import ScannedAdRow


@dataclass(slots=True, frozen=True)
class AlertCandidate:
    """Кандидат на отправку алерта в Telegram."""

    snapshot_id: str
    offer_id: object  # UUID | None
    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    offer_code: str | None
    offer_name: str | None
    offer_cpa: str | None
    stage: AlertStage
    matched_rule_codes: list[str]
    reason_title: str | None
    reason_text: str | None
    metrics_json: dict[str, Any]
    persist_event: bool = True


@dataclass(slots=True, frozen=True)
class ObserverCycleResult:
    """Результат одного цикла observer."""

    alerts_to_send: list[AlertCandidate]
    observed_ads: int


def _make_json_safe(value: Any) -> Any:
    """Рекурсивно приводит payload к JSON-совместимому виду."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return value


def build_rule_context(
    *,
    cpa_amount: Decimal,
    warning_percent_of_stop: Decimal = DEFAULT_WARNING_PERCENT_OF_STOP,
    stop_percent_of_base: Decimal = DEFAULT_STOP_PERCENT_OF_BASE,
    rule_config: object,
    observer_thresholds: dict[str, Decimal] | None = None,
) -> RuleContext:
    """Строит RuleContext из конфигурации правил оффера."""
    threshold_values = extract_observer_threshold_values(
        observer_thresholds
        or {
            "warning_percent_of_stop": warning_percent_of_stop,
            "stop_percent_of_base": stop_percent_of_base,
        }
    )
    return RuleContext(
        cpa_amount=Decimal(cpa_amount),
        warning_percent_of_stop=threshold_values["warning_percent_of_stop"],
        stop_percent_of_base=threshold_values["stop_percent_of_base"],
        cpc_warning_percent_of_stop=threshold_values["cpc_warning_percent_of_stop"],
        cpc_stop_percent_of_base=threshold_values["cpc_stop_percent_of_base"],
        cpl_warning_percent_of_stop=threshold_values["cpl_warning_percent_of_stop"],
        cpl_stop_percent_of_base=threshold_values["cpl_stop_percent_of_base"],
        cpr_warning_percent_of_stop=threshold_values["cpr_warning_percent_of_stop"],
        cpr_stop_percent_of_base=threshold_values["cpr_stop_percent_of_base"],
        cpc_enabled=bool(rule_config.cpc_percent_enabled),
        cpc_percent_stop=Decimal(rule_config.cpc_percent_stop),
        cpl_enabled=bool(rule_config.cpl_percent_enabled),
        cpl_percent_stop=Decimal(rule_config.cpl_percent_stop),
        cpr_enabled=bool(rule_config.cpr_percent_enabled),
        cpr_percent_stop=Decimal(rule_config.cpr_percent_stop),
        regs_no_dep_enabled=bool(rule_config.regs_no_dep_enabled),
        regs_no_dep_stop_count=int(rule_config.regs_no_dep_stop_count),
        spend_no_dep_enabled=bool(rule_config.spend_no_dep_enabled),
        spend_no_dep_from_percent=Decimal(rule_config.spend_no_dep_from_percent),
        spend_no_dep_to_percent=Decimal(rule_config.spend_no_dep_to_percent),
        spend_with_dep_enabled=bool(rule_config.spend_with_dep_enabled),
        spend_with_dep_from_percent=Decimal(rule_config.spend_with_dep_from_percent),
        spend_with_dep_to_percent=Decimal(rule_config.spend_with_dep_to_percent),
        early_outbound_ctr_signal_enabled=bool(rule_config.early_outbound_ctr_signal_enabled),
        early_outbound_ctr_signal_min_percent=Decimal(
            rule_config.early_outbound_ctr_signal_min_percent
        ),
        early_outbound_ctr_signal_min_spend_percent=Decimal(
            rule_config.early_outbound_ctr_signal_min_spend_percent
        ),
        early_lpv_ratio_signal_enabled=bool(rule_config.early_lpv_ratio_signal_enabled),
        early_lpv_ratio_signal_min_percent=Decimal(rule_config.early_lpv_ratio_signal_min_percent),
        early_lpv_ratio_signal_min_outbound_clicks=int(
            rule_config.early_lpv_ratio_signal_min_outbound_clicks
        ),
        early_cost_per_lpv_signal_enabled=bool(rule_config.early_cost_per_lpv_signal_enabled),
        early_cost_per_lpv_signal_percent_of_cpa=Decimal(
            rule_config.early_cost_per_lpv_signal_percent_of_cpa
        ),
        early_cost_per_lpv_signal_min_views=int(rule_config.early_cost_per_lpv_signal_min_views),
    )


def evaluate_row(
    *,
    row: ScannedAdRow,
    offer_cpa: Decimal | None,
    rule_config: object | None,
    warning_percent_of_stop: Decimal = DEFAULT_WARNING_PERCENT_OF_STOP,
    stop_percent_of_base: Decimal = DEFAULT_STOP_PERCENT_OF_BASE,
    observer_thresholds: dict[str, Decimal] | None = None,
) -> RuleEvaluation:
    """Оценивает одну строку. Без оффера — пропуск."""
    if offer_cpa is None or rule_config is None:
        return RuleEvaluation(stage=None, early_signal_hits=(), warning_hits=(), stop_hits=())
    ctx = build_rule_context(
        cpa_amount=offer_cpa,
        warning_percent_of_stop=warning_percent_of_stop,
        stop_percent_of_base=stop_percent_of_base,
        rule_config=rule_config,
        observer_thresholds=observer_thresholds,
    )
    return evaluate_stop_rules(row, ctx)


def _compose_reason_text(base_reason: str | None, diagnostics_text: str | None) -> str | None:
    """Склеивает основную причину и диагностический контекст."""
    if base_reason and diagnostics_text:
        return f"{base_reason} {diagnostics_text}"
    return base_reason or diagnostics_text


def resolve_offer_code(
    ad_name: str,
    campaign_name: str,
    offers: dict,
) -> str | None:
    """Сопоставляет объявление с оффером по вхождению кода в название.

    Оффер содержит часть названия объявления/кампании.
    Например, оффер "DRC_CR2" → объявление "DRC_CR2_CR002".

    Используется word-boundary matching: код не должен быть частью другого
    слова. Символы [a-z0-9_] считаются «внутри слова».
    """
    text_lower = f"{campaign_name} {ad_name}".casefold()
    best_match: str | None = None
    best_len = 0

    for code in offers:
        code_lower = code.casefold()
        pattern = r"(?<![a-z0-9])" + re.escape(code_lower) + r"(?![a-z0-9])"
        if re.search(pattern, text_lower) and len(code) > best_len:
            best_match = code
            best_len = len(code)

    return best_match


def build_metrics_json(
    row: ScannedAdRow,
    *,
    rule_summaries: list[str] | None = None,
    traffic_diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Формирует JSON-словарь метрик для хранения и отправки в TG."""
    payload = {
        "spend": f"{Decimal(row.spend):.2f}",
        "budget": row.budget or None,
        "reach": row.reach,
        "impressions": row.impressions,
        "clicks": row.clicks,
        "cpc": f"{Decimal(row.cpc):.4f}" if row.cpc is not None else None,
        "ctr": f"{Decimal(row.ctr):.4f}" if row.ctr is not None else None,
        "cost_per_result": (
            f"{Decimal(row.cost_per_result):.4f}" if row.cost_per_result is not None else None
        ),
        "cpm": f"{Decimal(row.cpm):.4f}" if row.cpm is not None else None,
        "frequency": f"{Decimal(row.frequency):.4f}" if row.frequency is not None else None,
        "leads": row.leads,
        "cost_per_lead": f"{Decimal(row.cost_per_lead):.4f}"
        if row.cost_per_lead is not None
        else None,
        "registrations": row.registrations,
        "cost_per_registration": (
            f"{Decimal(row.cost_per_registration):.4f}"
            if row.cost_per_registration is not None
            else None
        ),
        "deposits": row.deposits,
        "outbound_clicks": row.outbound_clicks,
        "outbound_ctr": f"{Decimal(row.outbound_ctr):.4f}"
        if row.outbound_ctr is not None
        else None,
        "landing_page_views": row.landing_page_views,
        "cost_per_landing_page_view": (
            f"{Decimal(row.cost_per_landing_page_view):.4f}"
            if row.cost_per_landing_page_view is not None
            else None
        ),
    }
    if rule_summaries:
        payload["rule_summaries"] = rule_summaries
    if traffic_diagnostics:
        payload["traffic_diagnostics"] = traffic_diagnostics
    return _make_json_safe(payload)
