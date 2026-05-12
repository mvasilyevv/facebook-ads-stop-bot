# -*- coding: utf-8 -*-
"""Шаг: загрузка креативов из creo_folder/<adset.creo_subfolder>."""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

_ALLOWED = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".gif"}


class UploadCreativesStep(BaseStep):
    """Загрузить все креативы из подпапки каждого адсета."""

    name = "upload_creatives"
    is_checkpoint = False

    async def execute(self, page: Page, context: StepContext) -> StepResult:
        try:
            base = Path(context.creo_folder)
            total = 0
            for adset in context.adsets:
                subdir = base / adset.creo_subfolder
                if not subdir.exists():
                    logger.warning("Подпапка не найдена: %s", subdir)
                    continue
                files = [str(p) for p in subdir.iterdir() if p.suffix.lower() in _ALLOWED]
                if not files:
                    logger.warning("Пустая подпапка: %s", subdir)
                    continue
                async with page.expect_file_chooser() as fc_info:
                    await page.click('[aria-label="Добавить медиафайлы"]')
                file_chooser = await fc_info.value
                await file_chooser.set_files(files)
                await human_wait(800, 1500)
                total += len(files)
                logger.info("Адсет %s: загружено %d файлов", adset.name, len(files))
            return StepResult(success=True, message=f"Загружено креативов: {total}")
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка upload_creatives: {exc}")
