# -*- coding: utf-8 -*-
"""Логика границы суток кабинета и архивирования агрегатов."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from core.math_utils import safe_div_str, safe_percent

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
    """Достаёт значение поля из dict-подобного объекта или ORM-модели.

    Для нормализованных полей (campaign_name, adset_name, ad_name, offer_code, offer_id)
    навигирует через цепочку fb_ad → adset → campaign, если объект — AdSnapshot.
    """
    if isinstance(item, Mapping):
        return item.get(field)

    # Прямой доступ к атрибуту ORM
    direct = getattr(item, field, None)
    if direct is not None:
        return direct

    # Нормализованные поля — навигация через relationship chain
    fb_ad = getattr(item, "fb_ad", None)
    if fb_ad is None:
        return None

    if field == "ad_name":
        return getattr(fb_ad, "ad_name", None)

    adset = getattr(fb_ad, "adset", None)
    if field == "adset_name":
        return getattr(adset, "adset_name", None) if adset else None

    campaign = getattr(adset, "campaign", None) if adset else None
    if field == "campaign_name":
        return getattr(campaign, "campaign_name", None) if campaign else None
    if field == "offer_code":
        return getattr(campaign, "offer_code", None) if campaign else None
    if field == "offer_id":
        return getattr(campaign, "offer_id", None) if campaign else None

    return None


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


_safe_decimal_div = safe_div_str
_safe_percent = safe_percent


def build_cabinet_day_archive_payload(
    items: list[Mapping[str, Any] | Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Собирает summary, breakdown по кампаниям и per-ad данные для архива."""
    total_spend = Decimal("0")
    total_clicks = 0
    total_leads = 0
    total_regs = 0
    total_deps = 0
    campaign_map: dict[str, dict[str, Any]] = {}
    ads_list: list[dict[str, Any]] = []

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

        # Per-ad запись для ads_json
        fb_ad_id = str(_extract_value(item, "fb_ad_id") or "")
        if fb_ad_id:
            ads_list.append(
                {
                    "fb_ad_id": fb_ad_id,
                    "ad_name": str(_extract_value(item, "ad_name") or ""),
                    "campaign_name": campaign_name,
                    "offer_code": _extract_value(item, "offer_code") or None,
                    "spend": str(spend),
                    "clicks": clicks,
                    "leads": leads,
                    "registrations": regs,
                    "deposits": deps,
                }
            )

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
        "cost_per_deposit": _safe_decimal_div(total_spend, total_deps),
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
                "cost_per_deposit": _safe_decimal_div(spend, row["deposits"]),
                "click_to_lead_rate": _safe_percent(row["leads"], row["clicks"]),
                "lead_to_reg_rate": _safe_percent(row["registrations"], row["leads"]),
                "reg_to_dep_rate": _safe_percent(row["deposits"], row["registrations"]),
            }
        )

    # Сортируем объявления по spend DESC
    ads_list.sort(key=lambda a: Decimal(a["spend"]), reverse=True)

    return summary, campaigns, ads_list
