from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from apps.browser_host.facebook_scanner import (
    FacebookAdsScannerProvider,
    _build_horizontal_pass_starts,
    _coerce_delivery_status,
    _matches_header_alias,
    _normalize_header_text,
    _parse_decimal_value,
    _parse_int_value,
)
from apps.browser_host.playwright_attach import AttachedBrowserSession
from core.config import Settings
from core.domain import DeliveryStatus, ScopePresence, TrackingMode
from core.scanner import (
    ScannedAdRow,
    ScannerScopeUnavailableError,
    build_adset_scope_key,
    build_campaign_scope_key,
)


# Проверяет, что scanner корректно нормализует приоритетный статус отклоненного объявления.
def test_coerce_delivery_status_maps_not_delivering() -> None:
    status = _coerce_delivery_status("Не показывается")

    assert status == DeliveryStatus.NOT_DELIVERING


# Проверяет, что scanner разбирает денежные значения из разных форматов интерфейса Facebook.
def test_parse_decimal_value_supports_common_ui_formats() -> None:
    assert _parse_decimal_value("$1,234.56") == Decimal("1234.56")
    assert _parse_decimal_value("1 234,56 $") == Decimal("1234.56")
    assert _parse_decimal_value("—") is None


# Проверяет, что scanner извлекает целочисленные метрики даже если Facebook отдает разделители тысяч.
def test_parse_int_value_supports_group_separators() -> None:
    assert _parse_int_value("1 234") == 1234
    assert _parse_int_value("2,345") == 2345


# Проверяет, что scanner умеет вытащить fb_ad_id из ссылок, где id приходит в query string.
def test_extract_fb_ad_id_from_href_reads_query_parameters() -> None:
    fb_ad_id = FacebookAdsScannerProvider._extract_fb_ad_id_from_href(
        "https://www.facebook.com/adsmanager/manage/ads?selected_ad_ids=1234567890123"
    )

    assert fb_ad_id == "1234567890123"


# Проверяет, что scanner не принимает колонку бюджета за колонку адсета из-за второго слова в заголовке.
def test_matches_header_alias_ignores_budget_header_for_adset_name() -> None:
    normalized_header = _normalize_header_text("Бюджет\nГруппа объявлений")

    assert _matches_header_alias("adset_name", normalized_header) is False


# Проверяет, что scanner распознает новые русские колонки кампании и группы объявлений без падения на бюджетную колонку.
def test_matches_header_alias_supports_explicit_scope_name_columns() -> None:
    assert (
        _matches_header_alias(
            "campaign_name",
            _normalize_header_text("Название кампании"),
        )
        is True
    )
    assert (
        _matches_header_alias(
            "adset_name",
            _normalize_header_text("Название группы объявлений"),
        )
        is True
    )


# Проверяет, что scanner планирует несколько горизонтальных проходов, когда таблица шире viewport.
def test_build_horizontal_pass_starts_splits_wide_table_into_passes() -> None:
    passes = _build_horizontal_pass_starts(
        header_positions=[207, 666, 1590, 1835, 1981, 2203, 2349, 2571, 2717, 3085, 3544],
        client_width=2000,
        max_scroll_left=2003,
    )

    assert passes == (0, 1635, 2003)


# Проверяет, что scanner захватывает graphql-response даже без временного probe-режима.
def test_should_capture_response_url_accepts_graphql_without_probe() -> None:
    provider = FacebookAdsScannerProvider(settings=Settings())

    assert (
        provider._should_capture_response_url(
            "https://adsmanager.facebook.com/api/graphql/?__crash_obid=1"
        )
        is True
    )


# Проверяет, что scanner оставляет имя адсета пустым без фейкового fallback и использует id только для внутреннего scope key.
@pytest.mark.asyncio
async def test_parse_visible_rows_reads_presentation_grid_and_derives_scope() -> None:
    class _Provider(FacebookAdsScannerProvider):
        def __init__(self, rows: list[dict[str, object]], header_map: dict[str, int]) -> None:
            super().__init__(settings=Settings())
            self._rows = rows
            self._header_map = header_map

        async def _extract_presentation_rows(self, page) -> list[dict[str, object]]:
            return self._rows

        async def _extract_header_map(self, page) -> dict[str, int]:
            return self._header_map

        async def _build_horizontal_passes(
            self, page, header_map: dict[str, int]
        ) -> tuple[int, ...]:
            return (0,)

        async def _set_horizontal_scroll(self, page, scroll_left: int) -> None:
            return None

    header_map = {
        "ad_name": 207,
        "delivery_status": 415,
        "spend": 1102,
        "clicks": 1218,
        "cpc": 1333,
        "leads": 1437,
        "cost_per_lead": 1552,
        "registrations": 1673,
        "cost_per_registration": 1807,
    }
    provider = _Provider(
        rows=[
            {
                "text": "DRC_CR2_CR013\nВыключено",
                "surfaces": [
                    "/am/table/table_row:120241420867480176unit/table_cell:forObjectType(name,ADGROUP)"
                ],
                "cells": [
                    {"text": "DRC_CR2_CR013\nДублировать\nОткрыть раскрывающееся меню", "x": 207},
                    {"text": "Выключено", "x": 415},
                    {"text": "28.81 $", "x": 1102},
                    {"text": "243", "x": 1218},
                    {"text": "0.12 $", "x": 1333},
                    {"text": "5", "x": 1437},
                    {"text": "5.76 $", "x": 1552},
                    {"text": "3", "x": 1673},
                    {"text": "9.60 $", "x": 1807},
                ],
            }
        ],
        header_map=header_map,
    )

    rows = await provider._parse_visible_rows(
        page=SimpleNamespace(),
        header_map=header_map,
        page_url=(
            "https://adsmanager.facebook.com/adsmanager/manage/ads"
            "?selected_campaign_ids=120241420128910176"
        ),
    )

    assert len(rows) == 1
    assert rows[0].fb_ad_id == "120241420867480176"
    assert rows[0].campaign_name == "Кампания 120241420128910176"
    assert rows[0].adset_name == ""
    assert rows[0].adset_scope_key.endswith(":120241420867480176")
    assert rows[0].ad_name == "DRC_CR2_CR013"
    assert rows[0].delivery_status == DeliveryStatus.PAUSED
    assert rows[0].deposits == 0


