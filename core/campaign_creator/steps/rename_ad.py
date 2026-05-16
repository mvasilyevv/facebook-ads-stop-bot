# -*- coding: utf-8 -*-
"""Переименовать объявление в дереве по индексу."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait
from core.campaign_creator.selectors import SELECTORS
from core.campaign_creator.tree_nav import ad_items_for_adset, get_item_name

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class RenameAdStep(BaseStep):
    name = "rename_ad"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            adset_idx = int(p.get("adset_idx", 0))
            ad_idx = int(p.get("ad_idx", 0))
            suffix = str(p.get("suffix", "")).strip()
            target = f"{ad_idx + 1} | {suffix}" if suffix else f"{ad_idx + 1}"

            ads = await ad_items_for_adset(page, adset_idx)
            item = ads.nth(ad_idx)
            await item.scroll_into_view_if_needed()
            current = await get_item_name(item)
            if target in current:
                return StepResult(success=True, message=f"Объявление уже {target}")

            # Открываем форму редактирования объявления — клик по узлу.
            await item.click()
            await human_wait(300, 600)

            # Меняем имя в инпуте имени объявления.
            inp = page.locator(SELECTORS["ad_name"]).first
            await inp.wait_for(state="visible", timeout=8000)
            await inp.click(click_count=3)
            await human_wait(50, 120)
            await page.keyboard.press("Backspace")
            await human_wait(80, 160)
            await inp.type(target, delay=60)
            await human_wait(150, 250)
            await page.keyboard.press("Tab")
            return StepResult(success=True, message=f"Объявление переименовано: {target}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка rename_ad: {exc}")
