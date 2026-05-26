# -*- coding: utf-8 -*-
"""Основной сервис observer: обрабатывает строки объявлений и создаёт алерты."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any

from core.domain import AlertStage
from core.observer.thresholds import extract_threshold_values
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
    rule_config: object,
    frequency_current: Decimal | None = None,
    frequency_1h_ago: Decimal | None = None,
    impressions: int | None = None,
    reach: int | None = None,
    adaptive_cpa: Decimal | None = None,
    use_adaptive_cpa: bool = False,
) -> RuleContext:
    """Строит RuleContext из конфигурации правил оффера.

    frequency_current / frequency_1h_ago — текущая frequency и ~час назад для
    правила выгорания аудитории; если None — правило не срабатывает.
    adaptive_cpa / use_adaptive_cpa — rolling median CPA по офферу; если
    use_adaptive_cpa=True и adaptive_cpa не None — используется вместо статичного.
    """
    threshold_values = extract_threshold_values(rule_config)

    # Читаем поля frequency-anomaly из rule_config (новые поля с дефолтами)
    _freq_anomaly_enabled = bool(getattr(rule_config, "frequency_anomaly_enabled", True))
    _freq_warn_thr = Decimal(str(getattr(rule_config, "frequency_warning_threshold", "2.5")))
    _freq_growth_pct = Decimal(str(getattr(rule_config, "frequency_growth_warning_pct", "30.0")))
    _freq_stop_thr = Decimal(str(getattr(rule_config, "frequency_stop_threshold", "3.5")))

    # Если adaptive_cpa включён и данные есть — используем их вместо статичного cpa_amount
    effective_cpa = (
        Decimal(adaptive_cpa)
        if use_adaptive_cpa and adaptive_cpa is not None
        else Decimal(cpa_amount)
    )

    return RuleContext(
        cpa_amount=effective_cpa,
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
        frequency_anomaly_enabled=_freq_anomaly_enabled,
        frequency_current=frequency_current,
        frequency_1h_ago=frequency_1h_ago,
        frequency_warning_threshold=_freq_warn_thr,
        frequency_growth_warning_pct=_freq_growth_pct,
        frequency_stop_threshold=_freq_stop_thr,
        impressions=impressions,
        reach=reach,
        use_adaptive_cpa=use_adaptive_cpa,
        adaptive_cpa=Decimal(adaptive_cpa) if adaptive_cpa is not None else None,
    )


def evaluate_row(
    *,
    row: ScannedAdRow,
    offer_cpa: Decimal | None,
    rule_config: object | None,
    frequency_1h_ago: Decimal | None = None,
    adaptive_cpa: Decimal | None = None,
    use_adaptive_cpa: bool = False,
) -> RuleEvaluation:
    """Оценивает одну строку. Без оффера — пропуск.

    frequency_1h_ago — значение frequency ~час назад (из AdMetricHistory).
    adaptive_cpa / use_adaptive_cpa — rolling median CPA по офферу; если
    use_adaptive_cpa=True и adaptive_cpa не None — пороги вычисляются от него.
    """
    if offer_cpa is None or rule_config is None:
        return RuleEvaluation(stage=None, warning_hits=(), stop_hits=())
    ctx = build_rule_context(
        cpa_amount=offer_cpa,
        rule_config=rule_config,
        frequency_current=Decimal(str(row.frequency)) if row.frequency is not None else None,
        frequency_1h_ago=frequency_1h_ago,
        impressions=int(row.impressions) if row.impressions is not None else None,
        reach=int(row.reach) if getattr(row, "reach", None) is not None else None,
        adaptive_cpa=adaptive_cpa,
        use_adaptive_cpa=use_adaptive_cpa,
    )
    return evaluate_stop_rules(row, ctx)


def _compose_reason_text(base_reason: str | None, diagnostics_text: str | None) -> str | None:
    """Склеивает основную причину и диагностический контекст."""
    if base_reason and diagnostics_text:
        return f"{base_reason} {diagnostics_text}"
    return base_reason or diagnostics_text


@lru_cache(maxsize=1024)
def _get_pattern(code_lower: str) -> re.Pattern[str]:
    """Возвращает скомпилированный regex для кода оффера (кэшируется по коду)."""
    return re.compile(r"(?<![a-z0-9])" + re.escape(code_lower) + r"(?![a-z0-9])")


def resolve_offer_code(
    ad_name: str,
    campaign_name: str,
    offers: dict,
) -> str | None:
    """Сопоставляет объявление с оффером по вхождению кода в название.

    Имя объявления и имя кампании рассматриваются по отдельности:
    сначала ищем код только в имени объявления (приоритетный источник),
    и лишь если там ничего не нашлось — пробуем имя кампании.

    Используется word-boundary matching: код не должен быть частью другого
    слова. Символы [a-z0-9_] считаются «внутри слова».
    Например, для оффера "KE_CR2" в строке "KEN_CR2_CR001" совпадения нет,
    а в "KE_CR2_CR001" — есть.
    При нескольких совпадениях побеждает самый длинный код.

    Паттерны компилируются один раз через _get_pattern (lru_cache).
    """

    def _best_match_in(text: str) -> str | None:
        text_lower = text.casefold()
        best_code: str | None = None
        best_len = 0
        for code in offers:
            if _get_pattern(code.casefold()).search(text_lower) and len(code) > best_len:
                best_code = code
                best_len = len(code)
        return best_code

    # Имя объявления — приоритетный источник истины
    match_in_ad = _best_match_in(ad_name)
    if match_in_ad is not None:
        return match_in_ad

    # Fallback на имя кампании, если в объявлении кода нет
    return _best_match_in(campaign_name)


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
