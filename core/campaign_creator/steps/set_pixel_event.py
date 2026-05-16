# -*- coding: utf-8 -*-
"""Шаг: выбрать пиксель по ID и событие 'Покупка'.

По записи 2026-05-16 FB ведёт себя так:
- combobox 'Пиксель' раскрывается за **два клика** — сначала по самому
  combobox'у, потом по полю input внутри, и только после этого можно
  печатать pixel_id и выбирать option role=option[name="<имя пикселя>"].
- combobox 'Событие' раскрывается **одним кликом**, без печати —
  option role=option[name="Покупка"] видна сразу.
"""

from __future__ import annotations

import logging

from playwright.async_api import Locator, Page

from core.campaign_creator.humanizer import human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

EVENT = "Покупка"


class SetPixelEventStep(BaseStep):
    """Выбрать пиксель по pixel_id и событие 'Покупка'."""

    name = "set_pixel_event"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            pixel_id = (params or {}).get("pixel_id", context.pixel_id)
            await self._select_pixel(page, str(pixel_id))
            await human_wait(400, 800)
            await self._select_purchase_event(page)
            await human_wait(200, 400)
            logger.info("Pixel %s выбран, событие %s", pixel_id, EVENT)
            return StepResult(success=True, message=f"Pixel {pixel_id}, событие {EVENT}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_pixel_event: {exc}")

    async def _combo(self, page: Page, name: str) -> Locator:
        combo = page.get_by_role("combobox", name=name).first
        await combo.wait_for(state="visible", timeout=8000)
        await combo.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await combo.hover()
        await human_wait(50, 120)
        return combo

    async def _select_pixel(self, page: Page, pixel_id: str) -> None:
        combo = await self._combo(page, "Пиксель")
        # Первый клик — раскрывает combobox.
        await combo.click()
        await human_wait(500, 800)

        # Если в раскрытом dropdown сразу видна нужная опция — кликаем её,
        # печатать не нужно (мало пикселей в аккаунте).
        option_by_id = page.locator(f'[role="option"][id="{pixel_id}"]').first
        try:
            await option_by_id.wait_for(state="visible", timeout=1500)
            await option_by_id.scroll_into_view_if_needed()
            await human_wait(80, 180)
            await option_by_id.hover()
            await human_wait(50, 120)
            await option_by_id.click()
            return
        except Exception:
            pass

        # Иначе раскрываем input внутри и печатаем pixel_id для фильтрации.
        input_inside = combo.locator("input").first
        try:
            await input_inside.wait_for(state="visible", timeout=3000)
            await input_inside.click()
            await human_wait(120, 250)
        except Exception:
            await combo.click()
            await human_wait(120, 250)

        await page.keyboard.press("Control+A")
        await human_wait(50, 120)
        await page.keyboard.press("Delete")
        await human_wait(80, 180)
        await page.keyboard.type(pixel_id, delay=40)
        await human_wait(900, 1400)

        # Снова пробуем точное совпадение по DOM id.
        option = page.locator(f'[role="option"][id="{pixel_id}"]').first
        try:
            await option.wait_for(state="visible", timeout=4000)
        except Exception:
            # Fallback: option с pixel_id в тексте (Dataset ID: ...).
            option = page.locator(f'[role="option"]:has-text("{pixel_id}")').first
            await option.wait_for(state="visible", timeout=4000)

        await option.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await option.hover()
        await human_wait(50, 120)
        await option.click()

    async def _select_purchase_event(self, page: Page) -> None:
        # Если событие уже выбрано как Покупка — пропускаем шаг.
        try:
            already = page.get_by_role("combobox", name="Событие").filter(has_text="Покупка").first
            if await already.count() and await already.is_visible():
                logger.info("Событие уже 'Покупка', пропускаю")
                return
        except Exception:
            pass

        combo = await self._combo(page, "Событие")
        await combo.click()
        await human_wait(300, 600)

        # Сначала по точному имени.
        option = page.get_by_role("option", name=EVENT, exact=True).first
        try:
            await option.wait_for(state="visible", timeout=4000)
        except Exception:
            # Fallback: option с подстрокой Покупка или PURCHASE в id.
            option = page.locator(
                '[role="option"]:has-text("Покупка"), '
                '[role="option"][id*="\\"eventName\\":\\"PURCHASE\\""]'
            ).first
            await option.wait_for(state="visible", timeout=6000)

        await option.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await option.hover()
        await human_wait(50, 120)
        await option.click()
