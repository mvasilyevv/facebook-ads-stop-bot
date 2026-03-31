# -*- coding: utf-8 -*-
"""Тесты humanizer: скролл должен попадать в реальную область таблицы Ads Manager."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class FakeTableMouse:
    """Фейковая мышь, которая раскрывает строку только при скролле над таблицей."""

    def __init__(self, page) -> None:
        self._page = page
        self.last_position = (0.0, 0.0)

    async def move(self, x: float, y: float) -> None:
        self.last_position = (x, y)

    async def wheel(self, _delta_x: int, _delta_y: int) -> None:
        x, y = self.last_position
        if 100 <= x <= 300 and 100 <= y <= 300:
            self._page.revealed = True


class FakeTablePage:
    """Фейковая страница, где элемент появляется только после скролла над таблицей."""

    def __init__(self) -> None:
        self.viewport_size = {"width": 1000, "height": 800}
        self.revealed = False
        self.element = object()
        self.mouse = FakeTableMouse(self)

    async def query_selector(self, _selector: str):
        return self.element if self.revealed else None

    async def evaluate(self, _script: str):
        return {"x": 200, "y": 200}


# Тест: human_scroll_to_find должен крутить колесо над самой таблицей, а не по центру окна.
@pytest.mark.asyncio
async def test_human_scroll_to_find_uses_ads_table_anchor():
    """Если таблица имеет свой контейнер, скролл должен привязываться именно к нему."""
    from core.browser.humanizer import human_scroll_to_find

    page = FakeTablePage()

    with patch("core.browser.humanizer.asyncio.sleep", new=AsyncMock()):
        element = await human_scroll_to_find(
            page,
            '[data-surface*="table_row:120241979860890176"]',
            max_steps=2,
            step_px=220,
        )

    assert element is page.element
    assert 100 <= page.mouse.last_position[0] <= 300
    assert 100 <= page.mouse.last_position[1] <= 300
