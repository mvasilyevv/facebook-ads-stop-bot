# -*- coding: utf-8 -*-
"""Presentation-only columns for the human-visible Ads Manager tab."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qs, parse_qsl, urlencode

# Keep this value byte-for-byte aligned with browser-agent DEFAULT_COLUMNS_QS.
# A unit contract reads the TypeScript source and rejects drift between the two runtimes.
DEFAULT_AM_COLUMNS_QS = (
    "columns=name%2Cdelivery%2Cbudget%2Cresults%2Creach%2Cimpressions%2Ccost_per_result"
    "%2Cspend%2Cclicks%2Ccpc%2Cactions%3Alead%2Ccost_per_action_type%3Alead"
    "%2Cactions%3Aomni_complete_registration%2Ccost_per_action_type%3Aomni_complete_registration"
    "%2Cctr%2Ccampaign_group_name%2Ccampaign_name%2Coutbound_clicks%3Aoutbound_click"
    "%2Coutbound_clicks_ctr%3Aoutbound_click%2Cactions%3Aomni_landing_page_view"
    "%2Ccost_per_action_type%3Alanding_page_view%2Ccpm%2Cfrequency"
    "&attribution_windows=default&column_preset=1030561339462971"
)

_COLUMN_LABELS = {
    "name": "Название",
    "delivery": "Статус показа",
    "budget": "Бюджет",
    "results": "Результаты",
    "reach": "Охват",
    "impressions": "Показы",
    "cost_per_result": "Цена за результат",
    "spend": "Затраты",
    "clicks": "Клики",
    "cpc": "CPC",
    "actions:lead": "Лиды",
    "cost_per_action_type:lead": "Цена за лид",
    "actions:omni_complete_registration": "Регистрации",
    "cost_per_action_type:omni_complete_registration": "Цена за регистрацию",
    "ctr": "CTR",
    "campaign_group_name": "Группа кампаний",
    "campaign_name": "Кампания",
    "outbound_clicks:outbound_click": "Исходящие клики",
    "outbound_clicks_ctr:outbound_click": "CTR исходящих кликов",
    "actions:omni_landing_page_view": "Просмотры лендинга",
    "cost_per_action_type:landing_page_view": "Цена просмотра лендинга",
    "cpm": "CPM",
    "frequency": "Частота",
}


def _columns_from_qs(value: str) -> tuple[str, ...]:
    columns = parse_qs(value, keep_blank_values=False).get("columns", [])
    if len(columns) != 1:
        return ()
    return tuple(column for column in columns[0].split(",") if column)


DEFAULT_AM_COLUMNS = _columns_from_qs(DEFAULT_AM_COLUMNS_QS)
KNOWN_AM_COLUMN_IDS = frozenset(DEFAULT_AM_COLUMNS)
KNOWN_AM_COLUMN_OPTIONS = tuple(
    (column_id, _COLUMN_LABELS[column_id]) for column_id in DEFAULT_AM_COLUMNS
)


def normalize_am_columns(column_ids: Iterable[str] | None) -> tuple[str, ...] | None:
    """Validate and deduplicate a checkbox selection; empty means default."""

    if not column_ids:
        return None
    normalized = tuple(dict.fromkeys(str(column_id).strip() for column_id in column_ids))
    if not normalized or any(not column_id for column_id in normalized):
        return None
    unknown = [column_id for column_id in normalized if column_id not in KNOWN_AM_COLUMN_IDS]
    if unknown:
        raise ValueError(f"Неизвестные колонки Ads Manager: {', '.join(unknown)}")
    return normalized


def build_am_columns_qs(column_ids: Iterable[str] | None) -> str | None:
    """Build the stored query from known IDs; ``None`` selects runtime fallback."""

    normalized = normalize_am_columns(column_ids)
    if normalized is None:
        return None
    params = parse_qsl(DEFAULT_AM_COLUMNS_QS, keep_blank_values=True)
    return urlencode(
        [(key, ",".join(normalized) if key == "columns" else value) for key, value in params]
    )


def selected_am_columns(stored_qs: str | None) -> tuple[str, ...]:
    """Return the stored known selection, or the built-in template for runtime fallback."""

    if not stored_qs or not stored_qs.strip():
        return DEFAULT_AM_COLUMNS
    selected = _columns_from_qs(stored_qs)
    return tuple(column_id for column_id in selected if column_id in KNOWN_AM_COLUMN_IDS)
