# -*- coding: utf-8 -*-
"""Удалить текущий креатив объявления и загрузить новый файл."""

from __future__ import annotations

import logging
import os

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait
from core.campaign_creator.tree_nav import ad_items_for_adset

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


class ReattachCreativeStep(BaseStep):
    name = "reattach_creative"
    is_checkpoint = False
    idempotent = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            adset_idx = int(p.get("adset_idx", 0))
            ad_idx = int(p.get("ad_idx", 0))
            file = p.get("file")
            subfolder = str(p.get("subfolder") or "")
            if not file:
                return StepResult(success=False, message="reattach_creative: нет file в params")

            ads = await ad_items_for_adset(page, adset_idx)
            await ads.nth(ad_idx).click()
            await human_wait(400, 800)

            # Удалить текущее видео/изображение, если оно есть.
            try:
                remove = page.get_by_role("button", name="Удалить").first
                await remove.wait_for(state="visible", timeout=3000)
                await remove.click()
                await human_wait(300, 500)
                # Иногда нужно подтвердить.
                try:
                    await page.get_by_role("button", name="Удалить").first.click(timeout=1500)
                except Exception:
                    pass
            except Exception:
                logger.info(
                    "reattach_creative: кнопка «Удалить» не найдена — возможно креатив уже пуст"
                )

            # Загрузить файл через input[type=file].
            path = os.path.join(context.creo_folder or "", subfolder, file)
            file_input = page.locator('input[type="file"]').first
            await file_input.wait_for(state="attached", timeout=10000)
            await file_input.set_input_files(path)
            await human_wait(1500, 2500)
            return StepResult(success=True, message=f"reattach_creative: {path}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка reattach_creative: {exc}")
