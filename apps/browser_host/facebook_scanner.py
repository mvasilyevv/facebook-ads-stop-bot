from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

from apps.browser_host.adapters.factory import build_adapter
from apps.browser_host.facebook_popups import dismiss_known_ads_manager_popups
from apps.browser_host.facebook_response_probe import (
    FacebookResponseProbeService,
    ResponsePayloadEntry,
)
from apps.browser_host.facebook_service_page import (
    SCANNER_SERVICE_PAGE,
    ensure_ads_manager_service_page,
    is_ads_manager_service_url,
)
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
    ScannerScopeUnavailableError,
    build_adset_scope_key,
    build_campaign_scope_key,
    normalize_delivery_status,
    parse_scanner_decimal,
)

_PRESENTATION_ROW_SELECTOR = "div[role='presentation']._1gd4"
_PRESENTATION_CELL_SELECTOR = "div._4lg0"
_HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "fb_ad_id": ("ad id", "id объявления", "id ad", "adid"),
    "campaign_name": ("campaign", "campaign name", "кампания", "название кампании"),
    "adset_name": (
        "ad set",
        "ad set name",
        "adset name",
        "adset",
        "адсет",
        "группа объявлений",
        "название группы объявлений",
    ),
    "ad_name": ("ad name", "объявление", "название объявления", "рекламу"),
    "delivery_status": (
        "delivery",
        "status",
        "delivery status",
        "статус",
        "статус показа",
        "показ",
    ),
    "spend": ("amount spent", "spend", "расход", "потраченная сумма", "сумма затрат"),
    "clicks": ("clicks", "clicks all", "клики", "клики все"),
    "cpc": ("cpc", "cpc all", "cpc (all)", "цена за клик", "cpc все"),
    "leads": ("leads", "лиды"),
    "cost_per_lead": ("cost per lead", "цена за лид"),
    "registrations": (
        "registrations",
        "registration",
        "completed registrations",
        "регистрации",
        "завершенные регистрации",
    ),
    "cost_per_registration": (
        "cost per registration",
        "cost per completed registration",
        "цена за регистрацию",
        "цена за завершенную регистрацию",
    ),
    "deposits": ("deposits", "deposit", "депозиты"),
}
_PRESENTATION_REQUIRED_FIELDS = (
    "ad_name",
    "delivery_status",
    "spend",
    "clicks",
    "cpc",
    "leads",
    "cost_per_lead",
    "registrations",
    "cost_per_registration",
)
_FB_AD_ID_PATTERN = re.compile(r"\b\d{8,20}\b")
_SURFACE_ROW_ID_PATTERN = re.compile(r"table_row:(\d{8,20})unit")
_NON_ALNUM_PATTERN = re.compile(r"[^a-zа-я0-9]+", re.IGNORECASE)
_PLACEHOLDER_CAMPAIGN_NAME_PATTERN = re.compile(
    r"^(?:кампания|campaign)\s+\d{8,20}$", re.IGNORECASE
)
_PLACEHOLDER_CAMPAIGN_BY_AD_PATTERN = re.compile(
    r"^(?:кампания объявления|campaign ad)\s+\d{8,20}$",
    re.IGNORECASE,
)
_PLACEHOLDER_ADSET_NAME_PATTERN = re.compile(r"^(?:адсет|adset)\s+\d{8,20}$", re.IGNORECASE)
_PLACEHOLDER_AD_NAME_PATTERN = re.compile(r"^(?:объявление|ad)\s+\d{8,20}$", re.IGNORECASE)
_MAX_HEADER_DISTANCE_PX = 120
_HORIZONTAL_PASS_PADDING_PX = 200
_RESPONSE_SETTLE_ATTEMPTS = 3
_EXPECTED_ROWS_TITLE_PATTERN = re.compile(r"^\((\d{1,6})\)")
_EXPECTED_ROWS_RESULTS_PATTERN = re.compile(
    r"(?:результаты,\s*число объявлений|results,\s*number of ads)\s*:\s*(\d{1,6})",
    re.IGNORECASE,
)
_SCOPE_SERVICE_TEXT_MARKERS = (
    "активные объявления",
    "active ads",
)
_GRAPHQL_NODE_TYPES = {"Adgroup", "AdCampaign", "AdCampaignGroup"}


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


