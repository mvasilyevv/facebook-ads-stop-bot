# -*- coding: utf-8 -*-
"""Переименовать адсет по индексу."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait
from core.campaign_creator.selectors import SELECTORS
from core.campaign_creator.tree_nav import adset_items, get_item_name

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class RenameAdsetStep(BaseStep):
    name = "rename_adset"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            adset_idx = int(p.get("adset_idx", 0))
            suffix = str(p.get("suffix", "")).strip()
            target = f"{adset_idx + 1} | {suffix}" if suffix else f"{adset_idx + 1}"

            adsets = await adset_items(page)
            item = adsets.nth(adset_idx)
            await item.scroll_into_view_if_needed()
            current = await get_item_name(item)
            if target in current:
                return StepResult(success=True, message=f"Адсет уже {target}")

            await item.click()
            await human_wait(300, 600)
            inp = page.locator(SELECTORS["adset_name"]).first
            await inp.wait_for(state="visible", timeout=8000)
            await inp.click(click_count=3)
            await human_wait(50, 120)
            await page.keyboard.press("Backspace")
            await human_wait(80, 160)
            await inp.type(target, delay=60)
            await human_wait(150, 250)
            await page.keyboard.press("Tab")
            return StepResult(success=True, message=f"Адсет переименован: {target}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка rename_adset: {exc}")
