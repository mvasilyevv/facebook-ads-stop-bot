# -*- coding: utf-8 -*-
"""Основной сервис observer: обрабатывает строки объявлений и создаёт алерты."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from core.domain import AlertStage, AlertState
from core.observer.state_machine import resolve_transition
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext, RuleEvaluation
from core.scanner.models import ScannedAdRow


@dataclass(slots=True, frozen=True)
class AlertCandidate:
    """Кандидат на отправку алерта в Telegram."""
    snapshot_id: str
    fb_ad_id: str
    ad_name: str
    offer_code: str | None
    stage: AlertStage
    matched_rule_codes: list[str]
    metrics_json: dict[str, str | int | None]


@dataclass(slots=True, frozen=True)
class ObserverCycleResult:
    """Результат одного цикла observer."""
    alerts_to_send: list[AlertCandidate]
    observed_ads: int


def build_rule_context(
    *,
    cpa_amount: Decimal,
    warning_percent_of_stop: Decimal,
    rule_config: object,
) -> RuleContext:
    """Строит RuleContext из конфигурации правил оффера."""
    return RuleContext(
        cpa_amount=Decimal(cpa_amount),
        warning_percent_of_stop=Decimal(warning_percent_of_stop),
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
    )


def evaluate_row(
    *,
    row: ScannedAdRow,
    offer_cpa: Decimal | None,
    rule_config: object | None,
    warning_percent_of_stop: Decimal,
) -> RuleEvaluation:
    """Оценивает одну строку. Без оффера — пропуск."""
    if offer_cpa is None or rule_config is None:
        return RuleEvaluation(stage=None, warning_hits=(), stop_hits=())
    ctx = build_rule_context(
        cpa_amount=offer_cpa,
        warning_percent_of_stop=warning_percent_of_stop,
        rule_config=rule_config,
    )
    return evaluate_stop_rules(row, ctx)


def build_metrics_json(row: ScannedAdRow) -> dict[str, str | int | None]:
    """Формирует JSON-словарь метрик для хранения и отправки в TG."""
    return {
        "spend": f"{Decimal(row.spend):.2f}",
        "clicks": row.clicks,
        "cpc": f"{Decimal(row.cpc):.4f}" if row.cpc is not None else None,
        "leads": row.leads,
        "cost_per_lead": f"{Decimal(row.cost_per_lead):.4f}" if row.cost_per_lead is not None else None,
        "registrations": row.registrations,
        "cost_per_registration": (
            f"{Decimal(row.cost_per_registration):.4f}"
            if row.cost_per_registration is not None
            else None
        ),
        "deposits": row.deposits,
    }
