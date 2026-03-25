# -*- coding: utf-8 -*-
"""Парсер DOM Ads Manager: извлекает строки объявлений через data-surface атрибуты.

Использует структуру Facebook Ads Manager, где каждая ячейка таблицы
имеет атрибут data-surface с ключом вида "table_row:N:field_name".
Также поддерживает обновление таблицы через кнопку «Обновить» без перезагрузки.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)

# Регулярка для извлечения числа из строки типа "$0.15", "0,15 $", "0.15"
_MONEY_RE = re.compile(r"[\d]+[.,]?\d*")
# Регулярка для номера строки в data-surface
_ROW_ID_RE = re.compile(r"table_row:(\d+)")
# Регулярка для Ad ID (минимум 5 цифр подряд)
_AD_ID_RE = re.compile(r"\d{5,}")

# Маппинг ключей data-surface → поля ScannedAdRow
_FIELD_KEYS = {
    "campaign_group_name": "campaign_name",
    "campaign_name": "adset_name",
    "forObjectType(name,ADGROUP)": "ad_name",
    "delivery": "delivery_status",
    "spend": "spend",
    "clicks": "clicks",
    "cpc": "cpc",
    "actions:lead": "leads",
    "cost_per_action_type:lead": "cost_per_lead",
    "omni_complete_registration": "registrations",
    "cost_per_action_type:omni_complete_registration": "cost_per_registration",
    "cost_per_result": "cost_per_result",
}


async def refresh_table(page) -> bool:
    """Нажимает кнопку «Обновить» в Ads Manager вместо перезагрузки страницы.

    Returns:
        True если кнопка найдена и нажата, False если нет
    """
    try:
        # Ищем контейнер с кнопками обновления и публикации
        container = await page.query_selector('[data-pagelet="AdsRefreshAndPublishButtons"]')
        if not container:
            logger.debug("Контейнер кнопок обновления не найден")
            return False

        # Ищем кнопку с текстом «Обновить» / «Refresh»
        buttons = await container.query_selector_all('[role="button"]')
        for btn in buttons:
            text = await btn.inner_text()
            if "Обновить" in text or "Refresh" in text or "обновить" in text:
                await btn.click()
                logger.info("Нажата кнопка «Обновить данные в таблице»")
                return True

        logger.debug("Кнопка обновления не найдена среди кнопок контейнера")
        return False
    except Exception:
        logger.debug("Ошибка при попытке нажать кнопку обновления", exc_info=True)
        return False


async def parse_ads_from_page(page) -> list[ScannedAdRow]:
    """Парсит видимые строки таблицы Ads Manager из текущего viewport.

    Стратегия: ищем ячейки с атрибутом data-surface, группируем по номеру
    строки (table_row:N), извлекаем метрики по ключам полей.

    Args:
        page: Playwright Page, открытая на Ads Manager

    Returns:
        Список ScannedAdRow с нормализованными метриками
    """
    rows: list[ScannedAdRow] = []

    try:
        # Находим все ячейки с data-surface содержащим table_row
        cells = await page.query_selector_all('[data-surface*="table_row:"]')

        # Группируем ячейки по номеру строки
        rows_map: dict[str, list] = {}
        for cell in cells:
            surface = await cell.get_attribute("data-surface")
            if not surface:
                continue
            match = _ROW_ID_RE.search(surface)
            if match:
                row_id = match.group(1)
                if row_id not in rows_map:
                    rows_map[row_id] = []
                rows_map[row_id].append((surface, cell))

        # Обрабатываем каждую строку
        for row_id, row_cells in rows_map.items():
            try:
                row = await _parse_row_from_cells(row_cells)
                if row is not None:
                    rows.append(row)
            except Exception:
                logger.debug("Не удалось распарсить строку %s", row_id, exc_info=True)
                continue

    except Exception:
        logger.exception("Ошибка при парсинге таблицы Ads Manager")

    return rows


async def _parse_row_from_cells(
    row_cells: list[tuple[str, object]],
) -> ScannedAdRow | None:
    """Извлекает данные объявления из сгруппированных ячеек одной строки.

    Args:
        row_cells: список пар (data-surface, element) для одной строки
    """
    # Извлекаем текст из каждой ячейки по ключам data-surface
    fields: dict[str, str] = {}
    for surface, cell in row_cells:
        for key, field_name in _FIELD_KEYS.items():
            if key in surface:
                text = await _get_first_text(cell)
                # Берём первое непустое значение для каждого поля
                if field_name not in fields or fields[field_name] in ("—", "-", ""):
                    fields[field_name] = text
                break

    ad_name = fields.get("ad_name", "")
    if not ad_name or ad_name in ("—", "-"):
        return None

    # Ищем Ad ID в тексте объявления или имени
    fb_ad_id = ""
    # Собираем весь текст строки для поиска Ad ID
    all_text = " ".join(fields.values())
    ad_id_match = _AD_ID_RE.search(all_text)
    if ad_id_match:
        fb_ad_id = ad_id_match.group()

    if not fb_ad_id:
        return None

    campaign_name = fields.get("campaign_name", "")
    adset_name = fields.get("adset_name", "")
    delivery_status = _detect_delivery_status(fields.get("delivery_status", ""))

    # Извлекаем метрики
    spend = _parse_money(fields.get("spend", ""), Decimal("0"))
    clicks = _parse_int_value(fields.get("clicks", ""))
    cpc = _parse_money_or_none(fields.get("cpc", ""))
    leads = _parse_int_value(fields.get("leads", ""))
    cost_per_lead = _parse_money_or_none(fields.get("cost_per_lead", ""))
    registrations = _parse_int_value(fields.get("registrations", ""))
    cost_per_registration = _parse_money_or_none(fields.get("cost_per_registration", ""))

    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=campaign_name,
        adset_name=adset_name,
        ad_name=ad_name,
        delivery_status=delivery_status,
        spend=spend,
        clicks=clicks,
        cpc=cpc,
        leads=leads,
        cost_per_lead=cost_per_lead,
        registrations=registrations,
        cost_per_registration=cost_per_registration,
        deposits=0,  # Deposits нет в стандартной таблице, считаются из внешних данных
    )


async def _get_first_text(element) -> str:
    """Извлекает первый непустой текстовый узел из элемента."""
    try:
        # Используем JS TreeWalker для точного извлечения текста
        text = await element.evaluate("""(el) => {
            const walk = document.createTreeWalker(
                el, NodeFilter.SHOW_TEXT, null, false
            );
            let n;
            while (n = walk.nextNode()) {
                const txt = n.textContent.trim();
                if (txt) return txt;
            }
            return "—";
        }""")
        return text or "—"
    except Exception:
        return "—"


def _detect_delivery_status(text: str) -> str:
    """Определяет статус объявления по тексту."""
    lowered = text.lower()
    if "active" in lowered or "активно" in lowered:
        return "ACTIVE"
    if "paused" in lowered or "пауза" in lowered:
        return "PAUSED"
    if "not delivering" in lowered or "не показывается" in lowered:
        return "NOT_DELIVERING"
    if "learning" in lowered or "обучение" in lowered:
        return "LEARNING"
    if "выключен" in lowered or "off" in lowered:
        return "OFF"
    return "UNKNOWN"


def _parse_money(text: str, default: Decimal) -> Decimal:
    """Извлекает Decimal из текстовой строки ('$0.15', '0,15 $', '-')."""
    if not text or text.strip() in ("—", "-", "–", "—", "n/a", ""):
        return default
    # Заменяем запятую на точку и убираем пробелы
    cleaned = text.replace(",", ".").replace("\xa0", "").replace(" ", "")
    match = _MONEY_RE.search(cleaned)
    if not match:
        return default
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return default


def _parse_money_or_none(text: str) -> Decimal | None:
    """Извлекает Decimal или None если значение отсутствует."""
    if not text or text.strip() in ("—", "-", "–", "—", "n/a", ""):
        return None
    cleaned = text.replace(",", ".").replace("\xa0", "").replace(" ", "")
    match = _MONEY_RE.search(cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return None


def _parse_int_value(text: str) -> int:
    """Извлекает int из текстовой строки."""
    if not text or text.strip() in ("—", "-", "–", "—", "n/a", ""):
        return 0
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0
