# -*- coding: utf-8 -*-
"""Дублировать адсет через меню ··· в дереве."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait
from core.campaign_creator.tree_nav import (
    adset_items,
    click_more_actions,
    menu_click,
)

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class DuplicateAdsetStep(BaseStep):
    name = "duplicate_adset"
    is_checkpoint = False
    idempotent = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            source = int(p.get("source_idx", 0))
            adsets = await adset_items(page)
            count_before = await adsets.count()
            src = adsets.nth(source)
            await src.scroll_into_view_if_needed()
            await human_wait(120, 240)
            await click_more_actions(src)
            await human_wait(120, 240)
            await menu_click(page, "Дублировать")
            await human_wait(600, 1100)
            try:
                confirm = page.get_by_role("button", name="Дублировать").first
                await confirm.wait_for(state="visible", timeout=2000)
                await confirm.click()
            except Exception:
                pass
            for _ in range(40):
                await human_wait(200, 350)
                now = await (await adset_items(page)).count()
                if now > count_before:
                    return StepResult(
                        success=True, message=f"Адсет дублирован: {count_before}→{now}"
                    )
            return StepResult(success=False, message="Дубликат адсета не появился")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка duplicate_adset: {exc}")
