# -*- coding: utf-8 -*-
"""Шаг: окно атрибуции (1 день / 7 дней по клику).

В свежем UI блок «Настройки атрибуции» спрятан под ссылкой
«Показать больше настроек». Открываем секцию (если ещё свёрнута),
кликаем на сам блок «Настройки атрибуции» (он раскрывает редактор),
затем меняем combobox «Переход по клику» → option «1 день» / «7 дней».
"""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class SetAttributionStep(BaseStep):
    """Выбрать окно атрибуции по клику: 1 день или 7 дней."""

    name = "set_attribution"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            days = (params or {}).get("days", context.attribution_days)
            await self._expand_more_settings(page)
            await human_wait(300, 600)
            await self._open_attribution_block(page)
            await human_wait(300, 600)
            option_label = "7 дней" if int(days) == 7 else "1 день"
            await self._pick_click_window(page, option_label)
            logger.info("Атрибуция: %s по клику", option_label)
            return StepResult(success=True, message=f"Атрибуция: {option_label} по клику")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_attribution: {exc}")

    async def _expand_more_settings(self, page: Page) -> None:
        """Кликнуть «Показать больше настроек», если ссылка ещё видна."""
        link = page.get_by_role("link", name="Показать больше настроек").first
        try:
            if await link.count() and await link.is_visible():
                await link.scroll_into_view_if_needed()
                await human_wait(80, 180)
                await link.hover()
                await human_wait(50, 120)
                await link.click()
        except Exception as exc:
            logger.debug("Раскрытие 'Показать больше настроек' пропущено: %s", exc)

    async def _open_attribution_block(self, page: Page) -> None:
        """Раскрыть редактор «Настройки атрибуции», если combobox ещё не виден."""
        combo = page.get_by_role("combobox", name="Переход по клику").first
        if await combo.count() and await combo.is_visible():
            return
        # Кликаем по тексту-метке секции; она же — заголовок свёрнутого блока.
        block = page.get_by_text("Настройки атрибуции", exact=True).first
        await block.wait_for(state="visible", timeout=8000)
        await block.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await block.hover()
        await human_wait(50, 120)
        await block.click()

    async def _pick_click_window(self, page: Page, option_label: str) -> None:
        combo = page.get_by_role("combobox", name="Переход по клику").first
        await combo.wait_for(state="visible", timeout=8000)
        await combo.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await combo.hover()
        await human_wait(50, 120)
        await combo.click()
        await human_wait(300, 600)
        option = page.get_by_role("option", name=option_label).first
        await option.wait_for(state="visible", timeout=8000)
        await option.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await option.hover()
        await human_wait(50, 120)
        await option.click()