def _extract_numeric_identifier(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    match = _FB_AD_ID_PATTERN.search(raw_value)
    return match.group(0) if match is not None else None


def _extract_mapped_value(mapped_values: dict[str, str], field_name: str) -> str | None:
    for key, value in mapped_values.items():
        if _matches_header_alias(field_name, key):
            return value
    return None


def _extract_mapped_identifier(mapped_values: dict[str, str], *field_names: str) -> str | None:
    for field_name in field_names:
        identifier = _extract_numeric_identifier(
            mapped_values.get(_normalize_header_text(field_name))
        )
        if identifier is not None:
            return identifier
    return None


def _matches_header_alias(field_name: str, normalized_header: str) -> bool:
    strict_fields = {"campaign_name", "adset_name"}

    for alias in _HEADER_ALIASES[field_name]:
        normalized_alias = _normalize_header_text(alias)
        if normalized_header == normalized_alias:
            return True
        if field_name in strict_fields:
            continue
        if normalized_header.startswith(f"{normalized_alias} "):
            return True
        if normalized_alias.startswith(f"{normalized_header} "):
            return True
    return False


def _sanitize_cell_value(field_name: str, raw_value: str) -> str:
    lines = [line.strip() for line in raw_value.splitlines() if line.strip()]
    if not lines:
        return raw_value.strip()
    if field_name in {"ad_name", "delivery_status"}:
        return lines[0]
    if field_name in {"campaign_name", "adset_name"}:
        meaningful_lines = [
            line
            for line in lines
            if not any(
                marker in _normalize_header_text(line) for marker in _SCOPE_SERVICE_TEXT_MARKERS
            )
        ]
        if not meaningful_lines:
            return ""
        return " ".join(meaningful_lines)
    return "\n".join(lines)


def _is_scope_name_usable(field_name: str, value: str | None) -> bool:
    if value is None:
        return False
    normalized = _normalize_header_text(value)
    if not normalized or normalized == "unknown":
        return False
    if field_name == "adset_name" and (
        "активные объявления" in normalized or "active ads" in normalized
    ):
        return False
    return True


def _is_placeholder_scope_name(field_name: str, value: str | None) -> bool:
    if value is None:
        return False
    normalized_value = value.strip()
    if field_name == "campaign_name":
        return bool(
            _PLACEHOLDER_CAMPAIGN_NAME_PATTERN.fullmatch(normalized_value)
            or _PLACEHOLDER_CAMPAIGN_BY_AD_PATTERN.fullmatch(normalized_value)
        )
    if field_name == "adset_name":
        return bool(_PLACEHOLDER_ADSET_NAME_PATTERN.fullmatch(normalized_value))
    return False


def _is_placeholder_ad_name(value: str | None) -> bool:
    if value is None:
        return False
    return bool(_PLACEHOLDER_AD_NAME_PATTERN.fullmatch(value.strip()))


def _build_horizontal_pass_starts(
    header_positions: list[int],
    client_width: int,
    max_scroll_left: int,
) -> tuple[int, ...]:
    if client_width <= 0 or max_scroll_left <= 0:
        return (0,)

    passes = [0]
    coverage_end = client_width

    for position in sorted(set(header_positions)):
        if position <= coverage_end - _HORIZONTAL_PASS_PADDING_PX:
            continue
        target = min(max(position - _HORIZONTAL_PASS_PADDING_PX, 0), max_scroll_left)
        if abs(target - passes[-1]) < 100:
            continue
        passes.append(target)
        coverage_end = target + client_width

    if (
        passes[-1] != max_scroll_left
        and max_scroll_left > coverage_end - _HORIZONTAL_PASS_PADDING_PX
    ):
        passes.append(max_scroll_left)

    return tuple(passes)


def _describe_reload_attempts(reload_attempts: int) -> str:
    if reload_attempts <= 1:
        return "после одного обновления страницы"
    return f"после повторных обновлений страницы (попыток: {reload_attempts})"


@dataclass(slots=True)
class _CapturedResponseRow:
    """Сырая строка, собранная из response Ads Manager."""

    fb_ad_id: str
    adset_id: str | None = None
    campaign_id: str | None = None
    campaign_name: str | None = None
    adset_name: str | None = None
    ad_name: str | None = None
    delivery_status: str | None = None
    spend: str | None = None
    clicks: str | None = None
    cpc: str | None = None
    leads: str | None = None
    cost_per_lead: str | None = None
    registrations: str | None = None
    cost_per_registration: str | None = None
    deposits: str | None = None

    def merge(self, mapped_values: dict[str, str]) -> None:
        """Дополняет строку новыми значениями, не перетирая уже найденные."""

        self.campaign_name = self.campaign_name or _extract_mapped_value(
            mapped_values,
            "campaign_name",
        )
        self.adset_name = self.adset_name or _extract_mapped_value(mapped_values, "adset_name")
        self.ad_name = self.ad_name or _extract_mapped_value(mapped_values, "ad_name")
        self.delivery_status = self.delivery_status or _extract_mapped_value(
            mapped_values,
            "delivery_status",
        )
        self.spend = self.spend or _extract_mapped_value(mapped_values, "spend")
        self.clicks = self.clicks or _extract_mapped_value(mapped_values, "clicks")
        self.cpc = self.cpc or _extract_mapped_value(mapped_values, "cpc")
        self.leads = self.leads or _extract_mapped_value(mapped_values, "leads")
        self.cost_per_lead = self.cost_per_lead or _extract_mapped_value(
            mapped_values,
            "cost_per_lead",
        )
        self.registrations = self.registrations or _extract_mapped_value(
            mapped_values,
            "registrations",
        )
        self.cost_per_registration = self.cost_per_registration or _extract_mapped_value(
            mapped_values,
            "cost_per_registration",
        )
        self.deposits = self.deposits or _extract_mapped_value(mapped_values, "deposits")


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
        self._response_probe = FacebookResponseProbeService(
            enabled=settings.scanner_response_probe_enabled,
            output_dir=settings.scanner_response_probe_dir,
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
                    return await self._scan_with_browser(
                        fallback_browser,
                        profile_id=profile_id,
                        browser_host_name=browser_host_name,
                    )

            return await self._scan_with_browser(
                browser,
                profile_id=profile_id,
                browser_host_name=browser_host_name,
            )
        finally:
            await self._browser_session_manager.release_session(attached_session)

    async def _scan_with_browser(
        self,
        browser,
        *,
        profile_id: str,
        browser_host_name: str,
    ) -> list[ScannedAdRow]:
        page = await self._resolve_ads_page(browser)
        await self._dismiss_known_popups(page, stage="перед началом сканирования")
        rows = await self._scan_rows_from_responses(
            page,
            profile_id=profile_id,
            browser_host_name=browser_host_name,
        )
        if not rows:
            raise ScannerScopeUnavailableError(
                "Не удалось получить полный набор строк Ads Manager после одного обновления страницы"
            )
        return rows

    async def _scan_rows_from_responses(
        self,
        page,
        *,
        profile_id: str,
        browser_host_name: str,
    ) -> list[ScannedAdRow]:
        logger = logging.getLogger(__name__)
        page_url = page.url
        page_title = await self._read_page_title(page)
        expected_rows_count = await self._read_expected_rows_count(page, page_title)
        reload_attempts = max(getattr(self._settings, "scanner_reload_attempts", 1), 1)
        if expected_rows_count is not None:
            logger.info(
                "Ожидаемое число строк Ads Manager по интерфейсу страницы: %s",
                expected_rows_count,
            )

        logger.info(
            "Получаю response Ads Manager с ретраями reload: максимум %s попыток",
            reload_attempts,
        )
        best_rows: list[ScannedAdRow] = []
        best_response_entries: list[ResponsePayloadEntry] = []
        last_exception: Exception | None = None

        for attempt_index in range(reload_attempts):
            attempt_number = attempt_index + 1
            if attempt_index > 0:
                logger.warning(
                    "Повторяю сбор полного scope Ads Manager: попытка %s из %s",
                    attempt_number,
                    reload_attempts,
                )
                await self._wait_before_reload_retry(page)

            try:
                rows, response_entries = await self._reload_and_parse_response_rows(
                    page,
                    page_url,
                    expected_rows_count=expected_rows_count,
                )
            except Exception as exc:  # noqa: BLE001
                last_exception = exc
                logger.warning(
                    "Не удалось получить response Ads Manager на попытке %s из %s: %s",
                    attempt_number,
                    reload_attempts,
                    exc,
                )
                continue

            if len(rows) >= len(best_rows):
                best_rows = rows
                best_response_entries = response_entries

            if rows and self._is_complete_response_rows(rows, expected_rows_count):
                logger.info(
                    "Response Ads Manager успешно собран на попытке %s из %s: строк %s",
                    attempt_number,
                    reload_attempts,
                    len(rows),
                )
                return rows

            logger.warning(
                "Попытка %s из %s не дала полный scope Ads Manager: строк %s при ожидаемых %s",
                attempt_number,
                reload_attempts,
                len(rows),
                expected_rows_count if expected_rows_count is not None else "неизвестно",
            )

        report_path = await self._response_probe.write_incomplete_scope_report(
            profile_id=profile_id,
            browser_host_name=browser_host_name,
            page_url=page_url,
            page_title=page_title,
            expected_rows_count=expected_rows_count,
            response_entries=best_response_entries,
            parsed_rows=best_rows,
        )
        if report_path is not None:
            logger.warning("Диагностический отчет response Ads Manager сохранен: %s", report_path)

        if best_rows:
            tolerated_rows_gap = max(
                int(getattr(self._settings, "scanner_scope_tolerance_rows", 0)),
                0,
            )
            if (
                expected_rows_count is not None
                and tolerated_rows_gap > 0
                and expected_rows_count > len(best_rows)
                and expected_rows_count - len(best_rows) <= tolerated_rows_gap
            ):
                logger.warning(
                    "Ads Manager не дотянул до полного scope %s строк, продолжаю с частичным набором в пределах допуска",
                    expected_rows_count - len(best_rows),
                )
                return best_rows
            logger.warning(
                "После всех retry-попыток Ads Manager собрано только %s строк при ожидаемых %s",
                len(best_rows),
                expected_rows_count if expected_rows_count is not None else "неизвестно",
            )
        else:
            logger.warning("Response Ads Manager не дал строк после всех retry-попыток")
            if last_exception is not None:
                logger.warning(
                    "Последняя ошибка во время retry-сбора Ads Manager: %s", last_exception
                )

        reload_attempts_suffix = _describe_reload_attempts(reload_attempts)
        if expected_rows_count is not None:
            raise ScannerScopeUnavailableError(
                "Не удалось получить полный набор строк Ads Manager: "
                f"получено меньше ожидаемых {expected_rows_count} строк {reload_attempts_suffix}"
            )
        raise ScannerScopeUnavailableError(
            f"Не удалось получить полный набор строк Ads Manager {reload_attempts_suffix}"
        )

    async def _reload_and_parse_response_rows(
        self,
        page,
        page_url: str,
        *,
        expected_rows_count: int | None,
    ) -> tuple[list[ScannedAdRow], list[ResponsePayloadEntry]]:
        logger = logging.getLogger(__name__)
        captured_responses: list[Any] = []
        response_handler = self._build_response_handler(captured_responses)
        add_listener = getattr(page, "on", None)
        remove_listener = getattr(page, "remove_listener", None)
        off_listener = getattr(page, "off", None)

        if add_listener is None:
            raise RuntimeError("Страница Ads Manager не поддерживает перехват response-событий")

        add_listener("response", response_handler)
        rows: list[ScannedAdRow] = []
        response_entries: list[ResponsePayloadEntry] = []
        merged_count = 0
        no_new_attempts = 0
        same_page_retry_passes = 0
        max_same_page_retry_passes = max(
            getattr(self._settings, "scanner_same_page_retry_passes", 0),
            0,
        )
        accumulated_visible_rows: list[ScannedAdRow] = []
        try:
            await page.reload(wait_until="domcontentloaded")
            await self._wait_for_response_settle(page)
            await self._dismiss_known_popups(page, stage="после обновления страницы")

            while True:
                response_entries = await self._resolve_response_payloads(captured_responses)
                response_rows = self._parse_response_rows(
                    [entry.payload for entry in response_entries if entry.is_relevant],
                    page_url,
                )
                graphql_rows = self._parse_graphql_rows(
                    [entry.payload for entry in response_entries],
                    page_url,
                )
                await self._dismiss_known_popups(page, stage="перед чтением текущего окна таблицы")
                visible_rows = await self._parse_current_view_rows(page, page_url)
                accumulated_visible_rows = self._merge_scanned_rows(
                    accumulated_visible_rows,
                    visible_rows,
                )
                rows = self._merge_scanned_rows(
                    response_rows,
                    graphql_rows,
                    accumulated_visible_rows,
                )

                if len(rows) > merged_count:
                    merged_count = len(rows)
                    no_new_attempts = 0
                    logger.info(
                        "Собрано строк Ads Manager: response=%s, graphql=%s, таблица=%s, всего=%s",
                        len(response_rows),
                        len(graphql_rows),
                        len(visible_rows),
                        len(rows),
                    )
                else:
                    no_new_attempts += 1

                is_scope_complete = self._is_complete_response_rows(rows, expected_rows_count)
                has_unresolved_scope_rows = self._has_unresolved_scope_rows(rows)
                if is_scope_complete and not has_unresolved_scope_rows:
                    return rows, response_entries

                if is_scope_complete and has_unresolved_scope_rows and accumulated_visible_rows:
                    logger.info(
                        "Полный scope по количеству уже собран, продолжаю прокрутку для уточнения campaign/adset имен"
                    )
                elif is_scope_complete:
                    return rows, response_entries

                if no_new_attempts >= self._settings.scanner_max_no_new_attempts:
                    if same_page_retry_passes < max_same_page_retry_passes:
                        same_page_retry_passes += 1
                        logger.info(
                            "Повторяю проход по таблице без обновления страницы: попытка %s из %s",
                            same_page_retry_passes,
                            max_same_page_retry_passes,
                        )
                        await self._restart_vertical_collection_pass(page)
                        no_new_attempts = 0
                        continue
                    return rows, response_entries

                scrolled = await self._scroll_once(page)
                if not scrolled:
                    if same_page_retry_passes < max_same_page_retry_passes:
                        same_page_retry_passes += 1
                        logger.info(
                            "Таблица достигла границы, повторяю проход без обновления страницы: попытка %s из %s",
                            same_page_retry_passes,
                            max_same_page_retry_passes,
                        )
                        await self._restart_vertical_collection_pass(page)
                        no_new_attempts = 0
                        continue
                    return rows, response_entries
                await self._wait_for_scroll_settle(page)
                await self._dismiss_known_popups(page, stage="после прокрутки таблицы")
                await self._wait_for_response_settle(page)
        finally:
            if remove_listener is not None:
                with contextlib.suppress(Exception):
                    remove_listener("response", response_handler)
            elif off_listener is not None:
                with contextlib.suppress(Exception):
                    off_listener("response", response_handler)
        return rows, response_entries

    async def _parse_current_view_rows(
        self,
        page,
        page_url: str,
    ) -> list[ScannedAdRow]:
        await self._dismiss_known_popups(page, stage="перед разбором видимых строк")
        try:
            header_map = await self._extract_header_map(page)
        except Exception:  # noqa: BLE001
            return []
        if not self._has_required_columns(header_map, _PRESENTATION_REQUIRED_FIELDS):
            return []
        return await self._parse_visible_rows(
            page=page,
            header_map=header_map,
            page_url=page_url,
        )

    async def _wait_for_scroll_settle(self, page) -> None:
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if wait_for_timeout is None:
            return
        await wait_for_timeout(self._settings.scanner_scroll_pause_ms)

    async def _wait_before_reload_retry(self, page) -> None:
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if wait_for_timeout is None:
            return
        await wait_for_timeout(max(getattr(self._settings, "scanner_retry_delay_ms", 0), 0))

    async def _restart_vertical_collection_pass(self, page) -> None:
        await self._wait_before_reload_retry(page)
        await self._scroll_vertical_area_to_edge(page, edge="top")
        await self._wait_for_scroll_settle(page)
        await self._dismiss_known_popups(page, stage="перед повторным проходом по таблице")

    async def _dismiss_known_popups(self, page, *, stage: str) -> bool:
        dismissed = await dismiss_known_ads_manager_popups(page)
        if dismissed:
            logging.getLogger(__name__).info(
                "Закрываю блокирующее окно Ads Manager %s",
                stage,
            )
        return dismissed

    @staticmethod
    def _merge_scanned_rows(*row_groups: list[ScannedAdRow]) -> list[ScannedAdRow]:
        rows_by_id: dict[str, ScannedAdRow] = {}
        for row_group in row_groups:
            for row in row_group:
                current_row = rows_by_id.get(row.fb_ad_id)
                if current_row is None:
                    rows_by_id[row.fb_ad_id] = row
                    continue
                rows_by_id[row.fb_ad_id] = FacebookAdsScannerProvider._merge_scanned_row_pair(
                    current_row,
                    row,
                )
        return list(rows_by_id.values())

    @staticmethod
    def _merge_scanned_row_pair(
        current_row: ScannedAdRow, candidate_row: ScannedAdRow
    ) -> ScannedAdRow:
        campaign_name = FacebookAdsScannerProvider._prefer_scope_name(
            "campaign_name",
            current_row.campaign_name,
            candidate_row.campaign_name,
        )
        adset_name = FacebookAdsScannerProvider._prefer_scope_name(
            "adset_name",
            current_row.adset_name,
            candidate_row.adset_name,
        )
        ad_name = FacebookAdsScannerProvider._prefer_ad_name(
            current_row.ad_name,
            candidate_row.ad_name,
        )
        campaign_scope_key = build_campaign_scope_key(campaign_name)
        return ScannedAdRow(
            fb_ad_id=current_row.fb_ad_id,
            campaign_scope_key=campaign_scope_key,
            adset_scope_key=build_adset_scope_key(
                adset_name or current_row.fb_ad_id,
                campaign_scope_key,
            ),
            campaign_name=campaign_name,
            adset_name=adset_name,
            ad_name=ad_name,
            delivery_status=FacebookAdsScannerProvider._prefer_delivery_status(
                current_row.delivery_status,
                candidate_row.delivery_status,
            ),
            tracking_mode=candidate_row.tracking_mode,
            scope_presence=candidate_row.scope_presence,
            spend=FacebookAdsScannerProvider._prefer_decimal_metric(
                current_row.spend,
                candidate_row.spend,
            ),
            clicks=FacebookAdsScannerProvider._prefer_int_metric(
                current_row.clicks,
                candidate_row.clicks,
            ),
            cpc=FacebookAdsScannerProvider._prefer_optional_decimal_metric(
                current_row.cpc,
                candidate_row.cpc,
            ),
            leads=FacebookAdsScannerProvider._prefer_int_metric(
                current_row.leads,
                candidate_row.leads,
            ),
            cost_per_lead=FacebookAdsScannerProvider._prefer_optional_decimal_metric(
                current_row.cost_per_lead,
                candidate_row.cost_per_lead,
            ),
            registrations=FacebookAdsScannerProvider._prefer_int_metric(
                current_row.registrations,
                candidate_row.registrations,
            ),
            cost_per_registration=FacebookAdsScannerProvider._prefer_optional_decimal_metric(
                current_row.cost_per_registration,
                candidate_row.cost_per_registration,
            ),
            deposits=FacebookAdsScannerProvider._prefer_int_metric(
                current_row.deposits,
                candidate_row.deposits,
            ),
            last_seen_at=FacebookAdsScannerProvider._prefer_last_seen_at(
                current_row.last_seen_at,
                candidate_row.last_seen_at,
            ),
            account_name=candidate_row.account_name or current_row.account_name,
            resolved_offer_id=candidate_row.resolved_offer_id or current_row.resolved_offer_id,
            resolved_offer_code=(
                candidate_row.resolved_offer_code or current_row.resolved_offer_code
            ),
        )

    @staticmethod
    def _prefer_scope_name(
        field_name: str,
        current_value: str,
        candidate_value: str,
    ) -> str:
        if not _is_scope_name_usable(field_name, candidate_value):
            return current_value
        if not _is_scope_name_usable(field_name, current_value):
            return candidate_value
        if _is_placeholder_scope_name(
            field_name, candidate_value
        ) and not _is_placeholder_scope_name(
            field_name,
            current_value,
        ):
            return current_value
        return candidate_value

    @staticmethod
    def _prefer_ad_name(current_value: str, candidate_value: str) -> str:
        if not candidate_value.strip():
            return current_value
        if not current_value.strip():
            return candidate_value
        if _is_placeholder_ad_name(candidate_value) and not _is_placeholder_ad_name(current_value):
            return current_value
        return candidate_value

    @staticmethod
    def _prefer_delivery_status(
        current_value: DeliveryStatus,
        candidate_value: DeliveryStatus,
    ) -> DeliveryStatus:
        if candidate_value == DeliveryStatus.UNKNOWN and current_value != DeliveryStatus.UNKNOWN:
            return current_value
        return candidate_value

    @staticmethod
    def _prefer_decimal_metric(current_value: Decimal, candidate_value: Decimal) -> Decimal:
        if not candidate_value.is_zero() or current_value.is_zero():
            return candidate_value
        return current_value

    @staticmethod
    def _prefer_optional_decimal_metric(
        current_value: Decimal | None,
        candidate_value: Decimal | None,
    ) -> Decimal | None:
        if candidate_value is None:
            return current_value
        if current_value is None or not candidate_value.is_zero() or current_value.is_zero():
            return candidate_value
        return current_value

    @staticmethod
    def _prefer_int_metric(current_value: int, candidate_value: int) -> int:
        if candidate_value != 0 or current_value == 0:
            return candidate_value
        return current_value

    @staticmethod
    def _prefer_last_seen_at(
        current_value: datetime | None,
        candidate_value: datetime | None,
    ) -> datetime | None:
        if current_value is None:
            return candidate_value
        if candidate_value is None:
            return current_value
        if candidate_value >= current_value:
            return candidate_value
        return current_value

    async def _parse_visible_rows(
        self,
        page,
        header_map: dict[str, int],
        page_url: str,
    ) -> list[ScannedAdRow]:
        """Собирает строки из текущего DOM-вью для вспомогательных полей и тестов."""

        scope_context = self._extract_scope_context(page_url)
        await self._set_horizontal_scroll(page, 0)
        horizontal_passes = await self._build_horizontal_passes(page, header_map)
        aggregated_rows: dict[str, dict[str, Any]] = {}
        parsed_rows: list[ScannedAdRow] = []

        for scroll_left in horizontal_passes:
            await self._set_horizontal_scroll(page, scroll_left)
            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if wait_for_timeout is not None:
                await wait_for_timeout(150)
            current_header_map = await self._extract_header_map(page)
            row_payloads = await self._extract_presentation_rows(page)

            for row_payload in row_payloads:
                mapped_values = self._map_presentation_cells(
                    row_payload.get("cells", []),
                    current_header_map,
                )
                fb_ad_id = self._extract_fb_ad_id(row_payload, mapped_values)
                if fb_ad_id is None:
                    continue

                aggregated_row = aggregated_rows.setdefault(
                    fb_ad_id,
                    {
                        "row_payload": row_payload,
                        "mapped_values": {},
                    },
                )
                aggregated_row["row_payload"] = row_payload
                aggregated_row["mapped_values"].update(
                    {key: value for key, value in mapped_values.items() if value.strip()}
                )

        await self._set_horizontal_scroll(page, 0)

        for fb_ad_id, aggregated_row in aggregated_rows.items():
            row_payload = aggregated_row["row_payload"]
            mapped_values = aggregated_row["mapped_values"]
            if not self._has_required_values(mapped_values, _PRESENTATION_REQUIRED_FIELDS):
                continue

            campaign_name, adset_name = self._resolve_scope_names(
                mapped_values=mapped_values,
                scope_context=scope_context,
                fb_ad_id=fb_ad_id,
            )
            ad_name = mapped_values["ad_name"]
            delivery_status_raw = mapped_values["delivery_status"]
            delivery_status = _coerce_delivery_status(delivery_status_raw)
            campaign_scope_key = build_campaign_scope_key(campaign_name)
            parsed_rows.append(
                ScannedAdRow(
                    fb_ad_id=fb_ad_id,
                    campaign_scope_key=campaign_scope_key,
                    adset_scope_key=build_adset_scope_key(
                        adset_name or fb_ad_id,
                        campaign_scope_key,
                    ),
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
                    deposits=_parse_int_value(mapped_values.get("deposits", "")),
                    last_seen_at=datetime.now(tz=UTC),
                    resolved_offer_code=extract_offer_code_from_ad_name(ad_name),
                )
            )

        return parsed_rows

    def _build_response_handler(self, captured_responses: list[Any]):
        """Собирает синхронный handler для response-событий Playwright."""

        def _handler(response: Any) -> None:
            url = getattr(response, "url", "")
            if callable(url):
                with contextlib.suppress(Exception):
                    url = url()
            if not isinstance(url, str) or not self._should_capture_response_url(url):
                return
            captured_responses.append(response)

        return _handler

    async def _wait_for_response_settle(self, page) -> None:
        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if wait_for_timeout is None:
            return

        for _ in range(_RESPONSE_SETTLE_ATTEMPTS):
            await wait_for_timeout(self._settings.scanner_stabilize_delay_ms)

    async def _resolve_response_payloads(
        self,
        captured_responses: list[Any],
    ) -> list[ResponsePayloadEntry]:
        payloads: list[ResponsePayloadEntry] = []
        for response in captured_responses:
            payload = await self._read_response_payload(response)
            if payload is not None:
                response_url = self._resolve_response_url(response)
                payloads.append(
                    ResponsePayloadEntry(
                        url=response_url,
                        is_relevant=self._is_relevant_response_url(response_url),
                        payload=payload,
                    )
                )
        return payloads

    async def _read_response_payload(self, response: Any) -> Any | None:
        response_json = getattr(response, "json", None)
        if callable(response_json):
            with contextlib.suppress(Exception):
                return await response_json()

        response_text = getattr(response, "text", None)
        if callable(response_text):
            with contextlib.suppress(Exception):
                text = await response_text()
                if text:
                    return json.loads(text)
        return None

    async def _read_page_title(self, page) -> str | None:
        title = None
        title_getter = getattr(page, "title", None)
        if callable(title_getter):
            with contextlib.suppress(Exception):
                title = await title_getter()

        if not isinstance(title, str) or not title.strip():
            try:
                title = await page.evaluate("() => document.title")
            except Exception:  # noqa: BLE001
                title = None

        if not isinstance(title, str):
            return None
        return title

    def _extract_expected_rows_count(self, title: str | None) -> int | None:
        if not isinstance(title, str):
            return None

        match = _EXPECTED_ROWS_TITLE_PATTERN.match(title.strip())
        if match is None:
            return None

        with contextlib.suppress(ValueError):
            return int(match.group(1))
        return None

    async def _read_expected_rows_count(
        self,
        page,
        page_title: str | None = None,
    ) -> int | None:
        logger = logging.getLogger(__name__)
        title_count = self._extract_expected_rows_count(page_title)
        footer_count = await self._read_results_rows_count(page)
        if footer_count is not None and title_count is not None and footer_count != title_count:
            logger.warning(
                "Число объявлений по футеру Ads Manager (%s) не совпадает с заголовком вкладки (%s), использую футер",
                footer_count,
                title_count,
            )
        return footer_count or title_count

    async def _read_results_rows_count(self, page) -> int | None:
        try:
            raw_count = await page.evaluate(
                """({ selector }) => {
                    const extractCount = (text) => {
                        if (typeof text !== "string" || !text.trim()) {
                            return null;
                        }
                        const match = text.match(
                            /(?:результаты,\\s*число объявлений|results,\\s*number of ads)\\s*:\\s*(\\d{1,6})/i,
                        );
                        if (!match) {
                            return null;
                        }
                        return Number.parseInt(match[1], 10);
                    };

                    const rows = Array.from(document.querySelectorAll(selector)).reverse();
                    for (const row of rows) {
                        const text = row.innerText || "";
                        const count = extractCount(text);
                        if (count !== null) {
                            return count;
                        }
                    }

                    const bodyText = document.body ? document.body.innerText || "" : "";
                    return extractCount(bodyText);
                }""",
                {"selector": _PRESENTATION_ROW_SELECTOR},
            )
        except Exception:  # noqa: BLE001
            return None

        if isinstance(raw_count, int) and raw_count > 0:
            return raw_count
        return None

    @staticmethod
    def _is_complete_response_rows(
        rows: list[ScannedAdRow],
        expected_rows_count: int | None,
    ) -> bool:
        if expected_rows_count is None:
            return bool(rows)
        return len(rows) >= expected_rows_count

    @staticmethod
    def _has_unresolved_scope_rows(rows: list[ScannedAdRow]) -> bool:
        for row in rows:
            if _is_placeholder_scope_name("campaign_name", row.campaign_name):
                return True
            if _is_placeholder_scope_name("adset_name", row.adset_name):
                return True
            if _is_placeholder_ad_name(row.ad_name):
                return True
        return False

    def _is_relevant_response_url(self, url: str) -> bool:
        lowered = url.casefold()
        return (
            "am_tabular" in lowered
            or "adaccount/ads" in lowered
            or "_reqname=adaccount/ads" in lowered
        )

    def _is_graphql_response_url(self, url: str) -> bool:
        lowered = url.casefold()
        return "facebook.com" in lowered and "/graphql" in lowered

    def _should_capture_response_url(self, url: str) -> bool:
        if self._is_relevant_response_url(url):
            return True
        if self._is_graphql_response_url(url):
            return True
        if not self._settings.scanner_response_probe_enabled:
            return False

        lowered = url.casefold()
        if "facebook.com" not in lowered and "fbcdn.net" not in lowered:
            return False
        if any(
            lowered.endswith(extension)
            for extension in (".js", ".css", ".png", ".jpg", ".jpeg", ".svg", ".woff", ".woff2")
        ):
            return False
        return True

    @staticmethod
    def _resolve_response_url(response: Any) -> str:
        raw_url = getattr(response, "url", "")
        if callable(raw_url):
            with contextlib.suppress(Exception):
                raw_url = raw_url()
        return raw_url if isinstance(raw_url, str) and raw_url else "unknown"

    def _parse_response_rows(self, payloads: list[Any], page_url: str) -> list[ScannedAdRow]:
        scope_context = self._extract_scope_context(page_url)
        rows_by_id: dict[str, _CapturedResponseRow] = {}
        adset_names_by_id, campaign_names_by_id, campaign_id_by_adset_id = (
            self._collect_graphql_scope_references(payloads)
        )

        for payload in payloads:
            for candidate in self._iter_response_row_candidates(payload):
                candidate_typename = self._coerce_response_text(candidate.get("__typename"))
                if candidate_typename in {"AdCampaign", "AdCampaignGroup"}:
                    continue
                mapped_values = self._normalize_response_row(candidate)
                fb_ad_id = self._extract_response_fb_ad_id(mapped_values)
                if fb_ad_id is None:
                    continue

                row = rows_by_id.setdefault(fb_ad_id, _CapturedResponseRow(fb_ad_id=fb_ad_id))
                row.merge(mapped_values)
                row.adset_id = row.adset_id or _extract_mapped_identifier(
                    mapped_values,
                    "adset_id",
                    "ad set id",
                    "ad_campaign_id",
                    "ad campaign id",
                )
                row.campaign_id = row.campaign_id or _extract_mapped_identifier(
                    mapped_values,
                    "campaign_id",
                    "campaign group id",
                    "campaign_group_id",
                    "ad_campaign_group_id",
                    "ad campaign group id",
                )
                self._apply_scope_references(
                    row,
                    adset_names_by_id=adset_names_by_id,
                    campaign_names_by_id=campaign_names_by_id,
                    campaign_id_by_adset_id=campaign_id_by_adset_id,
                )

        for row in rows_by_id.values():
            self._apply_scope_references(
                row,
                adset_names_by_id=adset_names_by_id,
                campaign_names_by_id=campaign_names_by_id,
                campaign_id_by_adset_id=campaign_id_by_adset_id,
            )

        return [
            self._build_row_from_response_data(row, scope_context) for row in rows_by_id.values()
        ]

    def _parse_graphql_rows(self, payloads: list[Any], page_url: str) -> list[ScannedAdRow]:
        scope_context = self._extract_scope_context(page_url)
        rows_by_id: dict[str, _CapturedResponseRow] = {}
        adset_names_by_id, campaign_names_by_id, campaign_id_by_adset_id = (
            self._collect_graphql_scope_references(payloads)
        )

        for payload in payloads:
            for node in self._iter_graphql_nodes(payload):
                typename = self._coerce_response_text(node.get("__typename"))
                node_id = self._extract_graphql_identifier(node.get("id") or node.get("node_id"))

                if typename != "Adgroup" or node_id is None:
                    continue

                row = rows_by_id.setdefault(node_id, _CapturedResponseRow(fb_ad_id=node_id))
                row.merge(self._normalize_response_row(node))
                row.adset_id = row.adset_id or self._extract_graphql_identifier(
                    node.get("ad_campaign_id")
                )
                row.campaign_id = row.campaign_id or self._extract_graphql_identifier(
                    node.get("ad_campaign_group_id")
                )
                direct_campaign_name = self._coerce_response_text(
                    node.get("ad_campaign_group_name")
                )
                if _is_scope_name_usable("campaign_name", direct_campaign_name):
                    row.campaign_name = row.campaign_name or direct_campaign_name

        for row in rows_by_id.values():
            self._apply_scope_references(
                row,
                adset_names_by_id=adset_names_by_id,
                campaign_names_by_id=campaign_names_by_id,
                campaign_id_by_adset_id=campaign_id_by_adset_id,
            )

        return [
            self._build_row_from_response_data(row, scope_context) for row in rows_by_id.values()
        ]

    def _collect_graphql_scope_references(
        self,
        payloads: list[Any],
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        adset_names_by_id: dict[str, str] = {}
        campaign_names_by_id: dict[str, str] = {}
        campaign_id_by_adset_id: dict[str, str] = {}

        for payload in payloads:
            for node in self._iter_graphql_nodes(payload):
                typename = self._coerce_response_text(node.get("__typename"))
                node_id = self._extract_graphql_identifier(node.get("id") or node.get("node_id"))
                if typename == "AdCampaign":
                    if node_id is None:
                        continue
                    adset_name = self._coerce_response_text(node.get("name"))
                    if _is_scope_name_usable("adset_name", adset_name):
                        adset_names_by_id[node_id] = adset_name
                    campaign_id = self._extract_graphql_identifier(node.get("ad_campaign_group_id"))
                    if campaign_id is not None:
                        campaign_id_by_adset_id[node_id] = campaign_id
                    campaign_name = self._coerce_response_text(node.get("ad_campaign_group_name"))
                    if campaign_id is not None and _is_scope_name_usable(
                        "campaign_name",
                        campaign_name,
                    ):
                        campaign_names_by_id[campaign_id] = campaign_name
                    continue

                if typename != "AdCampaignGroup" or node_id is None:
                    continue

                campaign_name = self._coerce_response_text(node.get("name"))
                if _is_scope_name_usable("campaign_name", campaign_name):
                    campaign_names_by_id[node_id] = campaign_name

        return adset_names_by_id, campaign_names_by_id, campaign_id_by_adset_id

    def _apply_scope_references(
        self,
        row: _CapturedResponseRow,
        *,
        adset_names_by_id: dict[str, str],
        campaign_names_by_id: dict[str, str],
        campaign_id_by_adset_id: dict[str, str],
    ) -> None:
        if (
            not _is_scope_name_usable("adset_name", row.adset_name)
            and row.adset_id is not None
            and row.adset_id in adset_names_by_id
        ):
            row.adset_name = adset_names_by_id[row.adset_id]

        campaign_id = row.campaign_id or (
            campaign_id_by_adset_id.get(row.adset_id) if row.adset_id is not None else None
        )
        if campaign_id is not None:
            row.campaign_id = row.campaign_id or campaign_id

        if (
            not _is_scope_name_usable("campaign_name", row.campaign_name)
            and campaign_id is not None
            and campaign_id in campaign_names_by_id
        ):
            row.campaign_name = campaign_names_by_id[campaign_id]

    def _iter_graphql_nodes(self, payload: Any) -> Iterable[dict[str, Any]]:
        if isinstance(payload, dict):
            typename = payload.get("__typename")
            if isinstance(typename, str) and typename in _GRAPHQL_NODE_TYPES:
                yield payload
            for value in payload.values():
                yield from self._iter_graphql_nodes(value)
            return

        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_graphql_nodes(item)

    @staticmethod
    def _extract_graphql_identifier(value: Any) -> str | None:
        if isinstance(value, (str, int)):
            return _extract_numeric_identifier(str(value))
        return None

    def _build_row_from_response_data(
        self,
        row: _CapturedResponseRow,
        scope_context: dict[str, str | None],
    ) -> ScannedAdRow:
        campaign_name = (
            (
                row.campaign_name
                if _is_scope_name_usable("campaign_name", row.campaign_name)
                else None
            )
            or (f"Кампания {row.campaign_id}" if row.campaign_id is not None else None)
            or (
                scope_context.get("campaign_name")
                if _is_scope_name_usable("campaign_name", scope_context.get("campaign_name"))
                else None
            )
            or f"Кампания объявления {row.fb_ad_id}"
        )
        adset_name = (
            (row.adset_name if _is_scope_name_usable("adset_name", row.adset_name) else None)
            or (f"Адсет {row.adset_id}" if row.adset_id is not None else None)
            or (
                scope_context.get("adset_name")
                if _is_scope_name_usable("adset_name", scope_context.get("adset_name"))
                else None
            )
            or ""
        )
        ad_name = row.ad_name or f"Объявление {row.fb_ad_id}"
        delivery_status = _coerce_delivery_status(row.delivery_status or "unknown")
        campaign_scope_key = build_campaign_scope_key(campaign_name)
        return ScannedAdRow(
            fb_ad_id=row.fb_ad_id,
            campaign_scope_key=campaign_scope_key,
            adset_scope_key=build_adset_scope_key(
                adset_name or row.fb_ad_id,
                campaign_scope_key,
            ),
            campaign_name=campaign_name,
            adset_name=adset_name,
            ad_name=ad_name,
            delivery_status=delivery_status,
            tracking_mode=TrackingMode.TRACKED,
            scope_presence=ScopePresence.IN_SCOPE,
            spend=_parse_decimal_value(row.spend or "") or Decimal("0"),
            clicks=_parse_int_value(row.clicks or ""),
            cpc=_parse_decimal_value(row.cpc or ""),
            leads=_parse_int_value(row.leads or ""),
            cost_per_lead=_parse_decimal_value(row.cost_per_lead or ""),
            registrations=_parse_int_value(row.registrations or ""),
            cost_per_registration=_parse_decimal_value(row.cost_per_registration or ""),
            deposits=_parse_int_value(row.deposits or ""),
            last_seen_at=datetime.now(tz=UTC),
            resolved_offer_code=extract_offer_code_from_ad_name(ad_name),
        )

    def _iter_response_row_candidates(self, payload: Any) -> Iterable[dict[str, Any]]:
        if isinstance(payload, dict):
            if isinstance(payload.get("rows"), list):
                for row in payload["rows"]:
                    if isinstance(row, dict):
                        yield row
                    else:
                        continue
                return

            if isinstance(payload.get("nodes"), list):
                for item in payload["nodes"]:
                    yield from self._iter_response_row_candidates(item)
                return

            data = payload.get("data")
            if isinstance(data, dict):
                yield from self._iter_response_row_candidates(data)
                return
            if isinstance(data, list):
                for item in data:
                    yield from self._iter_response_row_candidates(item)
                return

            if (
                any(
                    key in payload
                    for key in (
                        "dimension_values",
                        "atomic_values",
                        "ad_id",
                        "id",
                        "name",
                        "spend",
                        "clicks",
                        "cpc",
                    )
                )
                or payload.get("__typename") in _GRAPHQL_NODE_TYPES
            ):
                yield payload
                return

        if isinstance(payload, list):
            for item in payload:
                yield from self._iter_response_row_candidates(item)

    def _normalize_response_row(self, row: dict[str, Any]) -> dict[str, str]:
        mapped: dict[str, str] = {}
        typename = self._coerce_response_text(row.get("__typename"))
        headers = row.get("headers")
        if isinstance(headers, list):
            headers = [self._extract_response_header_name(item) for item in headers]
        else:
            headers = None

        direct_keys = {
            "ad_id",
            "fb_ad_id",
            "id",
            "campaign_name",
            "adset_name",
            "ad_name",
            "delivery_status",
            "delivery",
            "status",
            "spend",
            "clicks",
            "cpc",
            "leads",
            "cost_per_lead",
            "registrations",
            "cost_per_registration",
            "deposits",
        }

        for key, value in row.items():
            if key in {"headers", "rows"}:
                continue
            normalized_key = _normalize_header_text(str(key))
            if key in {"dimension_values", "atomic_values", "values"}:
                mapped.update(self._flatten_response_container(value, headers=headers))
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                mapped[normalized_key] = self._coerce_response_text(value)
                continue
            mapped.update(self._flatten_response_container(value, headers=headers))

        for key, value in row.items():
            if key in direct_keys:
                normalized_key = _normalize_header_text(str(key))
                if isinstance(value, (str, int, float, bool)) or value is None:
                    mapped[normalized_key] = self._coerce_response_text(value)

        if typename == "Adgroup":
            ad_name = row.get("name")
            if isinstance(ad_name, (str, int, float, bool)) or ad_name is None:
                mapped[_normalize_header_text("ad name")] = self._coerce_response_text(ad_name)
            adset_name = row.get("ad_campaign_name")
            if isinstance(adset_name, (str, int, float, bool)) or adset_name is None:
                mapped[_normalize_header_text("adset name")] = self._coerce_response_text(
                    adset_name
                )
            delivery_status = self._extract_graphql_delivery_status_text(row.get("delivery_status"))
            if delivery_status:
                mapped[_normalize_header_text("delivery status")] = delivery_status

        if typename == "AdCampaign":
            adset_name = row.get("name")
            if isinstance(adset_name, (str, int, float, bool)) or adset_name is None:
                mapped[_normalize_header_text("adset name")] = self._coerce_response_text(
                    adset_name
                )

        if typename == "AdCampaignGroup":
            campaign_name = row.get("name")
            if isinstance(campaign_name, (str, int, float, bool)) or campaign_name is None:
                mapped[_normalize_header_text("campaign name")] = self._coerce_response_text(
                    campaign_name
                )

        return mapped

    def _extract_graphql_delivery_status_text(self, value: Any) -> str | None:
        if isinstance(value, dict):
            substatuses = value.get("substatuses")
            if isinstance(substatuses, list):
                for item in substatuses:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
            status = value.get("status")
            if isinstance(status, str) and status.strip():
                return status.strip()
        return None

    def _flatten_response_container(
        self,
        value: Any,
        *,
        headers: list[str] | None = None,
    ) -> dict[str, str]:
        flattened: dict[str, str] = {}

        if isinstance(value, dict):
            pair_key = self._extract_pair_key(value)
            if (
                pair_key is not None
                and "value" in value
                and not isinstance(value["value"], (dict, list))
            ):
                flattened[_normalize_header_text(pair_key)] = self._coerce_response_text(
                    value["value"]
                )
                return flattened

            for key, item in value.items():
                if isinstance(item, (str, int, float, bool)) or item is None:
                    flattened[_normalize_header_text(str(key))] = self._coerce_response_text(item)
                else:
                    flattened.update(self._flatten_response_container(item, headers=headers))
            return flattened

        if isinstance(value, list):
            if headers is not None and len(headers) == len(value):
                for header, item in zip(headers, value, strict=False):
                    if isinstance(item, (str, int, float, bool)) or item is None:
                        flattened[_normalize_header_text(header)] = self._coerce_response_text(item)
                    else:
                        flattened.update(self._flatten_response_container(item, headers=headers))
                return flattened

            for item in value:
                if isinstance(item, dict):
                    flattened.update(self._flatten_response_container(item, headers=headers))
                elif isinstance(item, (str, int, float, bool)) or item is None:
                    flattened[str(len(flattened))] = self._coerce_response_text(item)
            return flattened

        return flattened

    @staticmethod
    def _extract_pair_key(value: dict[str, Any]) -> str | None:
        for key_name in ("name", "field", "column", "header", "metric", "dimension", "key"):
            key_value = value.get(key_name)
            if isinstance(key_value, str) and key_value.strip():
                return key_value
        return None

    @staticmethod
    def _coerce_response_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value).strip()

    def _extract_response_fb_ad_id(self, mapped_values: dict[str, str]) -> str | None:
        candidates = (
            mapped_values.get(_normalize_header_text("fb_ad_id")),
            mapped_values.get(_normalize_header_text("ad_id")),
            mapped_values.get(_normalize_header_text("id")),
        )
        for candidate in candidates:
            direct_identifier = _extract_numeric_identifier(candidate)
            if direct_identifier is not None:
                return direct_identifier
        return None

    def _map_presentation_cells(
        self,
        row_cells: list[dict[str, Any]],
        header_map: dict[str, int],
    ) -> dict[str, str]:
        mapped: dict[str, str] = {}
        best_distances: dict[str, int] = {}

        for row_cell in sorted(row_cells, key=lambda item: int(item.get("x", 0))):
            cell_text = str(row_cell.get("text", "")).strip()
            if not cell_text:
                continue
            field_name, distance = self._resolve_field_for_cell(
                int(row_cell.get("x", 0)), header_map
            )
            if field_name is None or distance > _MAX_HEADER_DISTANCE_PX:
                continue
            previous_distance = best_distances.get(field_name)
            if previous_distance is not None and previous_distance <= distance:
                continue
            mapped[field_name] = _sanitize_cell_value(field_name, cell_text)
            best_distances[field_name] = distance

        return mapped

    def _resolve_field_for_cell(
        self,
        cell_x: int,
        header_map: dict[str, int],
    ) -> tuple[str | None, int]:
        nearest_field_name: str | None = None
        nearest_distance: int | None = None

        for field_name, header_x in header_map.items():
            distance = abs(cell_x - header_x)
            if nearest_distance is None or distance < nearest_distance:
                nearest_field_name = field_name
                nearest_distance = distance

        return nearest_field_name, nearest_distance if nearest_distance is not None else 0

    def _extract_fb_ad_id(
        self,
        row_payload: dict[str, Any],
        mapped_values: dict[str, str],
    ) -> str | None:
        direct_identifier = _extract_numeric_identifier(mapped_values.get("fb_ad_id"))
        if direct_identifier is not None:
            return direct_identifier

        for surface in row_payload.get("surfaces", []):
            surface_match = _SURFACE_ROW_ID_PATTERN.search(str(surface))
            if surface_match is not None:
                return surface_match.group(1)

        return _extract_numeric_identifier(str(row_payload.get("text", "")))

    def _resolve_scope_names(
        self,
        *,
        mapped_values: dict[str, str],
        scope_context: dict[str, str | None],
        fb_ad_id: str,
    ) -> tuple[str, str]:
        campaign_name = (
            (
                mapped_values.get("campaign_name")
                if _is_scope_name_usable("campaign_name", mapped_values.get("campaign_name"))
                else None
            )
            or (
                scope_context.get("campaign_name")
                if _is_scope_name_usable("campaign_name", scope_context.get("campaign_name"))
                else None
            )
            or f"Кампания объявления {fb_ad_id}"
        )
        adset_name = (
            (
                mapped_values.get("adset_name")
                if _is_scope_name_usable("adset_name", mapped_values.get("adset_name"))
                else None
            )
            or (
                scope_context.get("adset_name")
                if _is_scope_name_usable("adset_name", scope_context.get("adset_name"))
                else None
            )
            or ""
        )
        return campaign_name, adset_name

    @staticmethod
    def _extract_scope_context(page_url: str) -> dict[str, str | None]:
        campaign_id = FacebookAdsScannerProvider._extract_query_identifier(
            page_url,
            "selected_campaign_ids",
        )
        adset_id = FacebookAdsScannerProvider._extract_query_identifier(
            page_url,
            "selected_adset_ids",
            "selected_ad_set_ids",
        )
        return {
            "campaign_name": f"Кампания {campaign_id}" if campaign_id is not None else None,
            "adset_name": f"Адсет {adset_id}" if adset_id is not None else None,
        }

    @staticmethod
    def _extract_query_identifier(page_url: str, *keys: str) -> str | None:
        query = parse_qs(urlparse(page_url).query)
        identifiers: list[str] = []
        for key in keys:
            values = query.get(key)
            if not values:
                continue
            for value in values:
                identifiers.extend(_FB_AD_ID_PATTERN.findall(value))
        unique_identifiers = tuple(dict.fromkeys(identifiers))
        if len(unique_identifiers) == 1:
            return unique_identifiers[0]
        return None

    def _log_scope_resolution(self, header_map: dict[str, int], page_url: str) -> None:
        logger = logging.getLogger(__name__)
        scope_context = self._extract_scope_context(page_url)

        if "campaign_name" not in header_map and scope_context.get("campaign_name") is not None:
            logger.info(
                "Имя кампании в сканере будет взято из selected_campaign_ids: %s",
                scope_context["campaign_name"],
            )
        if "campaign_name" not in header_map and scope_context.get("campaign_name") is None:
            logger.warning(
                "В текущем представлении Ads Manager нет колонки кампании и выбранного campaign id; для строк будет использован изолированный scope по id объявления"
            )
        if "adset_name" not in header_map and scope_context.get("adset_name") is not None:
            logger.info(
                "Имя адсета в сканере будет взято из selected_adset_ids: %s",
                scope_context["adset_name"],
            )
        if "adset_name" not in header_map and scope_context.get("adset_name") is None:
            logger.warning(
                "В текущем представлении Ads Manager нет колонки адсета и выбранного adset id; для строк будет использован изолированный scope по id объявления"
            )

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
        headers = await page.locator("[role='columnheader']").evaluate_all(
            """(elements) => elements.map((element) => {
                const rect = element.getBoundingClientRect();
                return {
                    text: (element.innerText || '').trim(),
                    x: Math.round(rect.x),
                    width: Math.round(rect.width),
                };
            })"""
        )
        resolved_map: dict[str, int] = {}

        for header in headers:
            header_text = _normalize_header_text(str(header.get("text", "")))
            header_x = int(header.get("x", 0))
            header_width = int(header.get("width", 0))
            if not header_text or header_width <= 0:
                continue

            for field_name in _HEADER_ALIASES:
                if field_name in resolved_map:
                    continue
                if _matches_header_alias(field_name, header_text):
                    resolved_map[field_name] = header_x
                    break

        return resolved_map

    @staticmethod
    def _has_required_columns(
        header_map: dict[str, int],
        required_fields: tuple[str, ...],
    ) -> bool:
        return all(field_name in header_map for field_name in required_fields)

    @staticmethod
    def _has_required_values(
        mapped_values: dict[str, str],
        required_fields: tuple[str, ...],
    ) -> bool:
        return all(mapped_values.get(field_name, "").strip() for field_name in required_fields)

    async def _count_visible_rows(self, page) -> int:
        return await page.locator(_PRESENTATION_ROW_SELECTOR).count()

    async def _build_horizontal_passes(
        self,
        page,
        header_map: dict[str, int],
    ) -> tuple[int, ...]:
        scroll_state = await self._get_horizontal_scroll_state(page)
        if scroll_state is None:
            return (0,)

        header_positions = [
            header_map[field_name]
            for field_name in (
                "ad_name",
                "delivery_status",
                "spend",
                "clicks",
                "cpc",
                "leads",
                "cost_per_lead",
                "registrations",
                "cost_per_registration",
                "campaign_name",
                "adset_name",
            )
            if field_name in header_map
        ]
        return _build_horizontal_pass_starts(
            header_positions=header_positions,
            client_width=int(scroll_state["client_width"]),
            max_scroll_left=int(scroll_state["max_scroll_left"]),
        )

    async def _can_parse_current_view(self, page, header_map: dict[str, int]) -> bool:
        if not self._has_required_columns(header_map, _PRESENTATION_REQUIRED_FIELDS):
            return False

        row_payloads = await self._extract_presentation_rows(page)
        for row_payload in row_payloads:
            mapped_values = self._map_presentation_cells(
                row_payload.get("cells", []),
                header_map,
            )
            if not self._has_required_values(mapped_values, _PRESENTATION_REQUIRED_FIELDS):
                continue
            if self._extract_fb_ad_id(row_payload, mapped_values) is not None:
                return True

        return False

    async def _extract_presentation_rows(self, page) -> list[dict[str, Any]]:
        return await page.locator(_PRESENTATION_ROW_SELECTOR).evaluate_all(
            f"""(elements) => elements.map((row) => {{
                const rowRect = row.getBoundingClientRect();
                const surfaces = Array.from(
                    new Set(
                        Array.from(row.querySelectorAll('[data-surface]'))
                            .map((node) => node.getAttribute('data-surface') || '')
                            .filter(Boolean)
                    )
                );
                const cells = Array.from(row.querySelectorAll('{_PRESENTATION_CELL_SELECTOR}'))
                    .map((cell) => {{
                        const cellRect = cell.getBoundingClientRect();
                        return {{
                            text: (cell.innerText || '').trim(),
                            x: Math.round(cellRect.x),
                            y: Math.round(cellRect.y),
                            width: Math.round(cellRect.width),
                            height: Math.round(cellRect.height),
                        }};
                    }})
                    .filter((cell) => cell.text && cell.width >= 30 && cell.height >= 20);
                return {{
                    text: (row.innerText || '').trim(),
                    x: Math.round(rowRect.x),
                    y: Math.round(rowRect.y),
                    width: Math.round(rowRect.width),
                    height: Math.round(rowRect.height),
                    surfaces,
                    cells,
                }};
            }})"""
        )

    async def _get_horizontal_scroll_state(self, page) -> dict[str, int] | None:
        try:
            return await page.evaluate(
                """() => {
                    const anchors = [
                        ...Array.from(document.querySelectorAll('[role="columnheader"]')),
                        ...Array.from(document.querySelectorAll('div[role="presentation"]._1gd4')).slice(0, 5),
                    ];
                    if (anchors.length === 0) {
                        return null;
                    }
                    let best = null;
                    let bestDelta = 0;
                    const visited = new Set();
                    for (const anchor of anchors) {
                        let node = anchor.parentElement;
                        while (node) {
                            if (!visited.has(node)) {
                                visited.add(node);
                                const delta = node.scrollWidth - node.clientWidth;
                                if (node.clientWidth > 0 && delta > bestDelta + 20) {
                                    best = node;
                                    bestDelta = delta;
                                }
                            }
                            node = node.parentElement;
                        }
                    }
                    if (!best) {
                        return null;
                    }
                    return {
                        client_width: Math.round(best.clientWidth),
                        max_scroll_left: Math.max(Math.round(best.scrollWidth - best.clientWidth), 0),
                        scroll_left: Math.round(best.scrollLeft),
                    };
                }"""
            )
        except Exception:  # noqa: BLE001
            return None

    async def _set_horizontal_scroll(self, page, scroll_left: int) -> None:
        try:
            await page.evaluate(
                """(targetScrollLeft) => {
                    const anchors = [
                        ...Array.from(document.querySelectorAll('[role="columnheader"]')),
                        ...Array.from(document.querySelectorAll('div[role="presentation"]._1gd4')).slice(0, 5),
                    ];
                    if (anchors.length === 0) {
                        return;
                    }
                    let best = null;
                    let bestDelta = 0;
                    const visited = new Set();
                    for (const anchor of anchors) {
                        let node = anchor.parentElement;
                        while (node) {
                            if (!visited.has(node)) {
                                visited.add(node);
                                const delta = node.scrollWidth - node.clientWidth;
                                if (node.clientWidth > 0 && delta > bestDelta + 20) {
                                    best = node;
                                    bestDelta = delta;
                                }
                            }
                            node = node.parentElement;
                        }
                    }
                    if (best) {
                        best.scrollLeft = targetScrollLeft;
                    }
                }""",
                scroll_left,
            )
        except Exception:  # noqa: BLE001
            return None

    async def _scroll_once(self, page) -> bool:
        locator_getter = getattr(page, "locator", None)
        if not callable(locator_getter):
            return False
        try:
            row_locator = locator_getter(_PRESENTATION_ROW_SELECTOR)
            row_count = await row_locator.count()
        except Exception:  # noqa: BLE001
            return False
        if row_count <= 0:
            return False

        before_state = await self._read_table_scroll_state(page)
        if before_state is None:
            return False

        scroll_px = getattr(self._settings, "scanner_scroll_step_px", 2500)
        dom_scroll_applied = await self._scroll_table_container(page, delta_px=scroll_px)
        if dom_scroll_applied:
            wait_for_timeout = getattr(page, "wait_for_timeout", None)
            if callable(wait_for_timeout):
                await wait_for_timeout(min(self._settings.scanner_scroll_pause_ms, 150))
            after_state = await self._read_table_scroll_state(page)
            if after_state is None:
                return False
            return (
                after_state["container_scroll_top"] != before_state["container_scroll_top"]
                or after_state["signature"] != before_state["signature"]
            )

        mouse = getattr(page, "mouse", None)
        if mouse is None:
            return False

        move = getattr(mouse, "move", None)
        wheel = getattr(mouse, "wheel", None)
        if not callable(move) or not callable(wheel):
            return False

        try:
            await move(before_state["anchor_x"], before_state["anchor_y"])
            await wheel(0, scroll_px)
        except Exception:  # noqa: BLE001
            return False

        wait_for_timeout = getattr(page, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            await wait_for_timeout(min(self._settings.scanner_scroll_pause_ms, 150))

        after_state = await self._read_table_scroll_state(page)
        if after_state is None:
            return False

        if after_state["page_scroll_top"] != before_state["page_scroll_top"]:
            return False
        return after_state["signature"] != before_state["signature"]

    async def _scroll_vertical_area_to_edge(self, page, *, edge: str) -> None:
        if await self._scroll_table_container(page, edge=edge):
            return
        try:
            await page.evaluate(
                """(targetEdge) => {
                    const containers = [
                        ...document.querySelectorAll('.uiScrollableAreaWrap, .uiScrollableAreaBody, div[role="grid"], div[role="table"]'),
                        document.scrollingElement,
                        document.body,
                        window,
                    ];
                    for (const container of containers) {
                        const targetTop = targetEdge === 'top' ? 0 : (container.scrollHeight || 999999);
                        if (container && container.scrollTo) {
                            container.scrollTo({ top: targetTop, behavior: 'auto' });
                        } else if (container && typeof container.scrollTop !== 'undefined') {
                            container.scrollTop = targetTop;
                        }
                    }
                }""",
                edge,
            )
        except Exception:  # noqa: BLE001
            return

    async def _scroll_table_container(
        self,
        page,
        *,
        delta_px: int | None = None,
        edge: str | None = None,
    ) -> bool:
        try:
            return bool(
                await page.evaluate(
                    """({ selector, deltaPx, targetEdge }) => {
                        const rows = Array.from(document.querySelectorAll(selector));
                        if (rows.length === 0) {
                            return false;
                        }

                        const findScrollableContainer = (row) => {
                            const doc = document.scrollingElement || document.documentElement || document.body;
                            let fallback = null;
                            let node = row.parentElement;
                            while (node) {
                                const style = window.getComputedStyle(node);
                                const maxScrollTop = Math.max((node.scrollHeight || 0) - (node.clientHeight || 0), 0);
                                const overflowY = `${style?.overflowY || ""} ${style?.overflow || ""}`;
                                if (maxScrollTop > 20 && fallback === null) {
                                    fallback = node;
                                }
                                if (maxScrollTop > 20 && /(auto|scroll|overlay)/i.test(overflowY)) {
                                    return node;
                                }
                                node = node.parentElement;
                            }
                            return fallback || doc;
                        };

                        const container = findScrollableContainer(rows[0]);
                        const currentTop = Math.max(
                            Math.round(typeof container.scrollTop === "number" ? container.scrollTop || 0 : 0),
                            0,
                        );
                        const maxScrollTop = Math.max(
                            Math.round((container.scrollHeight || 0) - (container.clientHeight || 0)),
                            0,
                        );
                        let targetTop = currentTop;
                        if (targetEdge === "top") {
                            targetTop = 0;
                        } else if (targetEdge === "bottom") {
                            targetTop = maxScrollTop;
                        } else if (typeof deltaPx === "number") {
                            const normalizedDeltaPx = Math.max(Math.round(deltaPx), 1);
                            const visibleHeight = Math.max(Math.round(container.clientHeight || 0), 0);
                            const safeStepPx = visibleHeight > 0
                                ? Math.max(Math.round(visibleHeight * 0.75), 240)
                                : normalizedDeltaPx;
                            const actualStepPx = Math.min(normalizedDeltaPx, safeStepPx);
                            targetTop = Math.min(maxScrollTop, Math.max(0, currentTop + actualStepPx));
                        }

                        if (targetTop === currentTop) {
                            return false;
                        }
                        if (typeof container.scrollTo === "function") {
                            container.scrollTo({ top: targetTop, behavior: "auto" });
                        } else {
                            container.scrollTop = targetTop;
                        }
                        return true;
                    }""",
                    {
                        "selector": _PRESENTATION_ROW_SELECTOR,
                        "deltaPx": delta_px,
                        "targetEdge": edge,
                    },
                )
            )
        except Exception:  # noqa: BLE001
            return False

    async def _read_table_scroll_state(self, page) -> dict[str, Any] | None:
        try:
            return await page.evaluate(
                """({ selector }) => {
                    const rows = Array.from(document.querySelectorAll(selector));
                    if (rows.length === 0) {
                        return null;
                    }

                    const findScrollableContainer = (row) => {
                        const doc = document.scrollingElement || document.documentElement || document.body;
                        let fallback = null;
                        let node = row.parentElement;
                        while (node) {
                            const style = window.getComputedStyle(node);
                            const maxScrollTop = Math.max((node.scrollHeight || 0) - (node.clientHeight || 0), 0);
                            const overflowY = `${style?.overflowY || ""} ${style?.overflow || ""}`;
                            if (maxScrollTop > 20 && fallback === null) {
                                fallback = node;
                            }
                            if (maxScrollTop > 20 && /(auto|scroll|overlay)/i.test(overflowY)) {
                                return node;
                            }
                            node = node.parentElement;
                        }
                        return fallback || doc;
                    };

                    const buildSignature = (row) => {
                        if (!row) {
                            return null;
                        }
                        const rect = row.getBoundingClientRect();
                        return {
                            surface: row.getAttribute("data-surface") || "",
                            text: (row.innerText || "").trim(),
                            top: Math.round(rect.top),
                        };
                    };

                    const firstRow = rows[0];
                    const rect = firstRow.getBoundingClientRect();
                    const container = findScrollableContainer(firstRow);
                    return {
                        page_scroll_top: Math.round(
                            document.scrollingElement ? document.scrollingElement.scrollTop || 0 : 0,
                        ),
                        container_scroll_top: Math.round(
                            typeof container.scrollTop === "number" ? container.scrollTop || 0 : 0,
                        ),
                        anchor_x: Math.round(rect.left + Math.min(200, Math.max(rect.width / 2, 80))),
                        anchor_y: Math.round(rect.top + Math.min(80, Math.max(rect.height / 2, 20))),
                        signature: JSON.stringify({
                            first: buildSignature(firstRow),
                            last: buildSignature(rows[rows.length - 1]),
                        }),
                    };
                }""",
                {"selector": _PRESENTATION_ROW_SELECTOR},
            )
        except Exception:  # noqa: BLE001
            return None

    async def _resolve_ads_page(self, browser):
        candidates = []
        for context in browser.contexts:
            for page in context.pages:
                score = 0
                url = page.url or ""
                lowered_url = url.casefold()
                if is_ads_manager_service_url(url):
                    continue
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
            if not browser.contexts:
                raise RuntimeError("В подключенном браузере нет открытых страниц для сканирования")
            seed_url = ""
            target_context = browser.contexts[0] if browser.contexts else None
        else:
            best_score, best_page = max(candidates, key=lambda item: item[0])
            if best_score <= 0:
                raise RuntimeError(
                    "Не удалось найти открытую страницу Facebook Ads Manager для текущего профиля"
                )
            await best_page.wait_for_load_state("domcontentloaded")
            seed_url = getattr(best_page, "url", "") or ""
            target_context = getattr(best_page, "context", None)

        return await ensure_ads_manager_service_page(
            browser=browser,
            context=target_context,
            service_role=SCANNER_SERVICE_PAGE,
            seed_url=seed_url,
        )

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
