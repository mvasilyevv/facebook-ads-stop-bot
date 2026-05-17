# -*- coding: utf-8 -*-
"""Шаг: гео — Антарктика + целевая страна оффера, удалить дефолтную страну.

UI: блок «Местоположения» в drawer (свёрнут — нужно кликнуть). Внутри —
input[placeholder="Поиск местоположений"]. Вводим страну → выбираем опцию
с тем же названием в открывшемся listbox. Удаление чипа — по
[aria-label^="Удалить: <страна>"].
"""

from __future__ import annotations

import logging

from playwright.async_api import Page

from core.campaign_creator.humanizer import (
    human_click_label,
    human_pick_option,
    human_wait,
)

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

ANTARCTICA_QUERY = "Антарктика"
ANTARCTICA_OPTION = "Антарктика"
SECTION_LABEL = "Местоположения"
SEARCH_INPUT_SELECTOR = 'input[placeholder="Поиск местоположений"]'


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

            # Сначала раскрываем секцию — иначе чипы не в DOM и diff будет пустым.
            await self._ensure_search_visible(page)
            current = await self._read_current_chips(page)
            to_add = [c for c in desired if c not in current]

            for label in to_add:
                if label == ANTARCTICA_OPTION:
                    await self._add_country(page, ANTARCTICA_QUERY, ANTARCTICA_OPTION)
                else:
                    await self._add_country(page, label, label)
                await human_wait(400, 700)

            # Перечитываем чипы после добавления — теперь удаляем всё, что не в desired
            # (исходная страна аккаунта: Китай/Гонконг/США/…).
            current_after = await self._read_current_chips(page)
            to_remove = [c for c in current_after if c not in desired]
            for label in to_remove:
                await self._remove_chip(page, label)
                await human_wait(200, 400)

            logger.info("Гео: добавлены %s, удалены %s", to_add, to_remove)
            return StepResult(
                success=True,
                message=f"Гео: + {to_add}, - {to_remove}",
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_geo: {exc}")

    async def _execute_legacy(self, page: Page, context: StepContext) -> StepResult:
        """Старое поведение для legacy-runner: Антарктика + slot, удалить дефолтную страну.

        Дефолт у разных FB-аккаунтов разный (Китай, Гонконг, …) — поэтому
        не привязываемся к имени, а удаляем всё, что осталось в чипах после
        добавления нашего набора (Антарктика + slot).
        """
        try:
            slot = context.geo_slot_name or context.extra.get("offer_country_name", "")
            if not slot:
                return StepResult(success=False, message="Нет geo_slot_name у оффера")

            await self._ensure_search_visible(page)

            await self._add_country(page, ANTARCTICA_QUERY, ANTARCTICA_OPTION)
            await human_wait(400, 700)
            await self._add_country(page, slot, slot)
            await human_wait(400, 700)

            desired = {ANTARCTICA_OPTION, slot}
            current = await self._read_current_chips(page)
            removed: list[str] = []
            for chip in current:
                if chip in desired:
                    continue
                await self._remove_chip(page, chip)
                await human_wait(200, 400)
                removed.append(chip)

            logger.info("Гео: + %s, + %s, - %s", ANTARCTICA_OPTION, slot, removed or "—")
            return StepResult(
                success=True,
                message=f"Гео: {ANTARCTICA_OPTION} + {slot} (удалены: {removed or '—'})",
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_geo: {exc}")

    async def _read_current_chips(self, page: Page) -> list[str]:
        """Имена выбранных чипов гео.

        У чипа нет aria-label — это `<li>` с `<span class="_3bss">name</span>`
        и кнопкой «Закрыть». Берём только те `_3bss`, рядом с которыми внутри
        того же `<li>` есть кнопка закрытия — это отсекает варианты опций
        в выпадающем списке поиска.
        """
        try:
            chips = await page.evaluate(
                """
                () => {
                    const spans = Array.from(document.querySelectorAll('span._3bss'));
                    const names = [];
                    for (const s of spans) {
                        const li = s.closest('li');
                        if (!li) continue;
                        // Чип реальный, если рядом есть кнопка «Закрыть».
                        const hasClose = Array.from(li.querySelectorAll('button')).some(b => {
                            const a = b.querySelector('.accessible_elem');
                            const t = ((a && a.innerText) || b.innerText || '').trim();
                            return t === 'Закрыть' || t === 'Close';
                        });
                        if (!hasClose) continue;
                        const name = (s.innerText || '').trim();
                        if (name) names.push(name);
                    }
                    return Array.from(new Set(names));
                }
                """
            )
            return list(chips or [])
        except Exception:
            return []

    async def _ensure_search_visible(self, page: Page) -> None:
        """Убедиться, что input поиска местоположений виден. Если нет — раскрыть секцию.

        FB иногда лениво рендерит блок «Местоположения». humanizer сам делает
        scroll-into-view и перебирает разные роли (button/heading/link), поэтому
        ручной mouse.wheel здесь не нужен.
        """
        search = page.locator(SEARCH_INPUT_SELECTOR).first
        if await search.count() and await search.is_visible():
            return

        try:
            await human_click_label(page, SECTION_LABEL, total_timeout_ms=6000)
        except Exception as exc:
            logger.debug("Не удалось раскрыть секцию «%s»: %s", SECTION_LABEL, exc)

        await search.wait_for(state="visible", timeout=6000)

    async def _add_country(self, page: Page, query: str, option_label: str) -> None:
        search = page.locator(SEARCH_INPUT_SELECTOR).first
        await search.wait_for(state="visible", timeout=8000)
        await search.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await search.click()
        await human_wait(120, 240)
        # Чистим input через клавиатуру (combobox-span не принимает fill).
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        await human_wait(80, 160)
        await page.keyboard.type(query, delay=60)
        await human_wait(900, 1400)
        # Выбираем опцию через humanizer — он перебирает role=option,
        # menuitem, listbox+text и т. п.
        await human_pick_option(page, option_label, total_timeout_ms=6000)

    async def _remove_chip(self, page: Page, name: str) -> None:
        """Удалить чип страны.

        У кнопки нет aria-label — она содержит `<span class="accessible_elem">Закрыть</span>`.
        Находим её через JS: для каждого `_3bss` с искомым именем ищем в том же
        `<li>` кнопку с текстом «Закрыть» и кликаем.
        """
        try:
            clicked = await page.evaluate(
                """
                (name) => {
                    const spans = Array.from(document.querySelectorAll('span._3bss'));
                    for (const s of spans) {
                        if ((s.innerText || '').trim() !== name) continue;
                        const li = s.closest('li');
                        if (!li) continue;
                        const btn = Array.from(li.querySelectorAll('button')).find(b => {
                            const a = b.querySelector('.accessible_elem');
                            const t = ((a && a.innerText) || b.innerText || '').trim();
                            return t === 'Закрыть' || t === 'Close';
                        });
                        if (!btn) continue;
                        btn.scrollIntoView({block: 'center'});
                        btn.click();
                        return true;
                    }
                    return false;
                }
                """,
                name,
            )
            if not clicked:
                logger.warning("Чип %r не найден — пропускаем", name)
                return
            logger.info("Чип %r удалён", name)
        except Exception as exc:
            logger.warning("Ошибка удаления чипа %r: %s", name, exc)
