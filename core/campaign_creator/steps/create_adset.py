# -*- coding: utf-8 -*-
"""Шаг: создать адсеты — итерация по ctx.adsets, задаём имя."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click_label, human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

ADSET_NAME_INPUT = 'input[placeholder="Введите название группы объявлений..."]'


class CreateAdsetStep(BaseStep):
    """Для каждого AdsetSpec — создать адсет и ввести имя."""

    name = "create_adset"
    is_checkpoint = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            adsets = (params or {}).get("adsets") if params else None
            if adsets is None:
                adsets = context.adsets
            for idx, adset in enumerate(adsets):
                if idx > 0:
                    await human_click_label(page, "Создать группу объявлений")
                    await human_wait(400, 800)
                name = adset.display_name(idx)
                # Не используем human_type — он добавляет паузу/scroll после ввода,
                # за это время фокус слетает и Enter уходит мимо поля.
                locator = page.locator(ADSET_NAME_INPUT).first
                await locator.wait_for(state="visible", timeout=10000)
                await locator.scroll_into_view_if_needed()
                await human_wait(120, 220)
                await locator.click(click_count=3, timeout=2000)
                await human_wait(40, 100)
                await page.keyboard.press("Backspace")
                await human_wait(80, 160)
                # Печатаем имя прямо в locator. Enter/Tab подхватывают опцию из автокомплита,
                # поэтому подтверждаем кликом по нейтральному заголовку секции — фокус слетает,
                # значение поля остаётся как есть, дропдаун закрывается.
                await locator.type(name, delay=70)
                await human_wait(150, 250)
                try:
                    await page.get_by_text("Название группы объявлений", exact=False).first.click(
                        timeout=2000
                    )
                except Exception:
                    # Фолбэк: клик в пустую область страницы.
                    await page.mouse.click(10, 10)
                await human_wait(200, 400)
                logger.info("Адсет %d: %s", idx + 1, name)
            return StepResult(success=True, message=f"Создано адсетов: {len(adsets)}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка create_adset: {exc}")