# Проверяет, что scanner предпочитает явные колонки кампании и адсета вместо fallback из URL.
@pytest.mark.asyncio
async def test_parse_visible_rows_prefers_explicit_campaign_and_adset_columns() -> None:
    class _Provider(FacebookAdsScannerProvider):
        def __init__(self, rows: list[dict[str, object]], header_map: dict[str, int]) -> None:
            super().__init__(settings=Settings())
            self._rows = rows
            self._header_map = header_map

        async def _extract_presentation_rows(self, page) -> list[dict[str, object]]:
            return self._rows

        async def _extract_header_map(self, page) -> dict[str, int]:
            return self._header_map

        async def _build_horizontal_passes(
            self, page, header_map: dict[str, int]
        ) -> tuple[int, ...]:
            return (0,)

        async def _set_horizontal_scroll(self, page, scroll_left: int) -> None:
            return None

    header_map = {
        "ad_name": 207,
        "delivery_status": 666,
        "spend": 1590,
        "clicks": 1835,
        "cpc": 1981,
        "leads": 2203,
        "cost_per_lead": 2349,
        "registrations": 2571,
        "cost_per_registration": 2717,
        "campaign_name": 3085,
        "adset_name": 3544,
    }
    provider = _Provider(
        rows=[
            {
                "text": "DRC_CR2_CR013\nВыключено",
                "surfaces": [
                    "/am/table/table_row:120241420867480176unit/table_cell:forObjectType(name,ADGROUP)"
                ],
                "cells": [
                    {"text": "DRC_CR2_CR013", "x": 207},
                    {"text": "Выключено", "x": 666},
                    {"text": "28.81 $", "x": 1590},
                    {"text": "243", "x": 1835},
                    {"text": "0.12 $", "x": 1981},
                    {"text": "5", "x": 2203},
                    {"text": "5.76 $", "x": 2349},
                    {"text": "3", "x": 2571},
                    {"text": "9.60 $", "x": 2717},
                    {"text": "Кампания Alpha", "x": 3085},
                    {"text": "Адсет Beta", "x": 3544},
                ],
            }
        ],
        header_map=header_map,
    )

    rows = await provider._parse_visible_rows(
        page=SimpleNamespace(),
        header_map=header_map,
        page_url=(
            "https://adsmanager.facebook.com/adsmanager/manage/ads"
            "?selected_campaign_ids=120241420128910176"
        ),
    )

    assert len(rows) == 1
    assert rows[0].campaign_name == "Кампания Alpha"
    assert rows[0].adset_name == "Адсет Beta"


# Проверяет, что scanner вычищает служебный хвост из adset-колонки и сохраняет только реальное имя.
@pytest.mark.asyncio
async def test_parse_visible_rows_ignores_service_text_in_adset_column() -> None:
    class _Provider(FacebookAdsScannerProvider):
        def __init__(self, rows: list[dict[str, object]], header_map: dict[str, int]) -> None:
            super().__init__(settings=Settings())
            self._rows = rows
            self._header_map = header_map

        async def _extract_presentation_rows(self, page) -> list[dict[str, object]]:
            return self._rows

        async def _extract_header_map(self, page) -> dict[str, int]:
            return self._header_map

        async def _build_horizontal_passes(
            self, page, header_map: dict[str, int]
        ) -> tuple[int, ...]:
            return (0,)

        async def _set_horizontal_scroll(self, page, scroll_left: int) -> None:
            return None

    header_map = {
        "ad_name": 207,
        "delivery_status": 666,
        "spend": 1590,
        "clicks": 1835,
        "cpc": 1981,
        "leads": 2203,
        "cost_per_lead": 2349,
        "registrations": 2571,
        "cost_per_registration": 2717,
        "campaign_name": 3085,
        "adset_name": 3544,
    }
    provider = _Provider(
        rows=[
            {
                "text": "DRC_CR2_CR013\nВыключено",
                "surfaces": [
                    "/am/table/table_row:120241420867480176unit/table_cell:forObjectType(name,ADGROUP)"
                ],
                "cells": [
                    {"text": "DRC_CR2_CR013", "x": 207},
                    {"text": "Выключено", "x": 666},
                    {"text": "28.81 $", "x": 1590},
                    {"text": "243", "x": 1835},
                    {"text": "0.12 $", "x": 1981},
                    {"text": "5", "x": 2203},
                    {"text": "5.76 $", "x": 2349},
                    {"text": "3", "x": 2571},
                    {"text": "9.60 $", "x": 2717},
                    {"text": "Кампания Alpha", "x": 3085},
                    {"text": "3\nАктивные объявления: 0", "x": 3544},
                ],
            }
        ],
        header_map=header_map,
    )

    rows = await provider._parse_visible_rows(
        page=SimpleNamespace(),
        header_map=header_map,
        page_url="https://adsmanager.facebook.com/adsmanager/manage/ads",
    )

    assert len(rows) == 1
    assert rows[0].campaign_name == "Кампания Alpha"
    assert rows[0].adset_name == "3"


# Проверяет, что scanner закрывает известное блокирующее окно Meta перед чтением видимых строк таблицы.
@pytest.mark.asyncio
async def test_parse_current_view_rows_dismisses_known_blocking_popup() -> None:
    class _ActionTarget:
        def __init__(self, page) -> None:
            self._page = page

        async def click(self) -> None:
            self._page.clicked.append("ОК")
            self._page.modal_visible = False
            self._page.body_text = ""

    class _Page:
        def __init__(self) -> None:
            self.body_text = (
                "Выключите блокирование рекламы\n"
                "Рекламные инструменты Meta могут работать не так, как ожидается."
            )
            self.modal_visible = True
            self.clicked: list[str] = []

        def get_by_role(self, role: str, name: str):
            if role == "button" and self.modal_visible and name == "ОК":
                return _ActionTarget(self)
            raise AssertionError(f"Неожиданный запрос роли: {role} {name}")

        async def wait_for_timeout(self, delay_ms: int) -> None:
            return None

    class _Provider(FacebookAdsScannerProvider):
        async def _extract_header_map(self, page) -> dict[str, int]:
            if page.modal_visible:
                raise RuntimeError("Модальное окно не закрыто")
            return {
                "ad_name": 1,
                "delivery_status": 2,
                "spend": 3,
                "clicks": 4,
                "cpc": 5,
                "leads": 6,
                "cost_per_lead": 7,
                "registrations": 8,
                "cost_per_registration": 9,
            }

        async def _parse_visible_rows(
            self,
            page,
            header_map: dict[str, int],
            page_url: str,
        ) -> list[ScannedAdRow]:
            assert page.modal_visible is False
            campaign_scope_key = build_campaign_scope_key("Кампания Alpha")
            return [
                ScannedAdRow(
                    fb_ad_id="120241420867480176",
                    campaign_scope_key=campaign_scope_key,
                    adset_scope_key=build_adset_scope_key("3", campaign_scope_key),
                    campaign_name="Кампания Alpha",
                    adset_name="3",
                    ad_name="DRC_CR2_CR013",
                    delivery_status=DeliveryStatus.ACTIVE,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.16"),
                    clicks=0,
                    cpc=None,
                    leads=0,
                    cost_per_lead=None,
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                )
            ]

    provider = _Provider(settings=Settings())
    page = _Page()

    rows = await provider._parse_current_view_rows(
        page,
        "https://adsmanager.facebook.com/adsmanager/manage/ads",
    )

    assert len(rows) == 1
    assert page.clicked == ["ОК"]
    assert page.modal_visible is False


