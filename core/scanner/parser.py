# -*- coding: utf-8 -*-
"""Парсер DOM Ads Manager: извлекает строки объявлений через data-surface атрибуты.

Использует структуру Facebook Ads Manager, где каждая ячейка таблицы
имеет атрибут data-surface с ключом вида "table_row:N:field_name".
Также поддерживает обновление таблицы через кнопку «Обновить» без перезагрузки.
"""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, InvalidOperation

from core.browser.humanizer import human_click
from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)

# Регулярка для извлечения числа из строки типа "$0.15", "0,15 $", "0.15"
_MONEY_RE = re.compile(r"[\d]+[.,]?\d*")
# Регулярка для номера строки в data-surface
_ROW_ID_RE = re.compile(r"table_row:(\d+)")
# Регулярка для Ad ID (минимум 5 цифр подряд)
_AD_ID_RE = re.compile(r"\d{5,}")

# Упорядоченные алиасы data-surface → поля ScannedAdRow.
# Более специфичные ключи идут раньше общих, чтобы includes-мэтчинг не путал
# `cost_per_action_type:omni_complete_registration` с `omni_complete_registration`.
_FIELD_ALIASES: tuple[tuple[str, str], ...] = (
    ("cost_per_action_type:omni_complete_registration", "cost_per_registration"),
    ("actions:omni_complete_registration", "registrations"),
    ("omni_complete_registration", "registrations"),
    ("cost_per_action_type:lead", "cost_per_lead"),
    ("actions:lead", "leads"),
    # Сначала разбираем кампанию/адсет, иначе их съедает общий алиас `name`.
    ("forObjectType(campaign_group_name,ADGROUP)", "campaign_name"),
    ("campaign_group_name", "campaign_name"),
    ("forObjectType(campaign_name,ADGROUP)", "adset_name"),
    ("adset_name", "adset_name"),
    ("campaign_name", "adset_name"),
    ("forObjectType(name,ADGROUP)", "ad_name"),
    ("name", "ad_name"),
    ("delivery", "delivery_status"),
    ("budget", "budget"),
    ("reach", "reach"),
    ("impressions", "impressions"),
    # Outbound-метрики идут ДО общего "clicks"/"ctr", иначе substring-матчинг
    # захватит "outbound_clicks_ctr" под алиас "ctr" раньше нужного.
    ("outbound_clicks_ctr", "outbound_ctr"),
    ("cost_per_landing_page_view", "cost_per_landing_page_view"),
    ("landing_page_view", "landing_page_views"),
    ("outbound_clicks", "outbound_clicks"),
    ("ctr", "ctr"),
    ("cost_per_result", "cost_per_result"),
    # В нашем Ads Manager колонка «Результаты» = количество депозитов.
    ("table_cell:results", "deposits"),
    ("results", "deposits"),
    ("spend", "spend"),
    ("clicks", "clicks"),
    ("cpc", "cpc"),
    ("cpm", "cpm"),
    ("frequency", "frequency"),
)

# Числовые/денежные поля, для которых нужна спецлогика извлечения
_NUMERIC_FIELDS = frozenset(
    {
        "spend",
        "reach",
        "impressions",
        "clicks",
        "cpc",
        "ctr",
        "cpm",
        "frequency",
        "leads",
        "cost_per_lead",
        "registrations",
        "cost_per_registration",
        "deposits",
        "cost_per_result",
        "outbound_clicks",
        "outbound_ctr",
        "landing_page_views",
        "cost_per_landing_page_view",
    }
)


def _match_field_name(surface: str) -> str | None:
    """Возвращает поле парсера по data-surface с учётом приоритета алиасов."""
    for key, field_name in _FIELD_ALIASES:
        if key in surface:
            return field_name
    return None


