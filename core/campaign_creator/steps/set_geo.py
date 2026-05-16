# -*- coding: utf-8 -*-
"""Шаг: гео — Антарктика + целевая страна оффера, удалить дефолтную страну.

UI: блок «Местоположения» в drawer (свёрнут — нужно кликнуть). Внутри —
combobox «Поиск местоположений». Вводим страну → выбираем option с тем же
названием. Затем удаляем дефолтную страну (обычно «Китай») по чипу
[aria-label="Удалить: Китай"] или родственному паттерну.
"""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

ANTARCTICA_QUERY = "Антарктида"  # FB ищет «Антарктика» по запросу «Антарктида»
ANTARCTICA_OPTION = "Антарктика"
DEFAULT_COUNTRY = "Китай"


class SetGeoStep(BaseStep):
    """Добавить Антарктику и целевую страну, убрать дефолтный Китай."""

    name = "set_geo"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            # Legacy-путь (нет params) — сохраняем старое поведение.
            if params is None:
                return await self._execute_legacy(page, context)

            # Декларативный путь: diff между текущими чипами и желаемым набором.
            countries = list(params.get("countries") or [])
            if not countries:
                countries = [context.geo_slot_name] if context and context.geo_slot_name else []
            desired = [ANTARCTICA_OPTION, *countries]

            current = await self._read_current_chips(page)
            to_add = [c for c in desired if c not in current]
            to_remove = [c for c in current if c not in desired]

            if not to_add and not to_remove:
                logger.info("Гео уже соответствует ожиданиям: %s", desired)
                return StepResult(success=True, message="гео уже соответствует ожиданиям")

            await self._open_locations_block(page)
            await human_wait(300, 600)

            for label in to_add:
                if label == ANTARCTICA_OPTION:
                    await self._add_country(page, ANTARCTICA_QUERY, ANTARCTICA_OPTION)
                else:
                    await self._add_country(page, label, label)
                await human_wait(400, 700)

            for label in to_remove:
                await self._remove_default(page, label)
                await human_wait(200, 400)

            logger.info("Гео: добавлены %s, удалены %s", to_add, to_remove)
            return StepResult(
                success=True,
                message=f"Гео: + {to_add}, - {to_remove}",
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_geo: {exc}")

    async def _execute_legacy(self, page: Page, context: StepContext) -> StepResult:
        """Старое поведение для legacy-runner: Антарктика + slot, удалить Китай."""
        try:
            slot = context.geo_slot_name or context.extra.get("offer_country_name", "")
            if not slot:
                return StepResult(success=False, message="Нет geo_slot_name у оффера")

            await self._open_locations_block(page)
            await human_wait(300, 600)

            await self._add_country(page, ANTARCTICA_QUERY, ANTARCTICA_OPTION)
            await human_wait(400, 700)
            await self._add_country(page, slot, slot)
            await human_wait(400, 700)

            await self._remove_default(page, DEFAULT_COUNTRY)

            logger.info("Гео: + %s, + %s, - %s", ANTARCTICA_OPTION, slot, DEFAULT_COUNTRY)
            return StepResult(
                success=True,
                message=f"Гео: {ANTARCTICA_OPTION} + {slot} (удалён {DEFAULT_COUNTRY})",
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_geo: {exc}")

    async def _read_current_chips(self, page: Page) -> list[str]:
        """Вернуть aria-label или текст всех уже выбранных чипов гео."""
        try:
            chips = await page.evaluate(
                """
                () => {
                    const items = Array.from(document.querySelectorAll('li'));
                    const result = [];
                    for (const li of items) {
                        const btns = Array.from(li.querySelectorAll('[role="button"], button, div[role="button"]'));
                        if (!btns.some(b => (b.innerText || '').trim().startsWith('Удалить'))) continue;
                        const nameBtn = btns.find(b => {
                            const t = (b.innerText || '').trim();
                            return t && !t.startsWith('Удалить') && !t.startsWith('Открыть');
                        });
                        if (nameBtn) result.push((nameBtn.innerText || '').trim());
                    }
                    return result;
                }
                """
            )
            return list(chips or [])
        except Exception:
            return []

    async def _open_locations_block(self, page: Page) -> None:
        """Раскрыть блок «Местоположения», если combobox ещё не виден."""
        combo = page.get_by_role("combobox", name="Поиск местоположений").first
        if await combo.count() and await combo.is_visible():
            return
        block = page.get_by_role("button", name="Местоположения").first
        await block.wait_for(state="visible", timeout=8000)
        await block.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await block.hover()
        await human_wait(50, 120)
        await block.click()

    async def _add_country(self, page: Page, query: str, option_label: str) -> None:
        combo = page.get_by_role("combobox", name="Поиск местоположений").first
        await combo.wait_for(state="visible", timeout=8000)
        await combo.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await combo.click()
        await human_wait(120, 240)
        await combo.fill(query)
        await human_wait(900, 1400)
        # FB option: первая строка — название, вторая — тип («Страна/регион», «Место»).
        # Различаем «Антарктика» от «Антарктида» по типу: для стран берём «Страна/регион».
        type_label = "Страна/регион"
        option = page.locator(
            f'[role="option"]:has-text("{option_label}"):has-text("{type_label}")'
        ).first
        await option.wait_for(state="visible", timeout=8000)
        await option.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await option.hover()
        await human_wait(50, 120)
        await option.click()

    async def _remove_default(self, page: Page, name: str) -> None:
        """Удалить чип дефолтной страны.

        У каждой выбранной страны есть строка с тремя кнопками: имя страны
        («Китай»), «Открыть раскрывающееся меню», «Удалить». Найдём кнопку
        с aria-label «Китай» и кликнем по соседней «Удалить» в том же
        контейнере (через JS, т.к. структура сложная).
        """
        try:
            removed = await page.evaluate(
                """
                (name) => {
                    // Чип страны — это <li>, содержащий кнопки с innerText:
                    // имя страны, "Открыть раскрывающееся меню", "Удалить".
                    const items = Array.from(document.querySelectorAll('li'));
                    for (const li of items) {
                        const btns = Array.from(li.querySelectorAll('[role="button"], button, div[role="button"]'));
                        const labels = btns.map(b => (b.innerText || '').trim());
                        if (!labels.some(t => t === name)) continue;
                        const remove = btns.find(b => (b.innerText || '').trim().startsWith('Удалить'));
                        if (remove) { remove.click(); return true; }
                    }
                    return false;
                }
                """,
                name,
            )
            if removed:
                logger.info("Чип %r удалён", name)
            else:
                logger.warning("Чип удаления %r не найден — пропускаем", name)
        except Exception as exc:
            logger.warning("Ошибка удаления чипа %r: %s", name, exc)
