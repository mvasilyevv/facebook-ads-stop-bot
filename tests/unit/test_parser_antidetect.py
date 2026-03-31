# -*- coding: utf-8 -*-
"""Тесты антидетект-улучшений парсера: mouse.move, mouse.wheel, единый evaluate."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from core.scanner.models import ScannedAdRow
from core.scanner.parser import (
    _build_extraction_js,
    _match_field_name,
    _parse_bulk_result,
    refresh_table,
)


# --- Тест 1: _build_extraction_js возвращает валидный JS-код ---
def test_build_extraction_js_returns_valid_js():
    """Проверяем, что генерируемый JS-код синтаксически корректен."""
    js = _build_extraction_js()
    # Должен быть непустой строкой
    assert isinstance(js, str)
    assert len(js) > 100
    # Должен содержать ключевые конструкции
    assert "FIELD_ALIASES" in js
    assert "NUMERIC_FIELDS" in js
    assert "table_row:" in js
    assert "getAdName" in js
    assert "getMetricText" in js
    assert "getFirstText" in js
    assert "matchFieldName" in js
    # Должен начинаться как IIFE/функция
    assert js.strip().startswith("() =>")
    # Должен содержать все ключи маппинга полей
    assert "campaign_group_name" in js
    assert "budget" in js
    assert "reach" in js
    assert "impressions" in js
    assert "cost_per_result" in js
    assert "spend" in js
    assert "actions:lead" in js
    assert "actions:omni_complete_registration" in js
    assert "omni_complete_registration" in js
    assert "table_cell:results" in js


# --- Тест 1.1: более специфичные data-surface должны побеждать общие ---
def test_match_field_name_prefers_specific_registration_cost_alias():
    """Проверяем, что цена регистрации не путается с количеством регистраций."""
    assert (
        _match_field_name("table_row:120:cost_per_action_type:omni_complete_registration")
        == "cost_per_registration"
    )
    assert _match_field_name("table_row:120:actions:omni_complete_registration") == "registrations"


# --- Тест 1.2: campaign/adset не должны путаться с общим алиасом `name` ---
def test_match_field_name_prefers_campaign_and_adset_over_generic_name():
    """Проверяем, что колонки кампании и адсета не съедаются полем ad_name."""
    assert (
        _match_field_name("table_row:120:forObjectType(campaign_group_name,ADGROUP)")
        == "campaign_name"
    )
    assert _match_field_name("table_row:120:forObjectType(campaign_name,ADGROUP)") == "adset_name"
    assert _match_field_name("table_row:120:forObjectType(name,ADGROUP)") == "ad_name"


# --- Тест 2: _parse_bulk_result корректно создаёт ScannedAdRow ---
def test_parse_bulk_result_creates_scanned_ad_rows():
    """Проверяем, что результат единого evaluate преобразуется в ScannedAdRow."""
    raw_rows = [
        {
            "_row_id": "120241979860890176",
            "campaign_name": "Test Campaign",
            "adset_name": "Test Adset",
            "ad_name": "Test Ad Name",
            "delivery_status": "Active",
            "spend": "$10.50",
            "budget": "$20.00 Daily",
            "reach": "123",
            "impressions": "456",
            "clicks": "42",
            "cpc": "$0.25",
            "ctr": "9.21%",
            "leads": "5",
            "cost_per_lead": "$2.10",
            "registrations": "2",
            "cost_per_registration": "$5.25",
            "cost_per_result": "$10.50",
            "deposits": "1",
        },
    ]
    rows = _parse_bulk_result(raw_rows)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, ScannedAdRow)
    assert row.fb_ad_id == "120241979860890176"
    assert row.campaign_name == "Test Campaign"
    assert row.adset_name == "Test Adset"
    assert row.ad_name == "Test Ad Name"
    assert row.delivery_status == "ACTIVE"
    assert row.spend == Decimal("10.50")
    assert row.budget == "$20.00 Daily"
    assert row.reach == 123
    assert row.impressions == 456
    assert row.clicks == 42
    assert row.cpc == Decimal("0.25")
    assert row.ctr == Decimal("9.21")
    assert row.leads == 5
    assert row.cost_per_lead == Decimal("2.10")
    assert row.registrations == 2
    assert row.cost_per_registration == Decimal("5.25")
    assert row.cost_per_result == Decimal("10.50")
    assert row.deposits == 1


# --- Тест 3: пустые/невалидные строки в bulk-результате пропускаются ---
def test_parse_bulk_result_skips_invalid_rows():
    """Проверяем, что строки без ad_name или fb_ad_id не попадают в результат."""
    raw_rows = [
        # Строка без ad_name — должна быть пропущена
        {
            "_row_id": "11111",
            "campaign_name": "Camp",
            "ad_name": "\u2014",
        },
        # Строка без _row_id и без ID в тексте — должна быть пропущена
        {
            "campaign_name": "Camp",
            "ad_name": "Some Ad",
        },
        # Валидная строка
        {
            "_row_id": "120241979860770176",
            "ad_name": "Good Ad",
            "delivery_status": "Active",
            "spend": "$1.00",
        },
    ]
    rows = _parse_bulk_result(raw_rows)
    assert len(rows) == 1
    assert rows[0].fb_ad_id == "120241979860770176"


# --- Тест 4: refresh_table использует humanizer для клика ---
@pytest.mark.asyncio
async def test_refresh_table_uses_human_click():
    """Проверяем, что refresh-клик выполняется через humanized-слой."""
    btn = AsyncMock()
    btn.inner_text = AsyncMock(return_value="Обновить")
    btn.click = AsyncMock()

    container = AsyncMock()
    container.query_selector_all = AsyncMock(return_value=[btn])

    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=container)
    page.mouse = AsyncMock()

    with patch("core.scanner.parser.human_click", new=AsyncMock()) as human_click_mock:
        result = await refresh_table(page)

    assert result is True
    human_click_mock.assert_awaited_once_with(page, btn)
    page.mouse.move.assert_not_called()
    page.mouse.click.assert_not_called()
    btn.click.assert_not_called()


# --- Тест 5: mouse.wheel используется для фактического скролла ---
@pytest.mark.asyncio
async def test_scroll_and_parse_uses_mouse_wheel():
    """Проверяем, что фактический скролл выполняется через humanized-слой."""
    from apps.observer_worker.main import _scroll_and_parse

    # Мок parse_fn: первый вызов возвращает строку, второй — ту же
    row = ScannedAdRow(
        fb_ad_id="12345",
        campaign_name="C",
        adset_name="A",
        ad_name="Ad",
        delivery_status="ACTIVE",
        spend=Decimal("1"),
    )
    call_count = 0

    async def mock_parse_fn(p):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return [row]
        return [row]  # Те же строки — скролл завершится

    page = AsyncMock()
    page.mouse = AsyncMock()
    page.mouse.wheel = AsyncMock()

    with (
        patch("apps.observer_worker.main.human_move", new=AsyncMock()),
        patch(
            "apps.observer_worker.main.human_wheel_scroll",
            new=AsyncMock(),
        ) as human_wheel_scroll_mock,
        patch(
            "apps.observer_worker.main._get_ads_table_scroll_metrics",
            new=AsyncMock(
                side_effect=[
                    {"found": True, "scroll_top": 0, "max_scroll_top": 300, "at_bottom": False},
                    {"found": True, "scroll_top": 120, "max_scroll_top": 300, "at_bottom": False},
                    {"found": True, "scroll_top": 120, "max_scroll_top": 300, "at_bottom": False},
                    {"found": True, "scroll_top": 120, "max_scroll_top": 300, "at_bottom": False},
                ]
            ),
        ),
    ):
        result = await _scroll_and_parse(page, mock_parse_fn)

    # Скролл должен идти через humanized-слой, а не напрямую из observer
    assert human_wheel_scroll_mock.await_count >= 1
    # Результат содержит нашу строку
    assert len(result) == 1
    assert result[0].fb_ad_id == "12345"


# --- Тест 5.1: если новых ID нет, но таблица движется вниз, скролл продолжается ---
@pytest.mark.asyncio
async def test_scroll_and_parse_continues_while_table_still_moves():
    """Overlap без новых ID не должен останавливать скролл, если таблица ещё едет вниз."""
    from apps.observer_worker.main import _scroll_and_parse

    row_top = ScannedAdRow(
        fb_ad_id="11111",
        campaign_name="C",
        adset_name="A",
        ad_name="Top",
        delivery_status="ACTIVE",
        spend=Decimal("1"),
    )
    row_middle = ScannedAdRow(
        fb_ad_id="22222",
        campaign_name="C",
        adset_name="A",
        ad_name="Middle",
        delivery_status="ACTIVE",
        spend=Decimal("2"),
    )

    pages = [
        [row_top],
        [row_top],
        [row_top, row_middle],
        [row_top, row_middle],
        [row_top, row_middle],
    ]

    async def mock_parse_fn(_page):
        return pages.pop(0) if pages else [row_top, row_middle]

    page = AsyncMock()
    page.mouse = AsyncMock()
    page.mouse.wheel = AsyncMock()

    with (
        patch("apps.observer_worker.main.human_move", new=AsyncMock()),
        patch(
            "apps.observer_worker.main.human_wheel_scroll",
            new=AsyncMock(),
        ) as human_wheel_scroll_mock,
        patch(
            "apps.observer_worker.main._get_ads_table_scroll_metrics",
            new=AsyncMock(
                side_effect=[
                    {"found": True, "scroll_top": 0, "max_scroll_top": 300, "at_bottom": False},
                    {"found": True, "scroll_top": 100, "max_scroll_top": 300, "at_bottom": False},
                    {"found": True, "scroll_top": 180, "max_scroll_top": 300, "at_bottom": False},
                    {"found": True, "scroll_top": 180, "max_scroll_top": 300, "at_bottom": False},
                    {"found": True, "scroll_top": 180, "max_scroll_top": 300, "at_bottom": False},
                    {"found": True, "scroll_top": 180, "max_scroll_top": 300, "at_bottom": False},
                ]
            ),
        ),
    ):
        result = await _scroll_and_parse(page, mock_parse_fn)

    assert sorted(row.fb_ad_id for row in result) == ["11111", "22222"]
    assert human_wheel_scroll_mock.await_count >= 3


# --- Тест 6: refresh_table корректно работает без bounding_box ---
@pytest.mark.asyncio
async def test_refresh_table_works_without_bounding_box():
    """Проверяем, что если bounding_box вернул None, клик всё равно происходит."""
    btn = AsyncMock()
    btn.inner_text = AsyncMock(return_value="Refresh")
    btn.bounding_box = AsyncMock(return_value=None)
    btn.click = AsyncMock()

    container = AsyncMock()
    container.query_selector_all = AsyncMock(return_value=[btn])

    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=container)
    page.mouse = AsyncMock()
    page.viewport_size = {"width": 1200, "height": 800}

    with patch("core.browser.humanizer.asyncio.sleep", new=AsyncMock()):
        result = await refresh_table(page)

    assert result is True
    btn.click.assert_called_once()
    # mouse.move НЕ должен быть вызван, т.к. bounding_box = None
    page.mouse.move.assert_not_called()
