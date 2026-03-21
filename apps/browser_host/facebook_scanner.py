from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

from apps.browser_host.adapters.factory import build_adapter
from apps.browser_host.playwright_attach import PlaywrightAttachService
from apps.browser_host.session_manager import BrowserSessionManager
from core.config import Settings
from core.domain import (
    DeliveryStatus,
    ScopePresence,
    TrackingMode,
    extract_offer_code_from_ad_name,
)
from core.scanner import (
    ScannedAdRow,
    build_adset_scope_key,
    build_campaign_scope_key,
    normalize_delivery_status,
    parse_scanner_decimal,
)

_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "fb_ad_id": ("ad id", "id объявления", "id ad", "adid"),
    "campaign_name": ("campaign", "campaign name", "кампания"),
    "adset_name": ("ad set", "ad set name", "adset", "адсет", "группа объявлений"),
    "ad_name": ("ad name", "ad", "объявление", "название объявления"),
    "delivery_status": ("delivery", "status", "delivery status", "статус", "показ"),
    "spend": ("amount spent", "spend", "расход", "потраченная сумма", "сумма затрат"),
    "clicks": ("clicks", "клики"),
    "cpc": ("cpc", "cpc (all)", "цена за клик"),
    "leads": ("leads", "лиды"),
    "cost_per_lead": ("cost per lead", "цена за лид"),
    "registrations": ("registrations", "registration", "регистрации"),
    "cost_per_registration": ("cost per registration", "цена за регистрацию"),
    "deposits": ("deposits", "deposit", "депозиты"),
}
_REQUIRED_FIELDS = (
    "campaign_name",
    "adset_name",
    "ad_name",
    "delivery_status",
    "spend",
    "clicks",
    "cpc",
    "leads",
    "cost_per_lead",
    "registrations",
    "cost_per_registration",
    "deposits",
)
_FB_AD_ID_PATTERN = re.compile(r"\b\d{8,20}\b")
_NON_ALNUM_PATTERN = re.compile(r"[^a-zа-я0-9]+", re.IGNORECASE)


def _normalize_header_text(value: str) -> str:
    normalized = value.casefold().strip()
    normalized = _NON_ALNUM_PATTERN.sub(" ", normalized)
    return " ".join(part for part in normalized.split() if part)


def _normalize_status_text(value: str) -> str:
    return _normalize_header_text(value)


def _coerce_delivery_status(raw_value: str) -> DeliveryStatus:
    return normalize_delivery_status(_normalize_status_text(raw_value))


def _parse_decimal_value(raw_value: str) -> Decimal | None:
    return parse_scanner_decimal(raw_value)


def _parse_int_value(raw_value: str) -> int:
    value = _parse_decimal_value(raw_value)
    if value is None:
        return 0
    return int(value)


