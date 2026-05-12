# -*- coding: utf-8 -*-
"""Шаг: вставить landing URL и tracking-параметры (sub2-sub7)."""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_type
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

OPERATOR_INITIALS = "MV"


def build_url_params(*, ad_name: str, cabinet_id: str) -> str:
    """Собрать строку URL-параметров для трекинга.

    {{campaign.name}}/{{adset.name}}/{{ad.name}} — FB-макросы, оставляем как есть.
    """
    return (
        f"sub2={OPERATOR_INITIALS}"
        f"&sub3={ad_name}"
        f"&sub4={cabinet_id}"
        "&sub5={{campaign.name}}"
        "&sub6={{adset.name}}"
        "&sub7={{ad.name}}"
    )


class SetTrackingUrlStep(BaseStep):
    """Заполнить landing_url и url_params для каждого объявления."""

    name = "set_tracking_url"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            await human_type(page, SELECTORS["landing_url"], context.landing_url)
            for adset in context.adsets:
                params = build_url_params(ad_name=adset.name, cabinet_id=context.cabinet_id)
                await human_type(page, SELECTORS["url_params"], params)
                logger.info("Tracking-параметры для %s: %s", adset.name, params)
            return StepResult(success=True, message="Tracking URL установлен")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_tracking_url: {exc}")
