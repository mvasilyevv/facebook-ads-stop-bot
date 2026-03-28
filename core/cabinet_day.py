# -*- coding: utf-8 -*-
"""Логика границы суток кабинета и архивирования агрегатов."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


_METRIC_FIELDS = (
    "spend",
    "clicks",
    "leads",
    "registrations",
    "deposits",
    "cpc",
    "cost_per_lead",
    "cost_per_registration",
)


def _extract_value(item: Mapping[str, Any] | Any, field: str) -> Any:
    """Достаёт значение поля из dict-подобного объекта или ORM-модели."""
    if isinstance(item, Mapping):
        return item.get(field)
    return getattr(item, field, None)


def _is_zero_value(value: Any) -> bool:
    """Считает пустые и нулевые значения метрик эквивалентными нулю."""
    if value is None:
        return True
    try:
        return Decimal(str(value)) == 0
    except (InvalidOperation, ValueError, TypeError):
        return False


def has_any_metric_value(item: Mapping[str, Any] | Any) -> bool:
    """Проверяет, есть ли в строке или snapshot-е хоть одна ненулевая метрика."""
    return any(not _is_zero_value(_extract_value(item, field)) for field in _METRIC_FIELDS)


def is_cabinet_day_reset_scan(items: list[Mapping[str, Any] | Any]) -> bool:
    """Определяет zero-scan, который означает начало новых суток в кабинете."""
    if not items:
        return False
    return all(not has_any_metric_value(item) for item in items)


def _safe_decimal_div(numerator: Decimal, denominator: int) -> str | None:
    """Строит строковое представление cost-метрики для JSON-архива."""
    if denominator <= 0:
        return None
    return str((Decimal(numerator) / Decimal(denominator)).quantize(Decimal("0.0001")))


def _safe_percent(numerator: int, denominator: int) -> float | None:
    """Считает конверсию в процентах для JSON-архива."""
    if denominator <= 0:
        return None
    return round((float(numerator) / float(denominator)) * 100, 1)


def build_cabinet_day_archive_payload(
    items: list[Mapping[str, Any] | Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Собирает summary и breakdown по кампаниям для архива завершившихся суток."""
    total_spend = Decimal("0")
    total_clicks = 0
    total_leads = 0
    total_regs = 0
    total_deps = 0
    campaign_map: dict[str, dict[str, Any]] = {}

    for item in items:
        spend = Decimal(str(_extract_value(item, "spend") or 0))
        clicks = int(_extract_value(item, "clicks") or 0)
        leads = int(_extract_value(item, "leads") or 0)
        regs = int(_extract_value(item, "registrations") or 0)
        deps = int(_extract_value(item, "deposits") or 0)
        campaign_name = str(_extract_value(item, "campaign_name") or "").strip()

        total_spend += spend
        total_clicks += clicks
        total_leads += leads
        total_regs += regs
        total_deps += deps

        if not campaign_name:
            continue

        row = campaign_map.setdefault(
            campaign_name,
            {
                "campaign": campaign_name,
                "spend": Decimal("0"),
                "clicks": 0,
                "leads": 0,
                "registrations": 0,
                "deposits": 0,
            },
        )
        row["spend"] += spend
        row["clicks"] += clicks
        row["leads"] += leads
        row["registrations"] += regs
        row["deposits"] += deps

    summary = {
        "ads_count": len(items),
        "spend": str(total_spend),
        "clicks": total_clicks,
        "leads": total_leads,
        "registrations": total_regs,
        "deposits": total_deps,
        "cpc": _safe_decimal_div(total_spend, total_clicks),
        "cpl": _safe_decimal_div(total_spend, total_leads),
        "cpr": _safe_decimal_div(total_spend, total_regs),
        "spend_per_dep": _safe_decimal_div(total_spend, total_deps),
        "click_to_lead_rate": _safe_percent(total_leads, total_clicks),
        "lead_to_reg_rate": _safe_percent(total_regs, total_leads),
        "reg_to_dep_rate": _safe_percent(total_deps, total_regs),
    }

    campaigns = []
    for row in sorted(campaign_map.values(), key=lambda item: item["spend"], reverse=True):
        spend = row["spend"]
        campaigns.append(
            {
                "campaign": row["campaign"],
                "spend": str(spend),
                "clicks": row["clicks"],
                "leads": row["leads"],
                "registrations": row["registrations"],
                "deposits": row["deposits"],
                "cpc": _safe_decimal_div(spend, row["clicks"]),
                "cpl": _safe_decimal_div(spend, row["leads"]),
                "cpr": _safe_decimal_div(spend, row["registrations"]),
                "spend_per_dep": _safe_decimal_div(spend, row["deposits"]),
                "click_to_lead_rate": _safe_percent(row["leads"], row["clicks"]),
                "lead_to_reg_rate": _safe_percent(row["registrations"], row["leads"]),
                "reg_to_dep_rate": _safe_percent(row["deposits"], row["registrations"]),
            }
        )

    return summary, campaigns
