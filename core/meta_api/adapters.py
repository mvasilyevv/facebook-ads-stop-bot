# -*- coding: utf-8 -*-
"""Адаптеры для конвертации DTO Marketing API в доменные структуры.

Главная задача модуля — явное и тестируемое преобразование MetaInsightsRow → ScannedAdRow.
ScannedAdRow — главный контракт между сканером и evaluator'ом (core/scanner/models.py).

Запрещённые импорты (см. META_INTEGRATION_PLAN.md §3.3):
    - core.observer.* — смешивание контрактов
    - apps.observer_worker.*, apps.disable_worker.*, apps.enable_worker.*
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

from core.meta_api.schemas import MetaInsightsRow
from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)


# ── Примитивные парсеры ────────────────────────────────────────────────────────


def parse_decimal(value: str | float | int | None) -> Decimal | None:
    """Безопасно конвертирует строку/число в Decimal.

    Возвращает None для пустых, None и невалидных значений.
    Meta возвращает числа строками ("1.23"), иногда float, иногда None.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    str_val = str(value).strip()
    if not str_val:
        return None
    try:
        return Decimal(str_val)
    except (InvalidOperation, ValueError):
        logger.debug("parse_decimal: невалидное значение %r", value)
        return None


def parse_int(value: str | int | None, *, default: int = 0) -> int:
    """Безопасно конвертирует строку/число в int.

    При None, пустой строке или невалидном значении возвращает default (по умолчанию 0).
    """
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    str_val = str(value).strip()
    if not str_val:
        return default
    try:
        # Числа могут приходить как "1234.0" — берём через Decimal → int
        return int(Decimal(str_val))
    except (InvalidOperation, ValueError):
        logger.debug("parse_int: невалидное значение %r", value)
        return default


# ── Парсеры actions/cost_per_action_type ──────────────────────────────────────


def extract_action_value(
    actions: list[dict] | None,
    action_type: str,
) -> int:
    """Ищет в списке actions элемент с нужным action_type и возвращает его value.

    Meta возвращает: [{"action_type": "lead", "value": "5"}, ...].
    Если action_type не найден — возвращает 0.
    """
    if not actions:
        return 0
    for item in actions:
        if item.get("action_type") == action_type:
            return parse_int(item.get("value"))
    return 0


def extract_cost_per_action(
    cost_per_action_type: list[dict] | None,
    action_type: str,
) -> Decimal | None:
    """Ищет в cost_per_action_type элемент с нужным action_type и возвращает value.

    Meta возвращает: [{"action_type": "lead", "value": "2.50"}, ...].
    Если action_type не найден — возвращает None.
    """
    if not cost_per_action_type:
        return None
    for item in cost_per_action_type:
        if item.get("action_type") == action_type:
            return parse_decimal(item.get("value"))
    return None


# ── Парсинг сырого JSON от Marketing API ──────────────────────────────────────

# Маппинг action_type для leads: Meta использует несколько вариантов
_LEAD_ACTION_TYPES = ("lead", "offsite_conversion.fb_pixel_lead", "leadgen_grouped")
# Маппинг action_type для registrations
_REGISTRATION_ACTION_TYPES = (
    "complete_registration",
    "offsite_conversion.fb_pixel_complete_registration",
)
# Маппинг action_type для deposits (purchase / кастомный ивент)
_DEPOSIT_ACTION_TYPES = (
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "omni_purchase",
)


def _extract_first_matching(
    actions: list[dict] | None,
    types: tuple[str, ...],
) -> int:
    """Возвращает value первого совпадающего action_type из кортежа приоритетов."""
    if not actions:
        return 0
    for action_type in types:
        val = extract_action_value(actions, action_type)
        if val:
            return val
    return 0


def _extract_cost_first_matching(
    cost_per_action_type: list[dict] | None,
    types: tuple[str, ...],
) -> Decimal | None:
    """Возвращает cost первого совпадающего action_type из кортежа приоритетов."""
    if not cost_per_action_type:
        return None
    for action_type in types:
        val = extract_cost_per_action(cost_per_action_type, action_type)
        if val is not None:
            return val
    return None


