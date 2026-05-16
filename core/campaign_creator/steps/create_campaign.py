# -*- coding: utf-8 -*-
"""Шаг: создать новую кампанию в Ads Manager (humanizer + selectors)."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_click_label, human_type, human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

CAMPAIGN_NAME_INPUT = 'input[placeholder="Введите название кампании..."]'


class CreateCampaignStep(BaseStep):
    """Создать → Продажи → Продолжить → ввод названия кампании."""

    name = "create_campaign"
    is_checkpoint = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            campaign_name = (params or {}).get("campaign_name", context.campaign_name)
            used = await human_click_label(page, "Создать")
            logger.info("Кнопка 'Создать' найдена: %s", used)
            await human_wait(500, 900)

            # FB иногда открывает «быстрое создание» вместо мастера — там нужно
            # «Ещё» → «Создать новую кампанию», чтобы попасть в обычный flow.
            if not await self._is_objectives_dialog_open(page):
                try:
                    used = await human_click_label(page, "Ещё", total_timeout_ms=4000)
                    logger.info("Кнопка 'Ещё' нажата: %s", used)
                    await human_wait(300, 600)
                    used = await human_click_label(
                        page, "Создать новую кампанию", total_timeout_ms=4000
                    )
                    logger.info("Пункт 'Создать новую кампанию' выбран: %s", used)
                    await human_wait(400, 800)
                except Exception as exc:
                    logger.info("Меню «Ещё» не понадобилось/не найдено: %s", exc)

            used = await human_click_label(page, "Продажи")
            logger.info("Цель 'Продажи' выбрана: %s", used)
            await human_wait(300, 600)

            used = await human_click_label(page, "Продолжить")
            logger.info("Кнопка 'Продолжить' нажата: %s", used)
            await human_wait(500, 900)

            # FB иногда показывает модалку-предупреждение перед drawer'ом:
            # «button.layerConfirm» — мягко подтверждаем, если появилась.
            try:
                confirm = page.locator("button.layerConfirm").first
                if await confirm.count() and await confirm.is_visible():
                    await confirm.click()
                    logger.info("Подтверждена layerConfirm-модалка")
                    await human_wait(400, 800)
            except Exception as exc:
                logger.debug("layerConfirm-модалка не появилась: %s", exc)

            await human_type(page, CAMPAIGN_NAME_INPUT, campaign_name)
            logger.info("Кампания создана: %s", campaign_name)
            return StepResult(success=True, message=f"Кампания создана: {campaign_name}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка create_campaign: {exc}")

    async def _is_objectives_dialog_open(self, page: Page) -> bool:
        """Открыт ли диалог выбора цели (видна карточка «Продажи»)?"""
        try:
            loc = page.get_by_text("Продажи", exact=True).first
            if await loc.count() == 0:
                return False
            return await loc.is_visible()
        except Exception:
            return False