# Проверяет, что scanner умеет закрывать тот же popup по варианту кнопки Ok, а не только по ОК.
@pytest.mark.asyncio
async def test_parse_current_view_rows_dismisses_popup_with_ok_variant() -> None:
    class _PopupButton:
        def __init__(self, page: "_Page", name: str) -> None:
            self._page = page
            self._name = name

        async def click(self) -> None:
            self._page.clicked.append(self._name)
            self._page.modal_visible = False
            self._page.body_text = ""

        async def is_visible(self) -> bool:
            return self._page.modal_visible

        async def count(self) -> int:
            return 1

        def nth(self, index: int) -> "_PopupButton":
            return self

    class _PopupDialog:
        def __init__(self, page: "_Page") -> None:
            self._page = page

        def get_by_role(self, role: str, name: str):
            if role == "button" and name == "Ok":
                return _PopupButton(self._page, "Ok")
            raise AssertionError(f"Неожиданная роль внутри dialog: {role} {name}")

        def locator(self, selector: str):
            if selector.startswith("button:has-text(") or selector.startswith(
                "[role='button']:has-text("
            ):
                return _PopupButton(self._page, "Ok")
            if selector.startswith("text="):
                return _PopupButton(self._page, "Ok")
            raise AssertionError(f"Неожиданный селектор dialog: {selector}")

        async def is_visible(self) -> bool:
            return self._page.modal_visible

    class _Page:
        def __init__(self) -> None:
            self.body_text = (
                "Выключите блокирование рекламы\n"
                "Рекламные инструменты Meta могут работать не так, как ожидается."
            )
            self.modal_visible = True
            self.clicked: list[str] = []

        def get_by_role(self, role: str, name: str | None = None):
            if role == "dialog":
                return _PopupDialog(self)
            if role == "button" and name == "Ok":
                raise AssertionError("Нежелательный поиск кнопки вне dialog")
            raise AssertionError(f"Неожиданная роль: {role} {name}")

        def locator(self, selector: str):
            if selector in ("[role='dialog']", "[aria-modal='true']"):
                return _PopupDialog(self)
            if selector.startswith("button:has-text(") or selector.startswith(
                "[role='button']:has-text("
            ):
                raise AssertionError("Нежелательный поиск кнопки вне dialog")
            if selector.startswith("text="):
                raise AssertionError("Нежелательный поиск кнопки вне dialog")
            raise AssertionError(f"Неожиданный селектор страницы: {selector}")

        async def wait_for_timeout(self, delay_ms: int) -> None:
            return None

    class _Provider(FacebookAdsScannerProvider):
        async def _extract_header_map(self, page) -> dict[str, int]:
            if page.modal_visible:
                raise RuntimeError("Модальное окно не закрыто")
            return {
                "ad_name": 1,
                "delivery_status": 2,
                "spend": 3,
                "clicks": 4,
                "cpc": 5,
                "leads": 6,
                "cost_per_lead": 7,
                "registrations": 8,
                "cost_per_registration": 9,
            }

        async def _parse_visible_rows(
            self,
            page,
            header_map: dict[str, int],
            page_url: str,
        ) -> list[ScannedAdRow]:
            assert page.modal_visible is False
            campaign_scope_key = build_campaign_scope_key("Кампания Alpha")
            return [
                ScannedAdRow(
                    fb_ad_id="120241420867480176",
                    campaign_scope_key=campaign_scope_key,
                    adset_scope_key=build_adset_scope_key("3", campaign_scope_key),
                    campaign_name="Кампания Alpha",
                    adset_name="3",
                    ad_name="DRC_CR2_CR013",
                    delivery_status=DeliveryStatus.ACTIVE,
                    tracking_mode=TrackingMode.TRACKED,
                    scope_presence=ScopePresence.IN_SCOPE,
                    spend=Decimal("0.16"),
                    clicks=0,
                    cpc=None,
                    leads=0,
                    cost_per_lead=None,
                    registrations=0,
                    cost_per_registration=None,
                    deposits=0,
                )
            ]

    provider = _Provider(settings=Settings())
    page = _Page()

    rows = await provider._parse_current_view_rows(
        page,
        "https://adsmanager.facebook.com/adsmanager/manage/ads",
    )

    assert len(rows) == 1
    assert page.clicked == ["Ok"]
    assert page.modal_visible is False


# Проверяет, что scanner прокручивает таблицу через mouse wheel по области строк.
@pytest.mark.asyncio
async def test_scroll_once_uses_mouse_wheel_on_table_area() -> None:
    class _Locator:
        async def count(self) -> int:
            return 5

    class _Mouse:
        def __init__(self) -> None:
            self.moves: list[tuple[int, int]] = []
            self.wheels: list[tuple[int, int]] = []

        async def move(self, x: int, y: int) -> None:
            self.moves.append((x, y))

        async def wheel(self, dx: int, dy: int) -> None:
            self.wheels.append((dx, dy))

    class _Page:
        def __init__(self) -> None:
            self.mouse = _Mouse()
            self._states = [
                {
                    "page_scroll_top": 0,
                    "anchor_x": 140,
                    "anchor_y": 360,
                    "signature": "before",
                },
                {
                    "page_scroll_top": 0,
                    "anchor_x": 140,
                    "anchor_y": 360,
                    "signature": "after",
                },
            ]

        def locator(self, selector: str) -> _Locator:
            assert selector == "div[role='presentation']._1gd4"
            return _Locator()

        async def evaluate(self, script: str, params: dict[str, object]) -> dict[str, object]:
            assert params["selector"] == "div[role='presentation']._1gd4"
            return self._states.pop(0)

        async def wait_for_timeout(self, delay_ms: int) -> None:
            return None

    provider = FacebookAdsScannerProvider(settings=Settings())
    page = _Page()
    scrolled = await provider._scroll_once(page)

    assert scrolled is True
    assert page.mouse.moves == [(140, 360)]
    assert page.mouse.wheels == [(0, provider._settings.scanner_scroll_step_px)]


# Проверяет, что scanner читает ожидаемое число строк из заголовка вкладки Ads Manager.
@pytest.mark.asyncio
async def test_extract_expected_rows_count_reads_title_prefix() -> None:
    provider = FacebookAdsScannerProvider(settings=Settings())
    expected_rows_count = provider._extract_expected_rows_count(
        "(51) Ads Manager - Управление рекламой"
    )

    assert expected_rows_count == 51


# Проверяет, что scanner предпочитает счетчик объявлений из футера таблицы, если он доступен.
@pytest.mark.asyncio
async def test_read_expected_rows_count_prefers_results_footer() -> None:
    provider = FacebookAdsScannerProvider(settings=Settings())

    class _Page:
        async def evaluate(self, script: str, params: dict[str, object]) -> int:
            assert params["selector"] == "div[role='presentation']._1gd4"
            return 51

    expected_rows_count = await provider._read_expected_rows_count(
        _Page(),
        "(54) Ads Manager - Управление рекламой",
    )

    assert expected_rows_count == 51


# Проверяет, что scanner считает response неполным, если строк меньше ожидаемого количества.
def test_is_complete_response_rows_requires_full_expected_scope() -> None:
    rows = [SimpleNamespace() for _ in range(42)]

    assert FacebookAdsScannerProvider._is_complete_response_rows(rows, 51) is False