def parse_insights_row_from_dict(raw: dict) -> MetaInsightsRow:
    """Парсит сырой JSON-ответ от Marketing API в MetaInsightsRow.

    Ожидаемый формат — одна строка из response["data"] endpoint /insights.
    actions и cost_per_action_type — опциональные списки.
    Все числовые поля Meta возвращает строками.
    """
    actions: list[dict] | None = raw.get("actions")
    cost_per_action_type: list[dict] | None = raw.get("cost_per_action_type")

    leads = _extract_first_matching(actions, _LEAD_ACTION_TYPES)
    registrations = _extract_first_matching(actions, _REGISTRATION_ACTION_TYPES)
    deposits = _extract_first_matching(actions, _DEPOSIT_ACTION_TYPES)

    cost_per_lead = _extract_cost_first_matching(cost_per_action_type, _LEAD_ACTION_TYPES)
    cost_per_registration = _extract_cost_first_matching(
        cost_per_action_type, _REGISTRATION_ACTION_TYPES
    )
    cost_per_deposit = _extract_cost_first_matching(cost_per_action_type, _DEPOSIT_ACTION_TYPES)

    # outbound_clicks живёт тоже в actions со своим action_type
    outbound_clicks_raw = extract_action_value(actions, "outbound_click")
    outbound_clicks = outbound_clicks_raw if outbound_clicks_raw else None

    # outbound_ctr приходит как отдельное поле верхнего уровня
    outbound_ctr_raw = raw.get("outbound_clicks_ctr")
    outbound_ctr: Decimal | None = None
    if outbound_ctr_raw:
        # может быть списком [{"action_type": "outbound_click", "value": "1.23"}]
        if isinstance(outbound_ctr_raw, list):
            first = outbound_ctr_raw[0] if outbound_ctr_raw else None
            outbound_ctr = parse_decimal(first.get("value") if first else None)
        else:
            outbound_ctr = parse_decimal(outbound_ctr_raw)

    lpv_raw = raw.get("landing_page_views")
    landing_page_views: int | None = None
    if lpv_raw is not None:
        # иногда приходит как actions[action_type=landing_page_view]
        if isinstance(lpv_raw, list):
            landing_page_views = extract_action_value(lpv_raw, "landing_page_view")
        else:
            landing_page_views = parse_int(lpv_raw) or None

    cost_per_lpv = parse_decimal(raw.get("cost_per_landing_page_view"))
    # cost_per_landing_page_view тоже бывает списком
    if cost_per_lpv is None and isinstance(raw.get("cost_per_landing_page_view"), list):
        lpv_list = raw["cost_per_landing_page_view"]
        if lpv_list:
            cost_per_lpv = parse_decimal(lpv_list[0].get("value"))

    return MetaInsightsRow(
        ad_id=str(raw.get("ad_id", "")),
        ad_name=str(raw.get("ad_name", "")),
        adset_name=str(raw.get("adset_name", "")),
        campaign_name=str(raw.get("campaign_name", "")),
        spend=parse_decimal(raw.get("spend")) or Decimal("0"),
        impressions=parse_int(raw.get("impressions")),
        clicks=parse_int(raw.get("clicks")),
        cpc=parse_decimal(raw.get("cpc")),
        ctr=parse_decimal(raw.get("ctr")),
        cpm=parse_decimal(raw.get("cpm")),
        frequency=parse_decimal(raw.get("frequency")),
        reach=parse_int(raw.get("reach")) or None,
        outbound_clicks=outbound_clicks,
        outbound_ctr=outbound_ctr,
        landing_page_views=landing_page_views,
        cost_per_landing_page_view=cost_per_lpv,
        leads=leads,
        cost_per_lead=cost_per_lead,
        registrations=registrations,
        cost_per_registration=cost_per_registration,
        deposits=deposits,
        cost_per_deposit=cost_per_deposit,
        cost_per_result=parse_decimal(raw.get("cost_per_result")),
        date_start=str(raw.get("date_start", "")),
        date_stop=str(raw.get("date_stop", "")),
    )


# ── Главный адаптер: MetaInsightsRow → ScannedAdRow ───────────────────────────


def meta_insights_row_to_scanned_ad_row(
    row: MetaInsightsRow,
    *,
    delivery_status: str = "active",
) -> ScannedAdRow:
    """Конвертирует MetaInsightsRow в ScannedAdRow для повторного использования в rules/evaluator.

    delivery_status передаётся снаружи — /insights его не возвращает, нужен отдельный запрос
    к /{ad_id}?fields=effective_status. По умолчанию "active"; вызывающий код должен явно
    передать реальный статус, если знает его.

    ScannedAdRow — главный контракт (core/scanner/models.py). Не мутируем его структуру —
    адаптируем данные к существующим полям.
    """
    return ScannedAdRow(
        fb_ad_id=row.ad_id,
        campaign_name=row.campaign_name,
        adset_name=row.adset_name,
        ad_name=row.ad_name,
        delivery_status=delivery_status,
        spend=row.spend,
        # budget не возвращается из /insights — оставляем пустым
        budget="",
        reach=row.reach or 0,
        impressions=row.impressions,
        clicks=row.clicks,
        cpc=row.cpc,
        ctr=row.ctr,
        outbound_clicks=row.outbound_clicks or 0,
        outbound_ctr=row.outbound_ctr,
        landing_page_views=row.landing_page_views or 0,
        cost_per_landing_page_view=row.cost_per_landing_page_view,
        cost_per_result=row.cost_per_result,
        cpm=row.cpm,
        frequency=row.frequency,
        leads=row.leads,
        cost_per_lead=row.cost_per_lead,
        registrations=row.registrations,
        cost_per_registration=row.cost_per_registration,
        deposits=row.deposits,
        # resolved_offer_code определяется выше по стеку (через offer matcher)
        resolved_offer_code=None,
    )
