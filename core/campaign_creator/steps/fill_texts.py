# -*- coding: utf-8 -*-
"""Шаг: для каждого объявления — заполнить headline и primary_text."""

from __future__ import annotations

import logging
import re

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_type, human_wait
from core.campaign_creator.selectors import SELECTORS

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)


async def _type_into_label(page: Page, label: str, text: str) -> None:
    """Найти поле по подписи и ввести текст, перебирая разные стратегии."""
    rx = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
    candidates = [
        page.get_by_role("textbox", name=rx),
        page.get_by_label(rx),
        page.locator(f'[aria-label="{label}"]'),
        page.locator(f'[aria-label*="{label}" i]'),
        page.locator(
            f'div[role="textbox"][aria-label*="{label}" i], textarea[aria-label*="{label}" i]'
        ),
    ]
    last_exc: Exception | None = None
    for loc in candidates:
        try:
            first = loc.first
            if await first.count() == 0:
                continue
            if not await first.is_visible():
                continue
            await first.scroll_into_view_if_needed(timeout=1500)
            await first.click(timeout=2500)
            await first.fill("")
            await first.type(text, delay=60)
            return
        except Exception as exc:
            last_exc = exc
            continue
    raise RuntimeError(f'Поле "{label}" не найдено: {last_exc}')


class FillTextsStep(BaseStep):
    """Заполнить headline и primary_text для каждого объявления."""

    name = "fill_texts"
    is_checkpoint = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            if params and ("headline" in params or "primary_text" in params):
                # Декларативный путь: одно объявление за вызов.
                primary = (params.get("primary_text") or "").strip()
                headline = (params.get("headline") or "").strip()
                if primary:
                    try:
                        await human_type(page, SELECTORS.get("primary_text", ""), primary)
                    except Exception:
                        await _type_into_label(page, "Основной текст", primary)
                    await human_wait(200, 400)
                if headline:
                    try:
                        await human_type(page, SELECTORS["headline"], headline)
                    except Exception:
                        await _type_into_label(page, "Заголовок", headline)
                return StepResult(success=True, message="Тексты заполнены")

            for idx, adset in enumerate(context.adsets):
                primary = (adset.primary_text or "").strip()
                headline = (adset.headline or "").strip()
                if primary:
                    try:
                        await human_type(page, SELECTORS.get("primary_text", ""), primary)
                    except Exception:
                        await _type_into_label(page, "Основной текст", primary)
                    await human_wait(200, 400)
                if headline:
                    try:
                        await human_type(page, SELECTORS["headline"], headline)
                    except Exception:
                        await _type_into_label(page, "Заголовок", headline)
                if primary or headline:
                    logger.info("Тексты адсета %s заполнены", adset.display_name(idx))
                else:
                    logger.info("Тексты адсета %s пропущены (пусто)", adset.display_name(idx))
            return StepResult(success=True, message="Тексты заполнены")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка fill_texts: {exc}")