# Проверяет, что scanner не берет фейковый campaign id из URL, если в фильтре выбрано несколько кампаний.
def test_extract_scope_context_ignores_multi_selected_campaign_ids() -> None:
    scope_context = FacebookAdsScannerProvider._extract_scope_context(
        "https://adsmanager.facebook.com/adsmanager/manage/ads"
        "?selected_campaign_ids=120241420128910176,120241420128900176"
        "&selected_adset_ids=120241420867470176,120241420867460176"
    )

    assert scope_context["campaign_name"] is None
    assert scope_context["adset_name"] is None


# Проверяет, что scanner умеет извлекать строки объявления из graphql nodes, если Facebook не прислал am_tabular.
def test_parse_response_rows_supports_graphql_adgroup_nodes() -> None:
    provider = FacebookAdsScannerProvider(settings=Settings())

    rows = provider._parse_response_rows(
        [
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "Adgroup",
                            "id": "120241420867480176",
                            "name": "DRC_CR2_CR013",
                            "ad_campaign_name": "3",
                            "delivery_status": {
                                "status": "OFF",
                                "substatuses": [{"id": "OFF", "text": "Выключено"}],
                            },
                        }
                    ]
                }
            }
        ],
        "https://adsmanager.facebook.com/adsmanager/manage/ads",
    )

    assert len(rows) == 1
    assert rows[0].fb_ad_id == "120241420867480176"
    assert rows[0].ad_name == "DRC_CR2_CR013"
    assert rows[0].adset_name == "3"
    assert rows[0].delivery_status == DeliveryStatus.PAUSED


# Проверяет, что scanner восстанавливает реальные campaign и adset из связанных graphql-узлов и не подменяет их multi-select URL.
def test_parse_response_rows_resolves_scope_names_from_graphql_references() -> None:
    provider = FacebookAdsScannerProvider(settings=Settings())

    rows = provider._parse_response_rows(
        [
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "Adgroup",
                            "id": "120241420867480176",
                            "name": "DRC_CR2_CR013",
                            "ad_campaign_id": "120241420867470176",
                            "delivery_status": {
                                "status": "OFF",
                                "substatuses": [{"id": "OFF", "text": "Выключено"}],
                            },
                        },
                        {
                            "__typename": "AdCampaign",
                            "id": "120241420867470176",
                            "name": "3",
                            "ad_campaign_group_id": "120241420128910176",
                        },
                        {
                            "__typename": "AdCampaignGroup",
                            "id": "120241420128910176",
                            "name": "Кампания Alpha",
                        },
                    ]
                }
            }
        ],
        (
            "https://adsmanager.facebook.com/adsmanager/manage/ads"
            "?selected_campaign_ids=120241420128910176,120241420128900176"
        ),
    )

    assert len(rows) == 1
    assert rows[0].campaign_name == "Кампания Alpha"
    assert rows[0].adset_name == "3"
    assert rows[0].ad_name == "DRC_CR2_CR013"


# Проверяет, что scanner собирает из graphql только объявления и берет имя адсета из связанных AdCampaign nodes.
def test_parse_graphql_rows_uses_adgroup_and_adcampaign_nodes() -> None:
    provider = FacebookAdsScannerProvider(settings=Settings())

    rows = provider._parse_graphql_rows(
        [
            {
                "data": {
                    "nodes": [
                        {
                            "__typename": "Adgroup",
                            "id": "120241420867480176",
                            "name": "DRC_CR2_CR013",
                            "ad_campaign_id": "120241420867470176",
                            "delivery_status": {
                                "status": "OFF",
                                "substatuses": [{"id": "OFF", "text": "Выключено"}],
                            },
                        },
                        {
                            "__typename": "AdCampaign",
                            "id": "120241420867470176",
                            "name": "3",
                        },
                    ]
                }
            }
        ],
        "https://adsmanager.facebook.com/adsmanager/manage/ads",
    )

    assert len(rows) == 1
    assert rows[0].fb_ad_id == "120241420867480176"
    assert rows[0].ad_name == "DRC_CR2_CR013"
    assert rows[0].adset_name == "3"
    assert rows[0].delivery_status == DeliveryStatus.PAUSED


# Проверяет, что merge строк сохраняет реальные scope-имена и ненулевые метрики, даже если поздний источник принес URL-фолбэк.
def test_merge_scanned_rows_keeps_real_scope_names_and_metrics() -> None:
    campaign_scope_key = build_campaign_scope_key("Кампания Alpha")
    placeholder_campaign_scope_key = build_campaign_scope_key("Кампания 120241420128910176")
    response_row = ScannedAdRow(
        fb_ad_id="120241420867480176",
        campaign_scope_key=campaign_scope_key,
        adset_scope_key=build_adset_scope_key("3", campaign_scope_key),
        campaign_name="Кампания Alpha",
        adset_name="3",
        ad_name="DRC_CR2_CR013",
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("28.81"),
        clicks=243,
        cpc=Decimal("0.12"),
        leads=5,
        cost_per_lead=Decimal("5.76"),
        registrations=3,
        cost_per_registration=Decimal("9.60"),
        deposits=2,
    )
    visible_row = ScannedAdRow(
        fb_ad_id="120241420867480176",
        campaign_scope_key=placeholder_campaign_scope_key,
        adset_scope_key=build_adset_scope_key(
            "120241420867480176",
            placeholder_campaign_scope_key,
        ),
        campaign_name="Кампания 120241420128910176",
        adset_name="",
        ad_name="DRC_CR2_CR013",
        delivery_status=DeliveryStatus.PAUSED,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        spend=Decimal("28.81"),
        clicks=243,
        cpc=Decimal("0.12"),
        leads=5,
        cost_per_lead=Decimal("5.76"),
        registrations=3,
        cost_per_registration=Decimal("9.60"),
        deposits=0,
    )

    rows = FacebookAdsScannerProvider._merge_scanned_rows([response_row], [visible_row])

    assert len(rows) == 1
    assert rows[0].campaign_name == "Кампания Alpha"
    assert rows[0].adset_name == "3"
    assert rows[0].deposits == 2


class _FakeResponse:
    """Заглушка response Ads Manager для проверки response-first scanner."""

    def __init__(self, url: str, payload: object) -> None:
        self.url = url
        self._payload = payload

    async def json(self) -> object:
        return self._payload

    async def text(self) -> str:
        return json.dumps(self._payload)


