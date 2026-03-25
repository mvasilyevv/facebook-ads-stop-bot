# -*- coding: utf-8 -*-
"""Парсер DOM Ads Manager: извлекает строки объявлений из HTML-таблицы.

Этот модуль нужно адаптировать под конкретную структуру DOM
Facebook Ads Manager, которая может меняться.
Текущие селекторы взяты из анализа существующего бота.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)

# Регулярка для извлечения числа из строки типа "$0.15" или "0.15"
_MONEY_RE = re.compile(r"[\d,.]+")
_AD_ID_RE = re.compile(r"\d{5,}")


async def parse_ads_from_page(page) -> list[ScannedAdRow]:
    """Парсит видимые строки таблицы Ads Manager из текущего viewport.

    Основная стратегия: ищем строки таблицы через data-testid или aria-атрибуты,
    извлекаем метрики из ячеек. Этот парсер нужно адаптировать под конкретную
    структуру DOM, которая может различаться.

    Args:
        page: Playwright Page, открытая на Ads Manager

    Returns:
        Список ScannedAdRow с нормализованными метриками
    """
    rows: list[ScannedAdRow] = []

    try:
        # Пробуем разные селекторы строк таблицы
        row_elements = await page.query_selector_all(
            'div[data-testid="ads-manager-table"] tr,'
            ' table tbody tr,'
            ' div[role="row"]'
        )

        for element in row_elements:
            try:
                row = await _parse_single_row(element)
                if row is not None:
                    rows.append(row)
            except Exception:
                logger.debug("Не удалось распарсить строку", exc_info=True)
                continue

    except Exception:
        logger.exception("Ошибка при парсинге таблицы Ads Manager")

    return rows


async def _parse_single_row(element) -> ScannedAdRow | None:
    """Пытается извлечь данные объявления из одной строки таблицы."""
    text = await element.inner_text()
    if not text or len(text) < 5:
        return None

    cells = await element.query_selector_all("td, div[role='gridcell'], div[role='cell']")
    if len(cells) < 3:
        return None

    cell_texts = []
    for cell in cells:
        cell_text = (await cell.inner_text()).strip()
        cell_texts.append(cell_text)

    # Ищем Ad ID в тексте строки
    ad_id_match = _AD_ID_RE.search(text)
    fb_ad_id = ad_id_match.group() if ad_id_match else ""

    if not fb_ad_id:
        return None

    # Извлекаем имена (эвристика — первые текстовые ячейки)
    ad_name = cell_texts[0] if len(cell_texts) > 0 else "unknown"
    delivery_status = _detect_delivery_status(text)

    # Извлекаем метрики из ячеек
    spend = _extract_money(cell_texts, "spend", Decimal("0"))
    clicks = _extract_int(cell_texts, "clicks", 0)
    cpc = _extract_money_or_none(cell_texts, "cpc")
    leads = _extract_int(cell_texts, "leads", 0)
    cost_per_lead = _extract_money_or_none(cell_texts, "cost_per_lead")
    registrations = _extract_int(cell_texts, "registrations", 0)
    cost_per_registration = _extract_money_or_none(cell_texts, "cost_per_registration")
    deposits = _extract_int(cell_texts, "deposits", 0)

    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name="",
        adset_name="",
        ad_name=ad_name,
        delivery_status=delivery_status,
        spend=spend,
        clicks=clicks,
        cpc=cpc,
        leads=leads,
        cost_per_lead=cost_per_lead,
        registrations=registrations,
        cost_per_registration=cost_per_registration,
        deposits=deposits,
    )


def _detect_delivery_status(text: str) -> str:
    """Определяет статус объявления по тексту строки."""
    lowered = text.lower()
    if "active" in lowered or "активно" in lowered:
        return "ACTIVE"
    if "paused" in lowered or "пауза" in lowered:
        return "PAUSED"
    if "not delivering" in lowered or "не показывается" in lowered:
        return "NOT_DELIVERING"
    if "learning" in lowered or "обучение" in lowered:
        return "LEARNING"
    return "UNKNOWN"


def _parse_decimal(text: str) -> Decimal | None:
    """Извлекает Decimal из текстовой строки ('$0.15', '0,15', '-')."""
    if not text or text.strip() in ("-", "–", "—", "n/a", ""):
        return None
    match = _MONEY_RE.search(text.replace(",", "."))
    if not match:
        return None
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return None


def _parse_int(text: str) -> int:
    """Извлекает int из текстовой строки."""
    if not text or text.strip() in ("-", "–", "—", "n/a", ""):
        return 0
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0


def _extract_money(cells: list[str], name: str, default: Decimal) -> Decimal:
    """Пытается найти денежное значение в ячейках. Fallback на default."""
    # Простая эвристика: ищем ячейку с $ или числом
    for cell in cells:
        val = _parse_decimal(cell)
        if val is not None and val >= 0:
            return val
    return default


def _extract_money_or_none(cells: list[str], name: str) -> Decimal | None:
    """Аналогично _extract_money, но может вернуть None."""
    return None  # Placeholder — нужна точная привязка к колонкам


def _extract_int(cells: list[str], name: str, default: int) -> int:
    """Извлекает целое число из ячеек."""
    return default  # Placeholder — нужна точная привязка к колонкам
