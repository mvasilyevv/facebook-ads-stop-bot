# -*- coding: utf-8 -*-
"""Шаг: выбрать 'Место получения конверсий' → 'Одно' → 'Сайт'.

Флоу из записи 2026-05-13: комбобокс показывает текущее значение
(«Сайт и звонки») — клик по нему открывает панель выбора. Далее FB
спрашивает, сколько мест назначения («Одно»/«Несколько»), и только
потом — какое именно («Сайт»).
"""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class SetConversionLocationStep(BaseStep):
    """Открыть комбобокс 'Место получения конверсий' и выбрать 'Одно' → 'Сайт'."""

    name = "set_conversion_location"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            if await self._already_website(page):
                logger.info("Место получения конверсий уже = Сайт, шаг пропущен")
                return StepResult(success=True, message="Место получения конверсий уже = Сайт")
            await self._open_combobox(page)
            await human_wait(300, 600)
            await self._pick_single_location(page)
            await human_wait(200, 400)
            await self._pick_website(page)
            await human_wait(200, 400)
            logger.info("Место получения конверсий = Одно → Сайт")
            return StepResult(success=True, message="Место получения конверсий: Одно → Сайт")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_conversion_location: {exc}")

    async def _already_website(self, page: Page) -> bool:
        # Текущее значение отображается внутри role=combobox (с zero-width space).
        # Если уже «Сайт» (не «Сайт и звонки») — повторно ничего делать не нужно.
        try:
            combo = page.get_by_role("combobox").filter(has_text="Сайт").first
            if not await combo.is_visible(timeout=1500):
                return False
            text = (await combo.inner_text()).replace("​", "").strip()
            return text == "Сайт"
        except Exception:
            return False

    async def _open_combobox(self, page: Page) -> None:
        # Комбобокс открывается кликом по его текущему значению («Сайт и звонки»).
        combo = page.get_by_role("combobox").filter(has_text="Сайт и звонки").first
        await combo.wait_for(state="visible", timeout=8000)
        await combo.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await combo.hover()
        await human_wait(50, 120)
        await combo.click()

    async def _pick_single_location(self, page: Page) -> None:
        # «Одно» — карточка с заголовком в открытой панели.
        heading = page.get_by_role("heading", name="Одно", exact=True).first
        await heading.wait_for(state="visible", timeout=8000)
        await heading.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await heading.hover()
        await human_wait(50, 120)
        await heading.click()

    async def _pick_website(self, page: Page) -> None:
        # «Сайт» — gridcell в карточке выбора канала конверсий (после выбора «Одно»).
        cell = page.get_by_role("gridcell", name="Сайт", exact=True).first
        await cell.wait_for(state="visible", timeout=8000)
        await cell.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await cell.hover()
        await human_wait(50, 120)
        await cell.click()