class _FakeResponsePage:
    """Заглушка страницы Playwright с response-событиями и reload-циклами."""

    def __init__(
        self,
        response_batches: list[list[_FakeResponse]],
        title_text: str = "Ads Manager",
        visible_batches: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self._response_batches = response_batches
        self._response_handlers: list[object] = []
        self.reload_count = 0
        self.url = "https://adsmanager.facebook.com/adsmanager/manage/ads"
        self._title_text = title_text
        self.visible_batches = visible_batches or []
        self.visible_index = 0

    async def bring_to_front(self) -> None:
        return None

    async def title(self) -> str:
        return self._title_text

    def on(self, event: str, handler) -> None:
        assert event == "response"
        self._response_handlers.append(handler)

    def remove_listener(self, event: str, handler) -> None:
        assert event == "response"
        if handler in self._response_handlers:
            self._response_handlers.remove(handler)

    async def reload(self, wait_until: str = "domcontentloaded") -> None:
        assert wait_until == "domcontentloaded"
        self.reload_count += 1
        batch_index = self.reload_count - 1
        if batch_index >= len(self._response_batches):
            return
        for response in self._response_batches[batch_index]:
            for handler in list(self._response_handlers):
                handler(response)

    async def wait_for_timeout(self, delay_ms: int) -> None:
        return None


# Проверяет, что scanner читает строки из response Ads Manager без опоры на DOM-таблицу.
@pytest.mark.asyncio
async def test_scan_rows_reads_response_payloads_without_dom_fallback() -> None:
    response_page = _FakeResponsePage(
        response_batches=[
            [
                _FakeResponse(
                    "https://adsmanager.facebook.com/am_tabular",
                    {
                        "data": [
                            {
                                "rows": [
                                    {
                                        "dimension_values": {
                                            "ad_id": "120241420867480176",
                                            "campaign_name": "Кампания Alpha",
                                            "adset_name": "Адсет Beta",
                                            "ad_name": "DRC_CR2_CR013",
                                            "delivery_status": "Выключено",
                                        },
                                        "atomic_values": {
                                            "spend": "28.81 $",
                                            "clicks": "243",
                                            "cpc": "0.12 $",
                                            "leads": "5",
                                            "cost_per_lead": "5.76 $",
                                            "registrations": "3",
                                            "cost_per_registration": "9.60 $",
                                            "deposits": "1",
                                        },
                                    }
                                ]
                            }
                        ]
                    },
                ),
            ]
        ]
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(settings=Settings())

        async def _resolve_ads_page(self, browser):
            return response_page

    provider = _Provider()

    rows = await provider._scan_with_browser(
        browser=SimpleNamespace(contexts=[]),
        profile_id="profile-1",
        browser_host_name="host-1",
    )

    assert response_page.reload_count == 1
    assert len(rows) == 1
    assert rows[0].fb_ad_id == "120241420867480176"
    assert rows[0].campaign_name == "Кампания Alpha"
    assert rows[0].adset_name == "Адсет Beta"
    assert rows[0].ad_name == "DRC_CR2_CR013"
    assert rows[0].spend == Decimal("28.81")
    assert rows[0].clicks == 243
    assert rows[0].deposits == 1


# Проверяет, что scanner создает отдельную служебную страницу и не использует рабочую вкладку пользователя для reload.
@pytest.mark.asyncio
async def test_scan_rows_uses_dedicated_service_page() -> None:
    class _SeedPage:
        def __init__(self) -> None:
            self.url = (
                "https://adsmanager.facebook.com/adsmanager/manage/ads"
                "?act=1&selected_campaign_ids=120241420128910176"
            )

        async def title(self) -> str:
            return "Ads Manager"

        async def wait_for_load_state(self, state: str) -> None:
            return None

    class _ServicePage(_FakeResponsePage):
        def __init__(self, response_batches: list[list[_FakeResponse]]) -> None:
            super().__init__(response_batches=response_batches)
            self.goto_urls: list[str] = []

        async def goto(self, url: str, wait_until: str | None = None) -> None:
            self.url = url
            self.goto_urls.append(url)

        async def wait_for_load_state(self, state: str) -> None:
            return None

    class _Context:
        def __init__(self, pages, service_page) -> None:
            self.pages = pages
            self._service_page = service_page
            self.created_pages: list[object] = []

        async def new_page(self):
            self.pages.append(self._service_page)
            self.created_pages.append(self._service_page)
            return self._service_page

    seed_page = _SeedPage()
    service_page = _ServicePage(
        response_batches=[
            [
                _FakeResponse(
                    "https://adsmanager.facebook.com/am_tabular",
                    {
                        "data": [
                            {
                                "rows": [
                                    {
                                        "dimension_values": {
                                            "ad_id": "120241420867480176",
                                            "campaign_name": "Кампания Alpha",
                                            "adset_name": "Адсет Beta",
                                            "ad_name": "DRC_CR2_CR013",
                                            "delivery_status": "Выключено",
                                        },
                                        "atomic_values": {
                                            "spend": "28.81 $",
                                            "clicks": "243",
                                            "cpc": "0.12 $",
                                            "leads": "5",
                                            "cost_per_lead": "5.76 $",
                                            "registrations": "3",
                                            "cost_per_registration": "9.60 $",
                                            "deposits": "1",
                                        },
                                    }
                                ]
                            }
                        ]
                    },
                ),
            ]
        ]
    )
    context = _Context([seed_page], service_page)
    browser = SimpleNamespace(contexts=[context])
    provider = FacebookAdsScannerProvider(settings=Settings())

    rows = await provider._scan_with_browser(
        browser=browser,
        profile_id="profile-1",
        browser_host_name="host-1",
    )

    assert context.created_pages == [service_page]
    assert service_page.goto_urls
    assert "fb_agent_service=scanner" in service_page.goto_urls[0]
    assert "selected_campaign_ids=120241420128910176" in service_page.goto_urls[0]
    assert service_page.reload_count == 1
    assert len(rows) == 1
    assert rows[0].fb_ad_id == "120241420867480176"


# Проверяет, что scanner собирает полный scope через один reload и последующую прокрутку таблицы без дополнительных обновлений страницы.
@pytest.mark.asyncio
async def test_scan_rows_collects_full_scope_after_single_reload_with_table_scroll() -> None:
    header_map = {
        "ad_name": 207,
        "delivery_status": 666,
        "spend": 1590,
        "clicks": 1835,
        "cpc": 1981,
        "leads": 2203,
        "cost_per_lead": 2349,
        "registrations": 2571,
        "cost_per_registration": 2717,
        "campaign_name": 3085,
        "adset_name": 3544,
    }
    visible_batches = [
        [
            {
                "text": "CR005\nВыключено",
                "surfaces": [
                    "/am/table/table_row:120241420565820176unit/table_cell:forObjectType(name,ADGROUP)"
                ],
                "cells": [
                    {"text": "CR005", "x": 207},
                    {"text": "Выключено", "x": 666},
                    {"text": "1.00 $", "x": 1590},
                    {"text": "10", "x": 1835},
                    {"text": "0.10 $", "x": 1981},
                    {"text": "1", "x": 2203},
                    {"text": "1.00 $", "x": 2349},
                    {"text": "1", "x": 2571},
                    {"text": "1.00 $", "x": 2717},
                    {"text": "Кампания Alpha", "x": 3085},
                    {"text": "1", "x": 3544},
                ],
            },
            {
                "text": "CR006\nВыключено",
                "surfaces": [
                    "/am/table/table_row:120241420565830176unit/table_cell:forObjectType(name,ADGROUP)"
                ],
                "cells": [
                    {"text": "CR006", "x": 207},
                    {"text": "Выключено", "x": 666},
                    {"text": "2.00 $", "x": 1590},
                    {"text": "20", "x": 1835},
                    {"text": "0.10 $", "x": 1981},
                    {"text": "2", "x": 2203},
                    {"text": "1.00 $", "x": 2349},
                    {"text": "2", "x": 2571},
                    {"text": "1.00 $", "x": 2717},
                    {"text": "Кампания Alpha", "x": 3085},
                    {"text": "1", "x": 3544},
                ],
            },
        ],
        [
            {
                "text": "CR005\nВыключено",
                "surfaces": [
                    "/am/table/table_row:120241420565820176unit/table_cell:forObjectType(name,ADGROUP)"
                ],
                "cells": [
                    {"text": "CR005", "x": 207},
                    {"text": "Выключено", "x": 666},
                    {"text": "1.00 $", "x": 1590},
                    {"text": "10", "x": 1835},
                    {"text": "0.10 $", "x": 1981},
                    {"text": "1", "x": 2203},
                    {"text": "1.00 $", "x": 2349},
                    {"text": "1", "x": 2571},
                    {"text": "1.00 $", "x": 2717},
                    {"text": "Кампания Alpha", "x": 3085},
                    {"text": "1", "x": 3544},
                ],
            },
            {
                "text": "CR006\nВыключено",
                "surfaces": [
                    "/am/table/table_row:120241420565830176unit/table_cell:forObjectType(name,ADGROUP)"
                ],
                "cells": [
                    {"text": "CR006", "x": 207},
                    {"text": "Выключено", "x": 666},
                    {"text": "2.00 $", "x": 1590},
                    {"text": "20", "x": 1835},
                    {"text": "0.10 $", "x": 1981},
                    {"text": "2", "x": 2203},
                    {"text": "1.00 $", "x": 2349},
                    {"text": "2", "x": 2571},
                    {"text": "1.00 $", "x": 2717},
                    {"text": "Кампания Alpha", "x": 3085},
                    {"text": "1", "x": 3544},
                ],
            },
            {
                "text": "CR007\nВыключено",
                "surfaces": [
                    "/am/table/table_row:120241420565840176unit/table_cell:forObjectType(name,ADGROUP)"
                ],
                "cells": [
                    {"text": "CR007", "x": 207},
                    {"text": "Выключено", "x": 666},
                    {"text": "3.00 $", "x": 1590},
                    {"text": "30", "x": 1835},
                    {"text": "0.10 $", "x": 1981},
                    {"text": "3", "x": 2203},
                    {"text": "1.00 $", "x": 2349},
                    {"text": "3", "x": 2571},
                    {"text": "1.00 $", "x": 2717},
                    {"text": "Кампания Alpha", "x": 3085},
                    {"text": "1", "x": 3544},
                ],
            },
        ],
    ]
    response_page = _FakeResponsePage(
        response_batches=[[]],
        title_text="(3) Ads Manager - Управление рекламой",
        visible_batches=visible_batches,
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(settings=Settings())
            self.scroll_calls = 0

        async def _resolve_ads_page(self, browser):
            return response_page

        async def _extract_header_map(self, page) -> dict[str, int]:
            return header_map

        async def _extract_presentation_rows(self, page) -> list[dict[str, object]]:
            return page.visible_batches[page.visible_index]

        async def _build_horizontal_passes(
            self, page, header_map: dict[str, int]
        ) -> tuple[int, ...]:
            return (0,)

        async def _set_horizontal_scroll(self, page, scroll_left: int) -> None:
            return None

        async def _scroll_once(self, page) -> bool:
            self.scroll_calls += 1
            if page.visible_index >= len(page.visible_batches) - 1:
                return False
            page.visible_index += 1
            return True

    provider = _Provider()

    rows = await provider._scan_with_browser(
        browser=SimpleNamespace(contexts=[]),
        profile_id="profile-1",
        browser_host_name="host-1",
    )

    assert response_page.reload_count == 1
    assert provider.scroll_calls == 1
    assert len(rows) == 3
    assert {row.fb_ad_id for row in rows} == {
        "120241420565820176",
        "120241420565830176",
        "120241420565840176",
    }


# Проверяет, что scanner не снимает response-listener во время прокрутки и добирает объявления из graphql.
@pytest.mark.asyncio
async def test_scan_rows_collects_graphql_rows_while_scrolling_table() -> None:
    response_page = _FakeResponsePage(
        response_batches=[[]],
        title_text="(3) Ads Manager - Управление рекламой",
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(settings=Settings())
            self.scroll_calls = 0

        async def _resolve_ads_page(self, browser):
            return response_page

        async def _parse_current_view_rows(self, page, page_url: str):
            return []

        async def _scroll_once(self, page) -> bool:
            self.scroll_calls += 1
            if self.scroll_calls > 1:
                return False

            payload = {
                "data": {
                    "nodes": [
                        {
                            "__typename": "Adgroup",
                            "id": "120241420565820176",
                            "name": "CR005",
                            "ad_campaign_id": "9001",
                            "delivery_status": {
                                "substatuses": [{"text": "Выключено"}],
                            },
                            "spend": "1.00 $",
                            "clicks": "10",
                            "cpc": "0.10 $",
                        },
                        {
                            "__typename": "Adgroup",
                            "id": "120241420565830176",
                            "name": "CR006",
                            "ad_campaign_id": "9001",
                            "delivery_status": {
                                "substatuses": [{"text": "Выключено"}],
                            },
                            "spend": "2.00 $",
                            "clicks": "20",
                            "cpc": "0.10 $",
                        },
                        {
                            "__typename": "Adgroup",
                            "id": "120241420565840176",
                            "name": "CR007",
                            "ad_campaign_id": "9001",
                            "delivery_status": {
                                "substatuses": [{"text": "Выключено"}],
                            },
                            "spend": "3.00 $",
                            "clicks": "30",
                            "cpc": "0.10 $",
                        },
                        {
                            "__typename": "AdCampaign",
                            "id": "9001",
                            "name": "1",
                        },
                    ]
                }
            }
            for handler in list(page._response_handlers):
                handler(_FakeResponse("https://adsmanager.facebook.com/api/graphql", payload))
            return True

    provider = _Provider()

    rows = await provider._scan_with_browser(
        browser=SimpleNamespace(contexts=[]),
        profile_id="profile-1",
        browser_host_name="host-1",
    )

    assert response_page.reload_count == 1
    assert provider.scroll_calls == 1
    assert len(rows) == 3
    assert {row.fb_ad_id for row in rows} == {
        "120241420565820176",
        "120241420565830176",
        "120241420565840176",
    }


# Проверяет, что scanner продолжает прокрутку даже при полном числе строк, если в scope еще остались фолбэк-имена кампании.
@pytest.mark.asyncio
async def test_scan_rows_keeps_scrolling_until_scope_names_are_enriched() -> None:
    response_page = _FakeResponsePage(
        response_batches=[
            [
                _FakeResponse(
                    "https://adsmanager.facebook.com/am_tabular",
                    {
                        "rows": [
                            {
                                "id": "120241420318420176",
                                "campaign_name": "Кампания 120241419517550176",
                                "adset_name": "1",
                                "ad_name": "DRC_CR2_CR004",
                                "delivery_status": "ACTIVE",
                            },
                            {
                                "id": "120241420398970176",
                                "campaign_name": "Кампания 120241419517550176",
                                "adset_name": "2",
                                "ad_name": "DRC_CR2_CR004",
                                "delivery_status": "ACTIVE",
                            },
                            {
                                "id": "120241420399000176",
                                "campaign_name": "Кампания 120241419517550176",
                                "adset_name": "3",
                                "ad_name": "DRC_CR2_CR002",
                                "delivery_status": "ACTIVE",
                            },
                        ]
                    },
                )
            ]
        ],
        title_text="(3) Ads Manager - Управление рекламой",
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(settings=Settings())
            self.scroll_calls = 0
            self.visible_batches = [
                [
                    ScannedAdRow(
                        fb_ad_id="120241420318420176",
                        campaign_scope_key=build_campaign_scope_key(
                            "CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03"
                        ),
                        adset_scope_key=build_adset_scope_key(
                            "1",
                            build_campaign_scope_key(
                                "CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03"
                            ),
                        ),
                        campaign_name="CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03",
                        adset_name="1",
                        ad_name="DRC_CR2_CR004",
                        delivery_status=DeliveryStatus.ACTIVE,
                        tracking_mode=TrackingMode.TRACKED,
                        scope_presence=ScopePresence.IN_SCOPE,
                        spend=Decimal("0"),
                    ),
                ],
                [
                    ScannedAdRow(
                        fb_ad_id="120241420398970176",
                        campaign_scope_key=build_campaign_scope_key(
                            "CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03"
                        ),
                        adset_scope_key=build_adset_scope_key(
                            "2",
                            build_campaign_scope_key(
                                "CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03"
                            ),
                        ),
                        campaign_name="CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03",
                        adset_name="2",
                        ad_name="DRC_CR2_CR004",
                        delivery_status=DeliveryStatus.ACTIVE,
                        tracking_mode=TrackingMode.TRACKED,
                        scope_presence=ScopePresence.IN_SCOPE,
                        spend=Decimal("0"),
                    ),
                ],
                [
                    ScannedAdRow(
                        fb_ad_id="120241420399000176",
                        campaign_scope_key=build_campaign_scope_key(
                            "CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03"
                        ),
                        adset_scope_key=build_adset_scope_key(
                            "3",
                            build_campaign_scope_key(
                                "CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03"
                            ),
                        ),
                        campaign_name="CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03",
                        adset_name="3",
                        ad_name="DRC_CR2_CR002",
                        delivery_status=DeliveryStatus.ACTIVE,
                        tracking_mode=TrackingMode.TRACKED,
                        scope_presence=ScopePresence.IN_SCOPE,
                        spend=Decimal("0"),
                    ),
                ],
            ]
            self.visible_index = 0

        async def _resolve_ads_page(self, browser):
            return response_page

        async def _parse_current_view_rows(self, page, page_url: str):
            return self.visible_batches[self.visible_index]

        async def _scroll_once(self, page) -> bool:
            self.scroll_calls += 1
            if self.visible_index >= len(self.visible_batches) - 1:
                return False
            self.visible_index += 1
            return True

    provider = _Provider()

    rows = await provider._scan_with_browser(
        browser=SimpleNamespace(contexts=[]),
        profile_id="profile-1",
        browser_host_name="host-1",
    )

    assert response_page.reload_count == 1
    assert provider.scroll_calls == 2
    assert len(rows) == 3
    assert {row.campaign_name for row in rows} == {"CR2 | DRC | MV | UPD MZ | pwa.partners | 15.03"}


# Проверяет, что scanner повторяет reload после неполного scope и добирает строки на следующей попытке.
@pytest.mark.asyncio
async def test_scan_rows_retries_reload_and_succeeds_on_second_attempt() -> None:
    partial_rows = [
        {
            "id": "120241420000000001",
            "campaign_name": "Кампания Alpha",
            "adset_name": "1",
            "ad_name": "CR001",
            "delivery_status": "ACTIVE",
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
        },
        {
            "id": "120241420000000002",
            "campaign_name": "Кампания Alpha",
            "adset_name": "1",
            "ad_name": "CR002",
            "delivery_status": "ACTIVE",
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
        },
    ]
    full_rows = [
        *partial_rows,
        {
            "id": "120241420000000003",
            "campaign_name": "Кампания Alpha",
            "adset_name": "1",
            "ad_name": "CR003",
            "delivery_status": "ACTIVE",
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
        },
    ]
    response_page = _FakeResponsePage(
        response_batches=[
            [_FakeResponse("https://adsmanager.facebook.com/am_tabular", {"rows": partial_rows})],
            [_FakeResponse("https://adsmanager.facebook.com/am_tabular", {"rows": full_rows})],
        ],
        title_text="(3) Ads Manager - Управление рекламой",
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(settings=Settings(scanner_reload_attempts=3))

        async def _resolve_ads_page(self, browser):
            return response_page

    provider = _Provider()

    rows = await provider._scan_with_browser(
        browser=SimpleNamespace(contexts=[]),
        profile_id="profile-1",
        browser_host_name="host-1",
    )

    assert response_page.reload_count == 2
    assert len(rows) == 3
    assert {row.fb_ad_id for row in rows} == {
        "120241420000000001",
        "120241420000000002",
        "120241420000000003",
    }


# Проверяет, что scanner сначала делает дополнительный проход по текущей странице без нового reload.
@pytest.mark.asyncio
async def test_scan_rows_retries_in_current_page_before_next_reload() -> None:
    partial_rows = [
        {
            "id": "120241420000000001",
            "campaign_name": "Кампания Alpha",
            "adset_name": "1",
            "ad_name": "CR001",
            "delivery_status": "ACTIVE",
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
        },
        {
            "id": "120241420000000002",
            "campaign_name": "Кампания Alpha",
            "adset_name": "1",
            "ad_name": "CR002",
            "delivery_status": "ACTIVE",
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
        },
    ]
    full_rows = [
        *partial_rows,
        {
            "id": "120241420000000003",
            "campaign_name": "Кампания Alpha",
            "adset_name": "1",
            "ad_name": "CR003",
            "delivery_status": "ACTIVE",
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
        },
    ]
    response_page = _FakeResponsePage(
        response_batches=[
            [_FakeResponse("https://adsmanager.facebook.com/am_tabular", {"rows": partial_rows})],
        ],
        title_text="(3) Ads Manager - Управление рекламой",
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(
                settings=Settings(
                    scanner_reload_attempts=1,
                    scanner_same_page_retry_passes=1,
                    scanner_max_no_new_attempts=1,
                )
            )
            self.same_page_retry_calls = 0

        async def _resolve_ads_page(self, browser):
            return response_page

        async def _restart_vertical_collection_pass(self, page) -> None:
            self.same_page_retry_calls += 1
            for handler in list(page._response_handlers):
                handler(
                    _FakeResponse("https://adsmanager.facebook.com/am_tabular", {"rows": full_rows})
                )

    provider = _Provider()

    rows = await provider._scan_with_browser(
        browser=SimpleNamespace(contexts=[]),
        profile_id="profile-1",
        browser_host_name="host-1",
    )

    assert response_page.reload_count == 1
    assert provider.same_page_retry_calls == 1
    assert len(rows) == 3


# Проверяет, что scanner не принимает неполный response как успешный и падает только после исчерпания retry-бюджета.
@pytest.mark.asyncio
async def test_scan_rows_fails_after_single_reload_when_response_is_smaller_than_title_count() -> (
    None
):
    response_rows = [
        {
            "id": str(120241420000000000 + index),
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
            "delivery_status": "PAUSED",
        }
        for index in range(42)
    ]
    response_page = _FakeResponsePage(
        response_batches=[
            [_FakeResponse("https://adsmanager.facebook.com/am_tabular", {"rows": response_rows})],
        ],
        title_text="(51) Ads Manager - Управление рекламой",
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(settings=Settings(scanner_reload_attempts=3))

        async def _resolve_ads_page(self, browser):
            return response_page

    provider = _Provider()

    with pytest.raises(
        ScannerScopeUnavailableError,
        match="получено меньше ожидаемых 51 строк после повторных обновлений страницы \\(попыток: 3\\)",
    ):
        await provider._scan_with_browser(
            browser=SimpleNamespace(contexts=[]),
            profile_id="profile-1",
            browser_host_name="host-1",
        )

    assert response_page.reload_count == 3


# Проверяет, что scanner делает несколько reload-попыток и поднимает ошибку, если response так и не дал строк.
@pytest.mark.asyncio
async def test_scan_rows_fails_after_single_reload_when_response_is_empty() -> None:
    response_page = _FakeResponsePage(response_batches=[[]])

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(settings=Settings(scanner_reload_attempts=3))

        async def _resolve_ads_page(self, browser):
            return response_page

    provider = _Provider()

    with pytest.raises(
        ScannerScopeUnavailableError,
        match="полный набор строк Ads Manager после повторных обновлений страницы \\(попыток: 3\\)",
    ):
        await provider._scan_with_browser(
            browser=SimpleNamespace(contexts=[]),
            profile_id="profile-1",
            browser_host_name="host-1",
        )

    assert response_page.reload_count == 3


# Проверяет, что scanner пишет диагностический отчет по лучшей попытке response, если scope так и остался неполным.
@pytest.mark.asyncio
async def test_scan_rows_writes_probe_report_for_incomplete_scope(tmp_path) -> None:
    response_rows = [
        {
            "id": str(120241420000000000 + index),
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
            "delivery_status": "PAUSED",
        }
        for index in range(42)
    ]
    response_page = _FakeResponsePage(
        response_batches=[
            [_FakeResponse("https://adsmanager.facebook.com/am_tabular", {"rows": response_rows})],
        ],
        title_text="(51) Ads Manager - Управление рекламой",
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(
                settings=Settings(
                    scanner_reload_attempts=3,
                    scanner_response_probe_enabled=True,
                    scanner_response_probe_dir=str(tmp_path),
                )
            )

        async def _resolve_ads_page(self, browser):
            return response_page

    provider = _Provider()

    with pytest.raises(
        ScannerScopeUnavailableError,
        match="получено меньше ожидаемых 51 строк после повторных обновлений страницы \\(попыток: 3\\)",
    ):
        await provider._scan_with_browser(
            browser=SimpleNamespace(contexts=[]),
            profile_id="profile-1",
            browser_host_name="vision-3030",
        )

    latest_reports = list(tmp_path.glob("*__latest.json"))
    assert len(latest_reports) == 1
    report_payload = json.loads(latest_reports[0].read_text(encoding="utf-8"))
    assert report_payload["expected_rows_count"] == 51
    assert report_payload["parsed_row_count"] == 42
    assert report_payload["captured_response_count"] == 1
    assert report_payload["responses"][0]["row_list_total"] == 42


# Проверяет, что временный probe сохраняет соседние JSON-response Facebook и не мешает основному парсеру строк даже после retry.
@pytest.mark.asyncio
async def test_scan_rows_probe_captures_additional_facebook_json_responses(tmp_path) -> None:
    response_rows = [
        {
            "id": str(120241420000000000 + index),
            "spend": "0.00",
            "clicks": "0",
            "cpc": "0.00",
            "leads": "0",
            "cost_per_lead": "0.00",
            "registrations": "0",
            "cost_per_registration": "0.00",
            "delivery_status": "PAUSED",
        }
        for index in range(42)
    ]
    response_page = _FakeResponsePage(
        response_batches=[
            [
                _FakeResponse(
                    "https://graph.facebook.com/graphql",
                    {
                        "data": {
                            "viewer": {
                                "ads": [
                                    {"id": "120241420999999991"},
                                    {"id": "120241420999999992"},
                                ]
                            }
                        }
                    },
                ),
                _FakeResponse(
                    "https://adsmanager.facebook.com/am_tabular", {"rows": response_rows}
                ),
            ],
        ],
        title_text="(51) Ads Manager - Управление рекламой",
    )

    class _Provider(FacebookAdsScannerProvider):
        def __init__(self) -> None:
            super().__init__(
                settings=Settings(
                    scanner_reload_attempts=3,
                    scanner_response_probe_enabled=True,
                    scanner_response_probe_dir=str(tmp_path),
                )
            )

        async def _resolve_ads_page(self, browser):
            return response_page

    provider = _Provider()

    with pytest.raises(
        ScannerScopeUnavailableError,
        match="получено меньше ожидаемых 51 строк после повторных обновлений страницы \\(попыток: 3\\)",
    ):
        await provider._scan_with_browser(
            browser=SimpleNamespace(contexts=[]),
            profile_id="profile-1",
            browser_host_name="vision-3030",
        )

    latest_reports = list(tmp_path.glob("*__latest.json"))
    assert len(latest_reports) == 1
    report_payload = json.loads(latest_reports[0].read_text(encoding="utf-8"))
    assert report_payload["captured_response_count"] == 2
    assert report_payload["responses"][0]["is_relevant"] is False
    assert report_payload["responses"][0]["ad_object_total"] == 2
    assert report_payload["responses"][1]["is_relevant"] is True
    assert report_payload["responses"][1]["row_list_total"] == 42


# Проверяет, что scanner всегда освобождает временную browser session, даже если в браузере нет страницы Ads Manager.
@pytest.mark.asyncio
async def test_scanner_releases_session_when_ads_page_missing() -> None:
    attached_session = AttachedBrowserSession(
        profile_id="profile-1",
        cdp_url="http://127.0.0.1:54000",
        webdriver_url=None,
        is_attached=True,
        browser=SimpleNamespace(contexts=[]),
    )

    class _FakeSessionManager:
        def __init__(self) -> None:
            self.released_profiles: list[str] = []

        async def ensure_session(self, profile_id: str) -> AttachedBrowserSession:
            return attached_session

        async def release_session(self, session: AttachedBrowserSession) -> None:
            self.released_profiles.append(session.profile_id)

    fake_manager = _FakeSessionManager()
    provider = FacebookAdsScannerProvider(
        settings=Settings(),
        browser_session_manager=fake_manager,
    )

    with pytest.raises(RuntimeError, match="нет открытых страниц"):
        await provider.scan_rows(profile_id="profile-1", browser_host_name="host-1")

    assert fake_manager.released_profiles == ["profile-1"]
