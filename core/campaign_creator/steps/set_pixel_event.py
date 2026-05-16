# -*- coding: utf-8 -*-
"""Шаг: выбрать пиксель по ID и событие 'Покупка'.

Якоря:
- combobox 'Пиксель' / 'Событие' опознаются по тексту видимой метки label,
  на который ссылается aria-labelledby[0]. Это стабильно и не зависит
  от текущего значения combobox'а.
- option пикселя в раскрытом listbox имеет DOM id == pixel_id
  (например id="2095184697722530").
- option события 'Покупка' имеет id-JSON
  {"eventName":"PURCHASE","pixelID":"<pixel_id>"}.
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
            value = await self._combo_value(page, "Пиксель")
            logger.info("Pixel выбран, combobox value=%r, событие %s", value, EVENT)
            return StepResult(success=True, message=f"Pixel {pixel_id}, событие {EVENT}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_pixel_event: {exc}")

    async def _combo_by_label(self, page: Page, label: str) -> Locator:
        """Combobox, у которого первый span aria-labelledby содержит ровно label."""
        # Через JS находим id label-span'а и затем локатор по aria-labelledby.
        label_id = await page.evaluate(
            """(label) => {
                const combos = document.querySelectorAll('[role="combobox"][aria-labelledby]');
                for (const el of combos) {
                    const ids = (el.getAttribute('aria-labelledby') || '').split(' ');
                    const labelEl = document.getElementById(ids[0]);
                    if (labelEl && (labelEl.innerText || '').trim() === label) {
                        return ids[0];
                    }
                }
                return null;
            }""",
            label,
        )
        if not label_id:
            raise RuntimeError(f"combobox с меткой {label!r} не найден")
        loc = page.locator(f'[role="combobox"][aria-labelledby^="{label_id}"]').first
        await loc.wait_for(state="visible", timeout=8000)
        return loc

    async def _combo_value(self, page: Page, label: str) -> str:
        return await page.evaluate(
            """(label) => {
                const combos = document.querySelectorAll('[role="combobox"]');
                for (const el of combos) {
                    const lb = (el.getAttribute('aria-labelledby') || '').split(' ');
                    const labelEl = document.getElementById(lb[0]);
                    if (labelEl && (labelEl.innerText || '').trim() === label) {
                        const valueEl = lb[1] ? document.getElementById(lb[1]) : null;
                        return valueEl ? (valueEl.innerText || '').trim() : '';
                    }
                }
                return null;
            }""",
            label,
        )

    async def _select_pixel(self, page: Page, pixel_id: str) -> None:
        combo = await self._combo_by_label(page, "Пиксель")
        await combo.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await combo.hover()
        await human_wait(50, 120)
        await combo.click()
        await human_wait(300, 600)

        # Очищаем существующий ввод и печатаем pixel_id.
        await page.keyboard.press("Control+A")
        await human_wait(50, 120)
        await page.keyboard.press("Delete")
        await human_wait(80, 180)
        await page.keyboard.type(pixel_id, delay=40)
        await human_wait(700, 1100)

        option = page.locator(f'[role="option"][id="{pixel_id}"]').first
        await option.wait_for(state="visible", timeout=8000)
        await option.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await option.hover()
        await human_wait(50, 120)
        await option.click()

    async def _select_purchase_event(self, page: Page) -> None:
        # Если событие уже Покупка — пропускаем.
        current = await self._combo_value(page, "Событие")
        if current and current.startswith("Покупка"):
            logger.info("Событие уже 'Покупка', пропускаю")
            return
        combo = await self._combo_by_label(page, "Событие")
        await combo.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await combo.hover()
        await human_wait(50, 120)
        await combo.click()
        await human_wait(300, 600)
        option = page.locator('[role="option"][id*="\\"eventName\\":\\"PURCHASE\\""]').first
        await option.wait_for(state="visible", timeout=8000)
        await option.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await option.hover()
        await human_wait(50, 120)
        await option.click()
