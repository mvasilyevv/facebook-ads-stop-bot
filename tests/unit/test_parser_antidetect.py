# -*- coding: utf-8 -*-
"""Тесты антидетект-улучшений парсера: mouse.move, mouse.wheel, единый evaluate."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from core.scanner.models import ScannedAdRow
from core.scanner.parser import (
    _build_extraction_js,
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
    assert "FIELD_KEYS" in js
    assert "NUMERIC_FIELDS" in js
    assert "table_row:" in js
    assert "getAdName" in js
    assert "getMetricText" in js
    assert "getFirstText" in js
    # Должен начинаться как IIFE/функция
    assert js.strip().startswith("() =>")
    # Должен содержать все ключи маппинга полей
    assert "campaign_group_name" in js
    assert "spend" in js
    assert "actions:lead" in js
    assert "omni_complete_registration" in js


# --- Тест 2: _parse_bulk_result корректно создаёт ScannedAdRow ---
def test_parse_bulk_result_creates_scanned_ad_rows():
    """Проверяем, что результат единого evaluate преобразуется в ScannedAdRow."""
    raw_rows = [
        {
            "_row_id": "12345678",
            "campaign_name": "Test Campaign",
            "adset_name": "Test Adset",
            "ad_name": "Test Ad Name",
            "delivery_status": "Active",
            "spend": "$10.50",
            "clicks": "42",
            "cpc": "$0.25",
            "leads": "5",
            "cost_per_lead": "$2.10",
            "registrations": "2",
            "cost_per_registration": "$5.25",
        },
    ]
    rows = _parse_bulk_result(raw_rows)
    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, ScannedAdRow)
    assert row.fb_ad_id == "12345678"
    assert row.campaign_name == "Test Campaign"
    assert row.adset_name == "Test Adset"
    assert row.ad_name == "Test Ad Name"
    assert row.delivery_status == "ACTIVE"
    assert row.spend == Decimal("10.50")
    assert row.clicks == 42
    assert row.cpc == Decimal("0.25")
    assert row.leads == 5
    assert row.cost_per_lead == Decimal("2.10")
    assert row.registrations == 2
    assert row.cost_per_registration == Decimal("5.25")
    assert row.deposits == 0


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
            "_row_id": "99999",
            "ad_name": "Good Ad",
            "delivery_status": "Active",
            "spend": "$1.00",
        },
    ]
    rows = _parse_bulk_result(raw_rows)
    assert len(rows) == 1
    assert rows[0].fb_ad_id == "99999"


# --- Тест 4: mouse.move вызывается перед click в refresh_table ---
@pytest.mark.asyncio
async def test_refresh_table_mouse_move_before_click():
    """Проверяем, что мышь двигается к кнопке перед кликом (антидетект)."""
    # Порядок вызовов для проверки
    call_order = []

    # Мок кнопки с bounding_box
    btn = AsyncMock()
    btn.inner_text = AsyncMock(return_value="Обновить")
    btn.bounding_box = AsyncMock(return_value={
        "x": 100, "y": 200, "width": 80, "height": 30,
    })
    btn.click = AsyncMock(
        side_effect=lambda: call_order.append("click")
    )

    # Мок контейнера
    container = AsyncMock()
    container.query_selector_all = AsyncMock(return_value=[btn])

    # Мок страницы
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=container)

    # Отслеживаем порядок вызовов mouse.move
    async def mock_mouse_move(x, y):
        call_order.append("mouse_move")

    page.mouse = AsyncMock()
    page.mouse.move = AsyncMock(side_effect=mock_mouse_move)

    # Патчим asyncio.sleep чтобы тест не ждал
    with patch("core.scanner.parser.asyncio.sleep", new_callable=AsyncMock):
        result = await refresh_table(page)

    assert result is True
    # mouse.move должен быть вызван ДО click
    assert "mouse_move" in call_order
    assert "click" in call_order
    assert call_order.index("mouse_move") < call_order.index("click")
    # mouse.move должен быть вызван с координатами около центра кнопки
    move_call = page.mouse.move.call_args
    x, y = move_call[0]
    # Центр кнопки: x=140, y=215, допуск ±5px
    assert 135 <= x <= 145
    assert 212 <= y <= 218


# --- Тест 5: mouse.wheel используется вместо evaluate для скролла ---
@pytest.mark.asyncio
async def test_scroll_and_parse_uses_mouse_wheel():
    """Проверяем, что скролл выполняется через mouse.wheel, а не evaluate."""
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

    with patch(
        "apps.observer_worker.main.asyncio.sleep",
        new_callable=AsyncMock,
    ):
        result = await _scroll_and_parse(page, mock_parse_fn)

    # mouse.wheel должен быть вызван хотя бы раз
    assert page.mouse.wheel.called
    # evaluate НЕ должен быть вызван для скролла
    page.evaluate.assert_not_called()
    # Результат содержит нашу строку
    assert len(result) == 1
    assert result[0].fb_ad_id == "12345"


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

    result = await refresh_table(page)

    assert result is True
    btn.click.assert_called_once()
    # mouse.move НЕ должен быть вызван, т.к. bounding_box = None
    page.mouse.move.assert_not_called()
