# -*- coding: utf-8 -*-
"""Адаптеры: Marketing API JSON → MetaInsightsRow → MetaApiAdRow → ScannedAdRow.

Цель: использовать существующий pipeline (process_scan_rows) и rule_evaluator
без модификаций — Marketing API даёт собственный DTO, который через явный
adapter превращается в общеизвестный ScannedAdRow.

ScannedAdRow остаётся главным контрактом (см. § 3.4 плана).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from core.meta_api.schemas import MetaApiAdRow, MetaInsightsRow
from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)
_MODERATION_REASON_LIMIT = 600


# ====================== низкоуровневые helpers ======================


def _to_decimal(value: Any) -> Decimal | None:
    """Безопасный парсинг Decimal из str/int/float. None если пусто."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        logger.warning("Не удалось распарсить Decimal (value_type=%s)", type(value).__name__)
        return None


def _to_int(value: Any, *, default: int = 0) -> int:
    """Безопасный парсинг int. Дефолт при None или ошибке."""
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (ValueError, TypeError):
        logger.warning("Не удалось распарсить int (value_type=%s)", type(value).__name__)
        return default


def flatten_actions(actions_field: list[dict[str, Any]] | None) -> dict[str, int]:
    """Сплющить Meta actions/cost_per_action_type в dict.

    Вход: [{"action_type": "lead", "value": "5"}, ...]
    Выход: {"lead": 5, ...}

    Не падает на пустом списке и на отсутствующих value.
    """
    if not actions_field:
        return {}
    result: dict[str, int] = {}
    for item in actions_field:
        if not isinstance(item, dict):
            continue
        action_type = item.get("action_type")
        if not action_type:
            continue
        result[str(action_type)] = _to_int(item.get("value"))
    return result


