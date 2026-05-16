# -*- coding: utf-8 -*-
"""Шаг: полное создание одного объявления внутри секции «Объявление».

Поток (по записи 20260513_203542_KE_CR2):
1. Заполнить «Введите название объявления...» = имя файла без расширения и без
   суффикса `_N` (например `KE_CR2_CR004_1.jpeg` → `KE_CR2_CR004`).
2. Источник: radio «Загрузка вручную».
3. Формат: radio «Одно изображение или видео».
4. URL сайта: landing_url из контекста.
5. Кнопка «Настроить креатив» → menuitem «Рекламное изображение» → «Далее».
6. «Загрузить» → file chooser → залить нужный файл из подпапки адсета.
7. «Поиск по медиафайлам» = stem файла → клик миниатюре → «Далее» x2.
8. CTA combobox «Выберите объект» → option «Играть» → «Далее» x2 → «Готово».
9. Удалить лишний creative variation (data-surface
   ads_creative_flex_header_button_delete).
10. Параметры URL:
   `sub2=MV&sub3={ad_name}&sub4={cabinet_id}&sub5={{campaign.name}}
    &sub6={{adset.name}}&sub7={{ad.name}}`.

Один прогон шага создаёт ОДНО объявление по первому файлу первой подпапки
первого адсета. Создание следующих ad через клонирование — отдельная запись.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

_ALLOWED = {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".gif"}
_CTA_LABEL = "Играть"


def _ad_name_from_file(path: Path) -> str:
    """`KE_CR2_CR004_1.jpeg` → `KE_CR2_CR004`. Срезает только хвост `_<digits>`."""
    stem = path.stem
    return re.sub(r"_\d+$", "", stem)


def _url_params(ad_name: str, cabinet_id: str) -> str:
    return (
        "sub2=MV"
        f"&sub3={ad_name}"
        f"&sub4={cabinet_id}"
        "&sub5={{campaign.name}}"
        "&sub6={{adset.name}}"
        "&sub7={{ad.name}}"
    )


class UploadCreativesStep(BaseStep):
    """Создать одно объявление: имя, источник, формат, медиа, CTA, URL params."""

    name = "upload_creatives"
    is_checkpoint = False
    idempotent = False

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            p = params or {}
            base = Path(context.creo_folder)
            # Декларативный путь: указаны subfolder и file — берём именно их.
            if "file" in p:
                subfolder = str(p.get("subfolder") or context.adsets[0].subfolder(0))
                subdir = base / subfolder
                if not subdir.exists():
                    return StepResult(success=False, message=f"Подпапка не найдена: {subdir}")
                creative = subdir / p["file"]
                if not creative.exists():
                    return StepResult(success=False, message=f"Файл не найден: {creative}")
            else:
                adset = context.adsets[0]
                subdir = base / adset.subfolder(0)
                if not subdir.exists():
                    return StepResult(success=False, message=f"Подпапка не найдена: {subdir}")
                files = sorted(pp for pp in subdir.iterdir() if pp.suffix.lower() in _ALLOWED)
                if not files:
                    return StepResult(success=False, message=f"Пустая подпапка: {subdir}")
                creative = files[0]

            ad_name = _ad_name_from_file(creative)
            landing_url = p.get("landing_url", context.landing_url)
            cabinet_id = p.get("cabinet_id", context.cabinet_id)
            logger.info("Создаю объявление %s из %s", ad_name, creative.name)

            await self._fill_ad_name(page, ad_name)
            await self._pick_radio(page, "Загрузка вручную")
            await self._pick_radio(page, "Одно изображение или видео")
            await self._fill_landing(page, landing_url)
            await self._open_creative_setup(page)
            await self._upload_media(page, creative)
            await self._pick_thumbnail(page, creative.stem)
            await self._set_cta(page)
            await self._finish_creative_dialog(page)
            await self._delete_extra_variation(page)
            await self._fill_url_params(page, _url_params(ad_name, cabinet_id))

            return StepResult(success=True, message=f"Создано объявление {ad_name}")
        except Exception as exc:
            logger.exception("Ошибка create_ad")
            return StepResult(success=False, message=f"Ошибка upload_creatives: {exc}")

    async def _fill_ad_name(self, page: Page, value: str) -> None:
        field = page.get_by_placeholder("Введите название объявления...").first
        await field.wait_for(state="visible", timeout=8000)
        await field.scroll_into_view_if_needed()
        await human_wait(150, 300)
        await field.click()
        await human_wait(80, 180)
        await field.fill(value)
        await human_wait(200, 450)

    async def _pick_radio(self, page: Page, name: str) -> None:
        radio = page.get_by_role("radio", name=name, exact=True).first
        await radio.wait_for(state="visible", timeout=8000)
        await radio.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await radio.click()
        await human_wait(400, 800)

    async def _fill_landing(self, page: Page, url: str) -> None:
        field = page.get_by_role("textbox", name="URL сайта").first
        await field.wait_for(state="visible", timeout=8000)
        await field.scroll_into_view_if_needed()
        await human_wait(100, 220)
        await field.click()
        await field.fill(url)
        await human_wait(300, 600)

    async def _open_creative_setup(self, page: Page) -> None:
        btn = page.get_by_role("button", name="Настроить креатив", exact=True).first
        await btn.wait_for(state="visible", timeout=8000)
        await btn.scroll_into_view_if_needed()
        await human_wait(100, 220)
        await btn.click()
        await human_wait(500, 900)

        item = page.get_by_role("menuitem", name="Рекламное изображение", exact=True).first
        await item.wait_for(state="visible", timeout=8000)
        await human_wait(80, 180)
        await item.click()
        await human_wait(500, 900)

        # Дальше открывается диалог выбора медиа — пройти первый «Далее».
        await self._click_dialog_button(page, "Далее")

    async def _upload_media(self, page: Page, file: Path) -> None:
        """Кнопка «Загрузить» открывает системный file chooser."""
        upload_btn = page.get_by_role("button", name="Загрузить", exact=True).first
        await upload_btn.wait_for(state="visible", timeout=8000)
        await human_wait(120, 260)
        async with page.expect_file_chooser() as fc_info:
            await upload_btn.click()
        chooser = await fc_info.value
        await chooser.set_files(str(file))
        # Аплоад: ждём появления поиска по медиафайлам.
        await page.get_by_role("textbox", name="Поиск по медиафайлам").first.wait_for(
            state="visible", timeout=30000
        )
        await human_wait(800, 1500)

    async def _pick_thumbnail(self, page: Page, query: str) -> None:
        """Найти миниатюру через поиск и кликнуть."""
        search = page.get_by_role("textbox", name="Поиск по медиафайлам").first
        await search.click()
        await search.fill(query)
        await human_wait(800, 1400)
        # Карточка — это div с alt-текстом или текстом имени файла.
        thumb = page.locator(f'[role="dialog"] :text-matches("{re.escape(query)}\\.")').first
        await thumb.wait_for(state="visible", timeout=10000)
        await human_wait(150, 320)
        await thumb.click()
        await human_wait(400, 700)
        await self._click_dialog_button(page, "Далее")
        await human_wait(400, 800)
        await self._click_dialog_button(page, "Далее")
        await human_wait(400, 800)

    async def _set_cta(self, page: Page) -> None:
        combo = page.get_by_role("combobox", name="Выберите объект").first
        await combo.wait_for(state="visible", timeout=10000)
        await combo.scroll_into_view_if_needed()
        await human_wait(150, 300)
        await combo.click()
        await human_wait(400, 800)
        option = page.get_by_role("option", name=_CTA_LABEL, exact=True).first
        await option.wait_for(state="visible", timeout=8000)
        await option.click()
        await human_wait(400, 700)

    async def _finish_creative_dialog(self, page: Page) -> None:
        await self._click_dialog_button(page, "Далее")
        await human_wait(500, 900)
        await self._click_dialog_button(page, "Далее")
        await human_wait(500, 900)
        await self._click_dialog_button(page, "Готово")
        await human_wait(800, 1500)

    async def _click_dialog_button(self, page: Page, name: str) -> None:
        btn = page.get_by_role("button", name=name, exact=True).last
        await btn.wait_for(state="visible", timeout=10000)
        await human_wait(120, 260)
        await btn.click()

    async def _delete_extra_variation(self, page: Page) -> None:
        """Удалить лишний creative variation (data-surface ..._delete).

        Если кнопка не найдена — нестрашно, идём дальше.
        """
        sel = '[data-surface*="creative_relaxation_section"][data-surface*="header_button_delete"]'
        btn = page.locator(sel).first
        try:
            await btn.wait_for(state="visible", timeout=4000)
        except Exception:
            logger.info("Кнопка удаления creative variation не найдена — пропуск")
            return
        await human_wait(150, 320)
        await btn.click()
        await human_wait(500, 900)

    async def _fill_url_params(self, page: Page, value: str) -> None:
        field = page.get_by_role("textbox", name="Параметры URL").first
        await field.wait_for(state="visible", timeout=10000)
        await field.scroll_into_view_if_needed()
        await human_wait(120, 260)
        await field.click()
        await field.fill(value)
        await human_wait(300, 600)
