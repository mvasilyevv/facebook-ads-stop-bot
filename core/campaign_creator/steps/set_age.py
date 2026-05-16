# -*- coding: utf-8 -*-
"""Шаг: возрастной диапазон аудитории — фиксированный 20–55 для всех офферов.

UI: два combobox в блоке «Возраст»: «Минимальный возраст» и
«Максимальный возраст». В каждом — список options с числами; выбираем
'20' и '55' соответственно.
"""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

MIN_AGE = 20
MAX_AGE = 55


class SetAgeStep(BaseStep):
    """Установить мин/макс возраст аудитории (20/55)."""

    name = "set_age"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            min_age = int(p.get("min_age", MIN_AGE))
            max_age = int(p.get("max_age", MAX_AGE))
            await self._open_age_block(page)
            await human_wait(300, 600)
            await self._pick_age(page, "Минимальный возраст", min_age)
            await human_wait(300, 600)
            await self._pick_age(page, "Максимальный возраст", max_age)
            logger.info("Возраст: %s–%s", min_age, max_age)
            return StepResult(success=True, message=f"Возраст: {min_age}–{max_age}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_age: {exc}")

    async def _open_age_block(self, page: Page) -> None:
        """Раскрыть блок «Возраст», если comboboxes ещё не появились.

        В свежем UI сначала видна строка «Возраст 18 - 65+», по клику
        раскрывается редактор с двумя comboboxes.
        """
        combo = page.get_by_role("combobox", name="Минимальный возраст").first
        if await combo.count() and await combo.is_visible():
            return
        # Заголовок-кнопка строки возраста
        block = page.get_by_text("Возраст", exact=True).first
        await block.wait_for(state="visible", timeout=8000)
        await block.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await block.hover()
        await human_wait(50, 120)
        await block.click()

    async def _pick_age(self, page: Page, combo_name: str, value: int) -> None:
        combo = page.get_by_role("combobox", name=combo_name).first
        await combo.wait_for(state="visible", timeout=8000)
        await combo.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await combo.hover()
        await human_wait(50, 120)
        await combo.click()
        await human_wait(300, 600)
        option = page.get_by_role("option", name=str(value), exact=True).first
        await option.wait_for(state="visible", timeout=8000)
        await option.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await option.hover()
        await human_wait(50, 120)
        await option.click()
