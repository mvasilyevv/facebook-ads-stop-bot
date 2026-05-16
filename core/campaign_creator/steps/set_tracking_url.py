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

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            landing_url = p.get("landing_url", context.landing_url)
            cabinet_id = p.get("cabinet_id", context.cabinet_id)
            await human_type(page, SELECTORS["landing_url"], landing_url)
            if "ad_name" in p:
                url_params = build_url_params(ad_name=p["ad_name"], cabinet_id=cabinet_id)
                await human_type(page, SELECTORS["url_params"], url_params)
                logger.info("Tracking-параметры для %s: %s", p["ad_name"], url_params)
            else:
                for adset in context.adsets:
                    ad_name = getattr(adset, "name", None) or adset.display_name(0)
                    url_params = build_url_params(ad_name=ad_name, cabinet_id=cabinet_id)
                    await human_type(page, SELECTORS["url_params"], url_params)
                    logger.info("Tracking-параметры для %s: %s", ad_name, url_params)
            return StepResult(success=True, message="Tracking URL установлен")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_tracking_url: {exc}")