def _parse_iso_date(value: Any) -> date | None:
    """Парс ISO-даты вида '2026-05-27'."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _clean_moderation_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _collect_moderation_feedback(
    value: Any,
    parts: list[str],
    *,
    label: str | None = None,
) -> None:
    text_value = _clean_moderation_text(value)
    if text_value is not None:
        parts.append(f"{label}: {text_value}" if label else text_value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_moderation_feedback(item, parts, label=label)
        return
    if not isinstance(value, dict):
        return
    for key, nested in value.items():
        _collect_moderation_feedback(
            nested,
            parts,
            label=str(key).replace("_", " "),
        )


def extract_moderation_reason(ad: dict[str, Any]) -> str | None:
    """Причина только из явного ответа Meta; отсутствие не реконструируется."""
    parts: list[str] = []
    _collect_moderation_feedback(ad.get("ad_review_feedback"), parts)
    issues = ad.get("issues_info")
    if isinstance(issues, list):
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            summary = _clean_moderation_text(issue.get("error_summary"))
            message = _clean_moderation_text(issue.get("error_message"))
            if summary and message and summary != message:
                parts.append(f"{summary}: {message}")
            elif message or summary:
                parts.append(message or summary or "")
    unique_parts = list(dict.fromkeys(parts))
    if not unique_parts:
        return None
    return " · ".join(unique_parts)[:_MODERATION_REASON_LIMIT]


# ====================== Insights row парсинг ======================


def meta_insights_row_from_dict(
    data: dict[str, Any],
    *,
    ad_account_id: str,
) -> MetaInsightsRow:
    """Распарсить один элемент из data[] ответа GET /act_X/insights.

    Возвращает frozen MetaInsightsRow. raw сохраняет исходный JSON для аудита.
    """
    return MetaInsightsRow(
        ad_id=str(data.get("ad_id") or ""),
        campaign_id=str(data["campaign_id"]) if data.get("campaign_id") else None,
        adset_id=str(data["adset_id"]) if data.get("adset_id") else None,
        ad_account_id=ad_account_id,
        spend=_to_decimal(data.get("spend")) or Decimal("0"),
        impressions=_to_int(data.get("impressions")),
        clicks=_to_int(data.get("clicks")),
        reach=_to_int(data.get("reach")),
        cpc=_to_decimal(data.get("cpc")),
        ctr=_to_decimal(data.get("ctr")),
        cpm=_to_decimal(data.get("cpm")),
        frequency=_to_decimal(data.get("frequency")),
        actions=flatten_actions(data.get("actions")),
        date_start=_parse_iso_date(data.get("date_start")),
        date_stop=_parse_iso_date(data.get("date_stop")),
        raw=dict(data),
    )


# ====================== MetaApiAdRow конверсия ======================


def merge_insights_and_ad(
    *,
    ad: dict[str, Any],
    insights: MetaInsightsRow,
    ad_account_id: str,
) -> MetaApiAdRow:
    """Склеить /ads (имена + статус) и /insights (метрики) в MetaApiAdRow.

    `ad` — элемент data[] ответа GET /act_X/ads?fields=name,effective_status,campaign{name},adset{name}.
    """
    campaign = ad.get("campaign") or {}
    adset = ad.get("adset") or {}
    return MetaApiAdRow(
        fb_ad_id=str(ad.get("id") or insights.ad_id),
        fb_campaign_id=str(campaign.get("id")) if campaign.get("id") else insights.campaign_id,
        fb_adset_id=str(adset.get("id")) if adset.get("id") else insights.adset_id,
        ad_account_id=ad_account_id,
        name=str(ad.get("name") or ""),
        campaign_name=str(campaign.get("name") or ""),
        adset_name=str(adset.get("name") or ""),
        effective_status=str(ad.get("effective_status") or "UNKNOWN"),
        configured_status=str(ad.get("status") or ad.get("effective_status") or "UNKNOWN"),
        spend=insights.spend,
        impressions=insights.impressions,
        clicks=insights.clicks,
        cpc=insights.cpc,
        ctr=insights.ctr,
        cpm=insights.cpm,
        reach=insights.reach,
        frequency=insights.frequency,
        actions=insights.actions,
        observed_at=datetime.now(timezone.utc),
        moderation_reason=extract_moderation_reason(ad),
    )


# ====================== MetaApiAdRow → ScannedAdRow ======================


def meta_api_ad_row_to_scanned_row(
    api_row: MetaApiAdRow,
    *,
    resolved_offer_code: str | None = None,
) -> ScannedAdRow:
    """Преобразовать API-снимок в ScannedAdRow для pipeline.

    Метрики, которых нет в Marketing API (budget, outbound_ctr), оставляем "/None.
    Actions раскладываем по типам.
    """
    actions = api_row.actions

    leads = actions.get("lead", 0)
    registrations = actions.get("complete_registration", 0)
    deposits = actions.get("offsite_conversion.custom.deposit", 0) or actions.get("purchase", 0)
    landing_page_views = actions.get("landing_page_view", 0)
    outbound_clicks = actions.get("link_click", 0) or api_row.clicks

    cost_per_lead = (api_row.spend / leads) if leads else None
    cost_per_registration = (api_row.spend / registrations) if registrations else None
    cost_per_landing_page_view = (
        (api_row.spend / landing_page_views) if landing_page_views else None
    )
    cost_per_result = cost_per_lead or cost_per_landing_page_view

    return ScannedAdRow(
        fb_ad_id=api_row.fb_ad_id,
        campaign_id=api_row.fb_campaign_id or "",
        adset_id=api_row.fb_adset_id or "",
        campaign_name=api_row.campaign_name,
        adset_name=api_row.adset_name,
        ad_name=api_row.name,
        delivery_status=api_row.effective_status.strip().upper() or "UNKNOWN",
        spend=api_row.spend,
        moderation_reason=api_row.moderation_reason,
        budget="",  # из insights не известен — нужен отдельный запрос /adsets
        reach=api_row.reach,
        impressions=api_row.impressions,
        clicks=api_row.clicks,
        cpc=api_row.cpc,
        ctr=api_row.ctr,
        outbound_clicks=outbound_clicks,
        outbound_ctr=None,  # Marketing API не отдаёт этот ключ в формате нашего сканера
        landing_page_views=landing_page_views,
        cost_per_landing_page_view=cost_per_landing_page_view,
        cost_per_result=cost_per_result,
        cpm=api_row.cpm,
        frequency=api_row.frequency,
        leads=leads,
        cost_per_lead=cost_per_lead,
        registrations=registrations,
        cost_per_registration=cost_per_registration,
        deposits=deposits,
        resolved_offer_code=resolved_offer_code,
    )
