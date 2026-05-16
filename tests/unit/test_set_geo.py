# -*- coding: utf-8 -*-
"""Тесты SetGeoStep — проверяем, что шаг использует humanizer, а не прямые локаторы."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.campaign_creator.steps.base import StepContext
from core.campaign_creator.steps.set_geo import (
    SEARCH_INPUT_SELECTOR,
    SECTION_LABEL,
    SetGeoStep,
)


def _ctx() -> StepContext:
    return StepContext(
        offer_code="X",
        cabinet_id="c1",
        campaign_name="N",
        pixel_id="p1",
        landing_url="https://example.com",
        geo_code="KE",
        geo_slot_name="Кения",
        daily_budget=10.0,
        attribution_days=7,
        budget_level="CBO",
        iter_num=1,
        adsets=[],
        creo_folder="/tmp",
    )


def _visible_locator(count: int = 1, visible: bool = True) -> MagicMock:
    loc = MagicMock()
    loc.first = loc
    loc.count = AsyncMock(return_value=count)
    loc.is_visible = AsyncMock(return_value=visible)
    loc.wait_for = AsyncMock()
    loc.scroll_into_view_if_needed = AsyncMock()
    loc.click = AsyncMock()
    loc.hover = AsyncMock()
    return loc


# Если в drawer уже видно поле поиска — _ensure_search_visible не должен ни
# скроллить колесом, ни звать humanizer для раскрытия секции.
@pytest.mark.asyncio
async def test_ensure_search_visible_when_already_visible():
    step = SetGeoStep()
    search_loc = _visible_locator(count=1, visible=True)
    page = MagicMock()
    page.locator = MagicMock(return_value=search_loc)
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()

    with patch(
        "core.campaign_creator.steps.set_geo.human_click_label",
        new=AsyncMock(),
    ) as click_label:
        await step._ensure_search_visible(page)
        click_label.assert_not_awaited()

    page.locator.assert_called_with(SEARCH_INPUT_SELECTOR)
    page.mouse.wheel.assert_not_awaited()


# Если поля поиска ещё нет — раскрываем секцию через human_click_label, не
# через ручной mouse.wheel.
@pytest.mark.asyncio
async def test_ensure_search_visible_opens_section_via_humanizer():
    step = SetGeoStep()
    # сначала count=0 (нет в DOM), потом после клика wait_for должен пройти
    search_loc = MagicMock()
    search_loc.first = search_loc
    search_loc.count = AsyncMock(return_value=0)
    search_loc.is_visible = AsyncMock(return_value=False)
    search_loc.wait_for = AsyncMock()

    page = MagicMock()
    page.locator = MagicMock(return_value=search_loc)
    page.mouse = MagicMock()
    page.mouse.wheel = AsyncMock()

    with patch(
        "core.campaign_creator.steps.set_geo.human_click_label",
        new=AsyncMock(),
    ) as click_label:
        await step._ensure_search_visible(page)

    click_label.assert_awaited_once()
    args, kwargs = click_label.call_args
    assert args[1] == SECTION_LABEL
    page.mouse.wheel.assert_not_awaited()
    search_loc.wait_for.assert_awaited()


# _add_country должен выбрать опцию через human_pick_option, а не через
# хрупкий [role="option"] локатор.
@pytest.mark.asyncio
async def test_add_country_uses_human_pick_option():
    step = SetGeoStep()
    search_loc = _visible_locator()
    page = MagicMock()
    page.locator = MagicMock(return_value=search_loc)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.type = AsyncMock()

    with patch(
        "core.campaign_creator.steps.set_geo.human_pick_option",
        new=AsyncMock(),
    ) as pick:
        await step._add_country(page, "Кения", "Кения")

    pick.assert_awaited_once()
    args, _ = pick.call_args
    assert args[1] == "Кения"
    page.keyboard.type.assert_awaited()


# Удаление чипа теперь идёт через page.evaluate (у кнопки нет aria-label —
# только .accessible_elem с текстом «Закрыть»). Проверяем, что evaluate
# вызвался с именем чипа и вернул True.
@pytest.mark.asyncio
async def test_remove_chip_uses_evaluate_with_name():
    step = SetGeoStep()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)

    await step._remove_chip(page, "Китай")

    page.evaluate.assert_awaited_once()
    args = page.evaluate.call_args.args
    assert args[1] == "Китай"


# Если evaluate вернул False — шаг не падает, просто логирует warning.
@pytest.mark.asyncio
async def test_remove_chip_skips_when_missing():
    step = SetGeoStep()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=False)

    await step._remove_chip(page, "Несуществующая")


# Чтение текущих чипов теперь идёт через JS-обход span._3bss с проверкой
# наличия кнопки «Закрыть» в том же <li>, а не через aria-label.
@pytest.mark.asyncio
async def test_read_current_chips_uses_close_button_query():
    step = SetGeoStep()
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=["Китай", "Антарктика"])

    chips = await step._read_current_chips(page)
    assert chips == ["Китай", "Антарктика"]

    js = page.evaluate.call_args.args[0]
    assert "span._3bss" in js
    assert "Закрыть" in js


# execute() с decларативными params: если desired уже совпадает с чипами после
# раскрытия секции — ни add_country, ни remove_chip не вызываются.
@pytest.mark.asyncio
async def test_execute_declarative_skips_when_already_matches():
    step = SetGeoStep()
    page = MagicMock()
    # _ensure_search_visible видит input сразу — секцию раскрывать не нужно.
    search = MagicMock()
    search.count = AsyncMock(return_value=1)
    search.is_visible = AsyncMock(return_value=True)
    search.wait_for = AsyncMock()
    page.locator = MagicMock(return_value=MagicMock(first=search))
    # Чипы уже в нужном составе.
    page.evaluate = AsyncMock(return_value=["Антарктика", "Кения"])
    step._add_country = AsyncMock()
    step._remove_chip = AsyncMock()

    result = await step.execute(page, _ctx(), {"countries": ["Кения"]})
    assert result.success
    step._add_country.assert_not_awaited()
    step._remove_chip.assert_not_awaited()