class FacebookAdsScannerProvider:
    """Реальный scanner provider для Facebook Ads Manager через Playwright по CDP."""

    def __init__(
        self,
        settings: Settings,
        browser_session_manager: BrowserSessionManager | None = None,
    ) -> None:
        self._settings = settings
        self._browser_session_manager = browser_session_manager or BrowserSessionManager(
            adapter=build_adapter(settings),
            playwright_attach_service=PlaywrightAttachService(),
        )

    async def scan_rows(self, profile_id: str, browser_host_name: str) -> list[ScannedAdRow]:
        logger = logging.getLogger(__name__)
        logger.info(
            "Запускаю реальный скан Ads Manager для профиля %s на хосте %s",
            profile_id,
            browser_host_name,
        )

        attached_session = await self._browser_session_manager.ensure_session(profile_id)
        try:
            browser = attached_session.browser
            if browser is None:
                if not attached_session.cdp_url:
                    raise RuntimeError(
                        "Не удалось получить CDP endpoint для сканирования Ads Manager"
                    )
                async with self._connect_browser(attached_session.cdp_url) as fallback_browser:
                    return await self._scan_with_browser(fallback_browser)

            return await self._scan_with_browser(browser)
        finally:
            await self._browser_session_manager.release_session(attached_session)

    async def _scan_with_browser(self, browser) -> list[ScannedAdRow]:
        page = await self._resolve_ads_page(browser)
        await page.bring_to_front()
        await self._refresh_page(page)
        header_map = await self._wait_until_table_stable(page)
        rows = await self._scan_full_scope(page, header_map)
        if not rows:
            raise RuntimeError("После стабилизации таблицы не удалось прочитать ни одной строки")
        return rows

    async def _refresh_page(self, page) -> None:
        await page.reload(wait_until="domcontentloaded")

    async def _wait_until_table_stable(self, page) -> dict[str, int]:
        logger = logging.getLogger(__name__)
        previous_signature: tuple[tuple[tuple[str, int], ...], int] | None = None
        stable_hits = 0
        max_attempts = max(self._settings.scanner_stabilize_attempts * 5, 10)

        for attempt in range(1, max_attempts + 1):
            await page.wait_for_timeout(self._settings.scanner_stabilize_delay_ms)
            if await self._has_busy_state(page):
                stable_hits = 0
                continue

            header_map = await self._extract_header_map(page)
            row_count = await self._count_visible_rows(page)
            if row_count == 0 or not self._has_required_columns(header_map):
                stable_hits = 0
                continue

            signature = (tuple(sorted(header_map.items())), row_count)
            if signature == previous_signature:
                stable_hits += 1
            else:
                stable_hits = 1
                previous_signature = signature

            logger.info(
                "Проверка стабилизации таблицы: попытка %s, строк %s, стабильных совпадений %s",
                attempt,
                row_count,
                stable_hits,
            )
            if stable_hits >= self._settings.scanner_stabilize_attempts:
                return header_map

        raise RuntimeError("Таблица Ads Manager не стабилизировалась после обновления страницы")

    async def _scan_full_scope(self, page, header_map: dict[str, int]) -> list[ScannedAdRow]:
        rows_by_id: dict[str, ScannedAdRow] = {}
        no_new_attempts = 0

        while no_new_attempts < self._settings.scanner_max_no_new_attempts:
            visible_rows = await self._parse_visible_rows(page, header_map)
            before_count = len(rows_by_id)
            for row in visible_rows:
                rows_by_id.setdefault(row.fb_ad_id, row)

            if len(rows_by_id) == before_count:
                no_new_attempts += 1
            else:
                no_new_attempts = 0

            if not await self._scroll_once(page):
                no_new_attempts += 1
            await page.wait_for_timeout(self._settings.scanner_scroll_pause_ms)

        return list(rows_by_id.values())

    async def _parse_visible_rows(self, page, header_map: dict[str, int]) -> list[ScannedAdRow]:
        row_locator = page.locator("[role='row']")
        row_count = await row_locator.count()
        parsed_rows: list[ScannedAdRow] = []

        for index in range(row_count):
            row = row_locator.nth(index)
            cell_locator = row.locator("[role='gridcell'], [role='cell']")
            cell_count = await cell_locator.count()
            if cell_count < len(_REQUIRED_FIELDS):
                continue

            cell_values = [text.strip() for text in await cell_locator.all_inner_texts()]
            mapped_values = self._map_cells_by_field(cell_values, header_map)
            fb_ad_id = await self._extract_fb_ad_id(row, mapped_values)
            if fb_ad_id is None:
                continue

            campaign_name = mapped_values["campaign_name"]
            adset_name = mapped_values["adset_name"]
            ad_name = mapped_values["ad_name"]
            delivery_status_raw = mapped_values["delivery_status"]
            delivery_status = _coerce_delivery_status(delivery_status_raw)
            campaign_scope_key = build_campaign_scope_key(campaign_name)
            parsed_rows.append(
                ScannedAdRow(
                    fb_ad_id=fb_ad_id,
                    campaign_scope_key=campaign_scope_key,
                    adset_scope_key=build_adset_scope_key(adset_name, campaign_scope_key),
                    campaign_name=campaign_name,
                    adset_name=adset_name,
                    ad_name=ad_name,
                    delivery_status=delivery_status,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=_parse_decimal_value(mapped_values["spend"]) or Decimal("0"),
                    clicks=_parse_int_value(mapped_values["clicks"]),
                    cpc=_parse_decimal_value(mapped_values["cpc"]),
                    leads=_parse_int_value(mapped_values["leads"]),
                    cost_per_lead=_parse_decimal_value(mapped_values["cost_per_lead"]),
                    registrations=_parse_int_value(mapped_values["registrations"]),
                    cost_per_registration=_parse_decimal_value(
                        mapped_values["cost_per_registration"]
                    ),
                    deposits=_parse_int_value(mapped_values["deposits"]),
                    last_seen_at=datetime.now(tz=UTC),
                    resolved_offer_code=extract_offer_code_from_ad_name(ad_name),
                )
            )

        return parsed_rows

    def _map_cells_by_field(
        self, cell_values: list[str], header_map: dict[str, int]
    ) -> dict[str, str]:
        mapped: dict[str, str] = {}
        for field_name in _REQUIRED_FIELDS:
            column_index = header_map[field_name]
            mapped[field_name] = (
                cell_values[column_index].strip() if column_index < len(cell_values) else ""
            )
        if "fb_ad_id" in header_map:
            column_index = header_map["fb_ad_id"]
            mapped["fb_ad_id"] = (
                cell_values[column_index].strip() if column_index < len(cell_values) else ""
            )
        return mapped

    async def _extract_fb_ad_id(self, row, mapped_values: dict[str, str]) -> str | None:
        column_value = mapped_values.get("fb_ad_id")
        if column_value:
            direct_match = _FB_AD_ID_PATTERN.search(column_value)
            if direct_match is not None:
                return direct_match.group(0)

        for attribute_name in ("data-id", "data-key", "data-item-id", "id"):
            attribute_value = await row.get_attribute(attribute_name)
            if not attribute_value:
                continue
            attribute_match = _FB_AD_ID_PATTERN.search(attribute_value)
            if attribute_match is not None:
                return attribute_match.group(0)

        link_locator = row.locator("a")
        link_count = await link_locator.count()
        for index in range(link_count):
            href = await link_locator.nth(index).get_attribute("href")
            if not href:
                continue
            from_href = self._extract_fb_ad_id_from_href(href)
            if from_href is not None:
                return from_href

        full_text = await row.inner_text()
        text_match = _FB_AD_ID_PATTERN.search(full_text)
        return text_match.group(0) if text_match is not None else None

    @staticmethod
    def _extract_fb_ad_id_from_href(href: str) -> str | None:
        parsed = urlparse(href)
        query = parse_qs(parsed.query)
        for key in ("selected_ad_ids", "ad_id", "asset_id", "id"):
            values = query.get(key)
            if not values:
                continue
            match = _FB_AD_ID_PATTERN.search(values[0])
            if match is not None:
                return match.group(0)
        href_match = _FB_AD_ID_PATTERN.search(href)
        return href_match.group(0) if href_match is not None else None

    async def _extract_header_map(self, page) -> dict[str, int]:
        headers = page.locator("[role='columnheader']")
        header_values = [text.strip() for text in await headers.all_inner_texts()]
        normalized_headers = [_normalize_header_text(text) for text in header_values]
        resolved_map: dict[str, int] = {}
        for field_name, aliases in _HEADER_ALIASES.items():
            for index, header in enumerate(normalized_headers):
                if any(alias == header or alias in header for alias in aliases):
                    resolved_map[field_name] = index
                    break
        return resolved_map

    @staticmethod
    def _has_required_columns(header_map: dict[str, int]) -> bool:
        return all(field_name in header_map for field_name in _REQUIRED_FIELDS)

    async def _count_visible_rows(self, page) -> int:
        row_locator = page.locator("[role='row']")
        count = await row_locator.count()
        return max(count - 1, 0)

    async def _has_busy_state(self, page) -> bool:
        busy_locator = page.locator(
            "[aria-busy='true'], [role='progressbar'], [data-visualcompletion='loading-state']"
        )
        return await busy_locator.count() > 0

    async def _scroll_once(self, page) -> bool:
        row_locator = page.locator("[role='row']")
        row_count = await row_locator.count()
        if row_count <= 1:
            return False
        last_row = row_locator.nth(row_count - 1)
        await last_row.scroll_into_view_if_needed()
        await page.mouse.wheel(0, 2500)
        return True

    async def _resolve_ads_page(self, browser):
        candidates = []
        for context in browser.contexts:
            for page in context.pages:
                score = 0
                url = page.url or ""
                lowered_url = url.casefold()
                if "adsmanager" in lowered_url:
                    score += 10
                if "facebook.com" in lowered_url or "business.facebook.com" in lowered_url:
                    score += 3
                try:
                    title = (await page.title()).casefold()
                except Exception:  # noqa: BLE001
                    title = ""
                if "ads manager" in title or "менеджер рекламы" in title:
                    score += 5
                candidates.append((score, page))

        if not candidates:
            raise RuntimeError("В подключенном браузере нет открытых страниц для сканирования")

        best_score, best_page = max(candidates, key=lambda item: item[0])
        if best_score <= 0:
            raise RuntimeError(
                "Не удалось найти открытую страницу Facebook Ads Manager для текущего профиля"
            )
        await best_page.wait_for_load_state("domcontentloaded")
        return best_page

    @staticmethod
    def _connect_browser(cdp_url: str):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _manager():
            try:
                from playwright.async_api import async_playwright
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "Для реального сканирования не установлен Playwright. Установите зависимости проекта заново."
                ) from exc

            async with async_playwright() as playwright:
                browser = None
                try:
                    browser = await playwright.chromium.connect_over_cdp(cdp_url)
                except Exception as exc:  # noqa: BLE001
                    raise RuntimeError(
                        f"Не удалось подключиться к браузеру Vision по CDP: {exc}"
                    ) from exc

                yield browser

        return _manager()
