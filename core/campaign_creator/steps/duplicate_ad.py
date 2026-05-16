# -*- coding: utf-8 -*-
"""Дублировать объявление: меню ··· → Дублировать."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait
from core.campaign_creator.tree_nav import (
    ad_items_for_adset,
    click_more_actions,
    menu_click,
)

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class DuplicateAdStep(BaseStep):
    name = "duplicate_ad"
    is_checkpoint = False
    idempotent = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            adset_idx = int(p.get("adset_idx", 0))
            source = int(p.get("source_ad_idx", 0))
            ads = await ad_items_for_adset(page, adset_idx)
            count_before = await ads.count()
            source_item = ads.nth(source)
            await source_item.scroll_into_view_if_needed()
            await human_wait(120, 240)
            await click_more_actions(source_item)
            await human_wait(120, 240)
            await menu_click(page, "Дублировать")
            await human_wait(600, 1100)
            # Если появился диалог подтверждения — нажать «Дублировать».
            try:
                confirm = page.get_by_role("button", name="Дублировать").first
                await confirm.wait_for(state="visible", timeout=2000)
                await confirm.click()
            except Exception:
                pass
            # Подождать пока количество объявлений не вырастет.
            for _ in range(40):
                await human_wait(150, 250)
                ads_now = await (await ad_items_for_adset(page, adset_idx)).count()
                if ads_now > count_before:
                    return StepResult(
                        success=True, message=f"Дублировано: {count_before}→{ads_now}"
                    )
            return StepResult(success=False, message="Дубликат объявления не появился в дереве")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка duplicate_ad: {exc}")
