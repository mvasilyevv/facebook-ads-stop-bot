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

        UI: секция возраста спрятана под link «Показать настройки» в блоке
        аудитории. После раскрытия видна строка «Возраст 18 - 65+» (или с
        ранее выставленным диапазоном) — клик по ней показывает два combobox.
        """
        combo = page.get_by_role("combobox", name="Минимальный возраст").first
        if await combo.count() and await combo.is_visible():
            return

        # Шаг 1: «Показать настройки», если ссылка ещё видна.
        link = page.get_by_role("link", name="Показать настройки").first
        try:
            if await link.count() and await link.is_visible():
                await link.scroll_into_view_if_needed()
                await human_wait(80, 180)
                await link.hover()
                await human_wait(50, 120)
                await link.click()
                await human_wait(400, 700)
        except Exception as exc:
            logger.debug("Раскрытие 'Показать настройки' пропущено: %s", exc)

        if await combo.count() and await combo.is_visible():
            return

        # Шаг 2: клик по строке «Возраст 18 - 65+» (или другой диапазон).
        # Стабильных селекторов у неё нет — ищем контейнер, который содержит
        # И «Возраст», И знак диапазона (« - »). Это однозначно строка-аккордеон.
        clicked = await self._click_age_row(page)
        if not clicked:
            raise RuntimeError(
                "Не удалось раскрыть блок «Возраст» — строка с диапазоном не найдена"
            )
        await human_wait(300, 600)

    async def _click_age_row(self, page: Page) -> bool:
        """Найти и кликнуть аккордеон «Возраст <диапазон>».

        Стратегия: ищем самый компактный элемент, текст которого начинается
        со слова «Возраст» и содержит дефис диапазона. У FB это div с tabindex
        внутри блока аудитории.
        """
        try:
            handle = await page.evaluate_handle(
                """
                () => {
                    const all = Array.from(document.querySelectorAll('div, span'));
                    const re = /^\\s*Возраст[\\s\\S]{0,30}\\d+\\s*[-–—]\\s*\\d+/;
                    // Кандидаты: содержат «Возраст» + диапазон, и сам текстовый контент короткий.
                    const cands = all.filter(el => {
                        const t = (el.innerText || '').trim();
                        if (!t || t.length > 80) return false;
                        return re.test(t);
                    });
                    if (!cands.length) return null;
                    // Берём самый «глубокий» (минимальная высота поддерева) — это сама строка, а не контейнер.
                    cands.sort((a, b) => a.getElementsByTagName('*').length - b.getElementsByTagName('*').length);
                    return cands[0];
                }
                """
            )
            element = handle.as_element() if handle else None
            if not element:
                return False
            await element.scroll_into_view_if_needed()
            await human_wait(80, 180)
            await element.hover()
            await human_wait(50, 120)
            await element.click()
            return True
        except Exception as exc:
            logger.debug("Клик по строке «Возраст …» не удался: %s", exc)
            return False

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