async def refresh_table(page) -> bool:
    """Нажимает кнопку «Обновить» в Ads Manager вместо перезагрузки страницы.

    Перед кликом используется humanized-слой, чтобы действие выглядело
    как поведение живого пользователя.

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
                # Антидетект: клик выполняем через humanized-слой
                await human_click(page, btn)
                logger.info("Нажата кнопка «Обновить данные в таблице»")
                return True

        logger.debug("Кнопка обновления не найдена среди кнопок контейнера")
        return False
    except Exception:
        logger.debug("Ошибка при попытке нажать кнопку обновления", exc_info=True)
        return False


def _build_extraction_js() -> str:
    """Генерирует единый JS-скрипт для извлечения данных всех строк таблицы.

    Возвращает JavaScript, который при выполнении через page.evaluate()
    находит все ячейки с data-surface*="table_row:" и возвращает массив
    объектов с полями для каждой строки. Это заменяет множество отдельных
    IPC-вызовов одним.
    """
    # Сериализуем маппинг полей в JS-объект
    field_aliases_json = json.dumps(list(_FIELD_ALIASES), ensure_ascii=False)
    numeric_fields_json = json.dumps(list(_NUMERIC_FIELDS), ensure_ascii=False)

    return (
        """() => {
    const FIELD_ALIASES = """
        + field_aliases_json
        + """;
    const NUMERIC_FIELDS = new Set("""
        + numeric_fields_json
        + """);
    const ROW_RE = /table_row:(\\d+)/;

    function matchFieldName(surface) {
        for (const [key, fieldName] of FIELD_ALIASES) {
            if (surface.includes(key)) {
                return fieldName;
            }
        }
        return null;
    }

    // Тексты кнопок, которые нужно игнорировать при извлечении ad_name
    const BUTTON_LABELS = new Set([
        'дублировать', 'редактировать', 'удалить', 'предпросмотр',
        'duplicate', 'edit', 'delete', 'preview',
        'открыть раскрывающееся меню', 'open dropdown menu',
        'выкл.', 'вкл.', 'off', 'on',
    ]);

    // Извлечение названия объявления (аналог _get_ad_name)
    function getAdName(el) {
        const link = el.querySelector('a[href]');
        if (link) {
            const t = link.textContent.trim();
            if (t && t.length > 2 && !BUTTON_LABELS.has(t.toLowerCase())) {
                return t;
            }
        }
        const walk = document.createTreeWalker(
            el, NodeFilter.SHOW_TEXT, null, false
        );
        let best = '';
        let n;
        while (n = walk.nextNode()) {
            const t = n.textContent.trim();
            if (!t || BUTTON_LABELS.has(t.toLowerCase())) continue;
            if (t.length > best.length) best = t;
        }
        return best || '\\u2014';
    }

    // Извлечение числовой метрики (аналог _get_metric_text)
    function getMetricText(el) {
        const walk = document.createTreeWalker(
            el, NodeFilter.SHOW_TEXT, null, false
        );
        let best = '';
        let n;
        while (n = walk.nextNode()) {
            const t = n.textContent.trim();
            if (!t) continue;
            if (t.length > 20) continue;
            if (!best) { best = t; continue; }
            const hasDigit = /\\d/.test(t);
            const bestHasDigit = /\\d/.test(best);
            if (hasDigit && !bestHasDigit) { best = t; continue; }
            if (hasDigit === bestHasDigit && t.length < best.length) {
                best = t;
            }
        }
        return best || '\\u2014';
    }

    // Извлечение первого текстового узла (аналог _get_first_text)
    function getFirstText(el) {
        const walk = document.createTreeWalker(
            el, NodeFilter.SHOW_TEXT, null, false
        );
        let n;
        while (n = walk.nextNode()) {
            const txt = n.textContent.trim();
            if (txt) return txt;
        }
        return '\\u2014';
    }

    // Находим все ячейки с data-surface*="table_row:"
    const cells = document.querySelectorAll('[data-surface*="table_row:"]');
    const rowsMap = {};

    for (const cell of cells) {
        const surface = cell.getAttribute('data-surface');
        if (!surface) continue;
        const m = ROW_RE.exec(surface);
        if (!m) continue;
        const rowId = m[1];
        if (!rowsMap[rowId]) rowsMap[rowId] = [];
        rowsMap[rowId].push({surface, cell});
    }

    const result = [];

    for (const [rowId, rowCells] of Object.entries(rowsMap)) {
        const fields = {};
        for (const {surface, cell} of rowCells) {
            const fieldName = matchFieldName(surface);
            if (!fieldName) {
                continue;
            }

            let text;
            if (fieldName === 'ad_name') {
                text = getAdName(cell);
            } else if (NUMERIC_FIELDS.has(fieldName)) {
                text = getMetricText(cell);
            } else {
                text = getFirstText(cell);
            }
            if (!(fieldName in fields)
                || fields[fieldName] === '\\u2014'
                || fields[fieldName] === '-'
                || fields[fieldName] === '') {
                fields[fieldName] = text;
            }
        }
        fields._row_id = rowId;
        result.push(fields);
    }

    return result;
}"""
    )


async def parse_ads_from_page(page) -> list[ScannedAdRow]:
    """Парсит видимые строки таблицы Ads Manager из текущего viewport.

    Основной путь — единый page.evaluate() для извлечения всех данных разом
    (минимум IPC-вызовов). Если единый evaluate не сработал — fallback
    на поэлементный парсинг.

    Args:
        page: Playwright Page, открытая на Ads Manager

    Returns:
        Список ScannedAdRow с нормализованными метриками
    """
    # Основной путь: единый JS-вызов
    try:
        raw_rows = await page.evaluate(_build_extraction_js())
        if raw_rows:
            rows = _parse_bulk_result(raw_rows)
            if rows:
                logger.info(
                    "Парсер (bulk): извлечено %s строк за один evaluate",
                    len(rows),
                )
                return rows
    except Exception:
        logger.debug(
            "Единый evaluate не сработал, переход на fallback",
            exc_info=True,
        )

    # Fallback: поэлементный парсинг через отдельные IPC-вызовы
    return await _parse_ads_fallback(page)


def _parse_bulk_result(raw_rows: list[dict]) -> list[ScannedAdRow]:
    """Преобразует результат единого JS evaluate в список ScannedAdRow."""
    rows: list[ScannedAdRow] = []
    for fields in raw_rows:
        try:
            row = _build_row_from_fields(fields)
            if row is not None:
                rows.append(row)
        except Exception:
            logger.debug(
                "Не удалось построить строку из bulk-данных",
                exc_info=True,
            )
            continue
    return rows


def _build_row_from_fields(fields: dict) -> ScannedAdRow | None:
    """Строит ScannedAdRow из словаря полей (от JS или от fallback)."""
    ad_name = fields.get("ad_name", "")
    if not ad_name or ad_name in ("\u2014", "-"):
        return None

    # FB Ad ID берём из row_id (реальные ID — минимум 10 цифр)
    fb_ad_id = fields.get("_row_id", "")
    if not fb_ad_id or len(fb_ad_id) < 10:
        # Короткий row_id — это порядковый номер строки, не FB Ad ID
        # (например, строка итогов "Результаты, числ..." имеет row_id=12)
        return None
    if not fb_ad_id:
        return None

    campaign_name = fields.get("campaign_name", "")
    adset_name = fields.get("adset_name", "")
    delivery_status = _detect_delivery_status(fields.get("delivery_status", ""))

    spend = _parse_money(fields.get("spend", ""), Decimal("0"))
    budget = fields.get("budget", "")
    reach = _parse_int_value(fields.get("reach", ""))
    impressions = _parse_int_value(fields.get("impressions", ""))
    clicks = _parse_int_value(fields.get("clicks", ""))
    cpc = _parse_money_or_none(fields.get("cpc", ""))
    ctr = _parse_decimal_or_none(fields.get("ctr", ""))
    cost_per_result = _parse_money_or_none(fields.get("cost_per_result", ""))
    cpm = _parse_money_or_none(fields.get("cpm", ""))
    frequency = _parse_decimal_or_none(fields.get("frequency", ""))
    leads = _parse_int_value(fields.get("leads", ""))
    cost_per_lead = _parse_money_or_none(fields.get("cost_per_lead", ""))
    registrations = _parse_int_value(fields.get("registrations", ""))
    cost_per_registration = _parse_money_or_none(fields.get("cost_per_registration", ""))
    deposits = _parse_int_value(fields.get("deposits", ""))
    outbound_clicks = _parse_int_value(fields.get("outbound_clicks", ""))
    outbound_ctr = _parse_decimal_or_none(fields.get("outbound_ctr", ""))
    landing_page_views = _parse_int_value(fields.get("landing_page_views", ""))
    cost_per_landing_page_view = _parse_money_or_none(fields.get("cost_per_landing_page_view", ""))

    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=campaign_name,
        adset_name=adset_name,
        ad_name=ad_name,
        delivery_status=delivery_status,
        spend=spend,
        budget=budget,
        reach=reach,
        impressions=impressions,
        clicks=clicks,
        cpc=cpc,
        ctr=ctr,
        cost_per_result=cost_per_result,
        cpm=cpm,
        frequency=frequency,
        leads=leads,
        cost_per_lead=cost_per_lead,
        registrations=registrations,
        cost_per_registration=cost_per_registration,
        deposits=deposits,
        outbound_clicks=outbound_clicks,
        outbound_ctr=outbound_ctr,
        landing_page_views=landing_page_views,
        cost_per_landing_page_view=cost_per_landing_page_view,
    )


async def _parse_ads_fallback(page) -> list[ScannedAdRow]:
    """Fallback-парсинг: поэлементные IPC-вызовы (старая логика)."""
    rows: list[ScannedAdRow] = []

    try:
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
                logger.debug(
                    "Не удалось распарсить строку %s",
                    row_id,
                    exc_info=True,
                )
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
        field_name = _match_field_name(surface)
        if field_name is None:
            continue
        # Выбираем функцию извлечения текста
        if field_name == "ad_name":
            text = await _get_ad_name(cell)
        elif field_name in _NUMERIC_FIELDS:
            text = await _get_metric_text(cell)
        else:
            text = await _get_first_text(cell)
        if field_name not in fields or fields[field_name] in ("\u2014", "-", ""):
            fields[field_name] = text

    ad_name = fields.get("ad_name", "")
    if not ad_name or ad_name in ("\u2014", "-"):
        return None

    # Ищем Ad ID
    fb_ad_id = ""
    all_text = " ".join(fields.values())
    ad_id_match = _AD_ID_RE.search(all_text)
    if ad_id_match:
        fb_ad_id = ad_id_match.group()

    if not fb_ad_id:
        return None

    campaign_name = fields.get("campaign_name", "")
    adset_name = fields.get("adset_name", "")
    delivery_status = _detect_delivery_status(fields.get("delivery_status", ""))

    spend = _parse_money(fields.get("spend", ""), Decimal("0"))
    budget = fields.get("budget", "")
    reach = _parse_int_value(fields.get("reach", ""))
    impressions = _parse_int_value(fields.get("impressions", ""))
    clicks = _parse_int_value(fields.get("clicks", ""))
    cpc = _parse_money_or_none(fields.get("cpc", ""))
    ctr = _parse_decimal_or_none(fields.get("ctr", ""))
    cost_per_result = _parse_money_or_none(fields.get("cost_per_result", ""))
    cpm = _parse_money_or_none(fields.get("cpm", ""))
    frequency = _parse_decimal_or_none(fields.get("frequency", ""))
    leads = _parse_int_value(fields.get("leads", ""))
    cost_per_lead = _parse_money_or_none(fields.get("cost_per_lead", ""))
    registrations = _parse_int_value(fields.get("registrations", ""))
    cost_per_registration = _parse_money_or_none(fields.get("cost_per_registration", ""))
    deposits = _parse_int_value(fields.get("deposits", ""))
    outbound_clicks = _parse_int_value(fields.get("outbound_clicks", ""))
    outbound_ctr = _parse_decimal_or_none(fields.get("outbound_ctr", ""))
    landing_page_views = _parse_int_value(fields.get("landing_page_views", ""))
    cost_per_landing_page_view = _parse_money_or_none(fields.get("cost_per_landing_page_view", ""))

    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=campaign_name,
        adset_name=adset_name,
        ad_name=ad_name,
        delivery_status=delivery_status,
        spend=spend,
        budget=budget,
        reach=reach,
        impressions=impressions,
        clicks=clicks,
        cpc=cpc,
        ctr=ctr,
        cost_per_result=cost_per_result,
        cpm=cpm,
        frequency=frequency,
        leads=leads,
        cost_per_lead=cost_per_lead,
        registrations=registrations,
        cost_per_registration=cost_per_registration,
        deposits=deposits,
        outbound_clicks=outbound_clicks,
        outbound_ctr=outbound_ctr,
        landing_page_views=landing_page_views,
        cost_per_landing_page_view=cost_per_landing_page_view,
    )


async def _get_ad_name(element) -> str:
    """Извлекает название объявления из ячейки name.

    Facebook помещает название в span/a внутри ячейки, а рядом —
    кнопки «Дублировать», «Редактировать» и т.д. Фильтруем известные
    тексты кнопок и берём самый длинный оставшийся текстовый фрагмент.
    """
    try:
        name = await element.evaluate("""(el) => {
            // Тексты кнопок, которые нужно игнорировать
            const BUTTON_LABELS = new Set([
                'дублировать', 'редактировать', 'удалить', 'предпросмотр',
                'duplicate', 'edit', 'delete', 'preview',
            ]);

            // Ищем ссылку с названием — обычно первый <a> с текстом
            const link = el.querySelector('a[href]');
            if (link) {
                const t = link.textContent.trim();
                if (t && t.length > 2
                    && !BUTTON_LABELS.has(t.toLowerCase())) return t;
            }

            // Fallback: самый длинный текст, исключая кнопки
            const walk = document.createTreeWalker(
                el, NodeFilter.SHOW_TEXT, null, false
            );
            let best = '';
            let n;
            while (n = walk.nextNode()) {
                const t = n.textContent.trim();
                if (!t || BUTTON_LABELS.has(t.toLowerCase())) continue;
                if (t.length > best.length) best = t;
            }
            return best || '\\u2014';
        }""")
        return name or "\u2014"
    except Exception:
        return "\u2014"


async def _get_first_text(element) -> str:
    """Извлекает первый непустой текстовый узел из элемента."""
    try:
        text = await element.evaluate("""(el) => {
            const walk = document.createTreeWalker(
                el, NodeFilter.SHOW_TEXT, null, false
            );
            let n;
            while (n = walk.nextNode()) {
                const txt = n.textContent.trim();
                if (txt) return txt;
            }
            return "\\u2014";
        }""")
        return text or "\u2014"
    except Exception:
        return "\u2014"


async def _get_metric_text(element) -> str:
    """Извлекает числовое/денежное значение из ячейки метрики.

    Facebook оборачивает метрики в контейнеры, рядом с которыми могут быть
    длинные тексты тултипов/подсказок. Берём самый короткий непустой текстовый
    узел, который похож на число или прочерк, игнорируя длинные строки.
    """
    try:
        text = await element.evaluate("""(el) => {
            const walk = document.createTreeWalker(
                el, NodeFilter.SHOW_TEXT, null, false
            );
            let best = '';
            let n;
            while (n = walk.nextNode()) {
                const t = n.textContent.trim();
                if (!t) continue;
                // Пропускаем длинные строки — это тултипы/подсказки
                if (t.length > 20) continue;
                // Если ещё нет кандидата — берём первый короткий текст
                if (!best) { best = t; continue; }
                // Предпочитаем текст, содержащий цифру или прочерк
                const hasDigit = /\\d/.test(t);
                const bestHasDigit = /\\d/.test(best);
                if (hasDigit && !bestHasDigit) { best = t; continue; }
                // При прочих равных берём более короткий
                if (hasDigit === bestHasDigit && t.length < best.length) {
                    best = t;
                }
            }
            return best || '\\u2014';
        }""")
        return text or "\u2014"
    except Exception:
        return "\u2014"


def _detect_delivery_status(text: str) -> str:
    """Определяет статус объявления по тексту."""
    lowered = text.lower()
    if "active" in lowered or "активно" in lowered:
        return "ACTIVE"
    if "paused" in lowered or "пауза" in lowered:
        return "PAUSED"
    if "not delivering" in lowered or "не показывается" in lowered:
        return "NOT_DELIVERING"
    # if "прекращен" in lowered or "stopped" in lowered or "завершен" in lowered:
    #     return "NOT_DELIVERING"
    if "learning" in lowered or "обучение" in lowered:
        return "LEARNING"
    if "выкл" in lowered or "off" in lowered or "disabled" in lowered:
        return "OFF"
    logger.debug("delivery_status UNKNOWN: '%s'", text.strip()[:60])
    return "UNKNOWN"


def _parse_money(text: str, default: Decimal) -> Decimal:
    """Извлекает Decimal из текстовой строки ('$0.15', '0,15 $', '-')."""
    if not text or text.strip() in ("\u2014", "-", "\u2013", "\u2014", "n/a", ""):
        return default
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
    if not text or text.strip() in ("\u2014", "-", "\u2013", "\u2014", "n/a", ""):
        return None
    cleaned = text.replace(",", ".").replace("\xa0", "").replace(" ", "")
    match = _MONEY_RE.search(cleaned)
    if not match:
        return None
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return None


def _parse_decimal_or_none(text: str) -> Decimal | None:
    """Извлекает Decimal из процентной или дробной строки."""
    return _parse_money_or_none(text)


def _parse_int_value(text: str) -> int:
    """Извлекает int из текстовой строки."""
    if not text or text.strip() in ("\u2014", "-", "\u2013", "\u2014", "n/a", ""):
        return 0
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0
