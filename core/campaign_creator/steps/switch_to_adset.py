# -*- coding: utf-8 -*-
"""Перейти к редактированию N-го адсета — клик по узлу в дереве."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait
from core.campaign_creator.tree_nav import adset_items

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class SwitchToAdsetStep(BaseStep):
    name = "switch_to_adset"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            adset_idx = int(p.get("adset_idx", 0))
            adsets = await adset_items(page)
            item = adsets.nth(adset_idx)
            selected = await item.get_attribute("aria-selected")
            if selected == "true":
                return StepResult(success=True, message=f"Уже на адсете {adset_idx + 1}")
            await item.scroll_into_view_if_needed()
            await human_wait(120, 220)
            await item.click()
            await human_wait(400, 700)
            return StepResult(success=True, message=f"Переключились на адсет {adset_idx + 1}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка switch_to_adset: {exc}")
