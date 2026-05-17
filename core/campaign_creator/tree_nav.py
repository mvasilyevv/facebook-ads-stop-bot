# -*- coding: utf-8 -*-
"""Навигация по дереву кампания/адсет/объявление в Ads Manager."""

from __future__ import annotations

from playwright.async_api import Locator, Page


async def adset_items(page: Page) -> Locator:
    """Все треюитемы адсетов (второй уровень дерева)."""
    # Адсеты — прямые дочерние группы treeitem от treeitem кампании.
    return page.locator('[role="tree"] [role="treeitem"][aria-level="2"]')


async def ad_items_for_adset(page: Page, adset_idx: int) -> Locator:
    """Все треюитемы объявлений внутри адсета по индексу.

    Гарантирует, что дерево уже отрисовало адсет и сам адсет раскрыт
    (если был свёрнут — кликаем chevron / по самому узлу, пока
    `aria-expanded` не станет `true`). После раскрытия ждём появления
    хотя бы одного `aria-level="3"`-узла.
    """
    adsets = page.locator('[role="tree"] [role="treeitem"][aria-level="2"]')
    # Ждём, что нужный адсет вообще существует в дереве.
    await adsets.nth(adset_idx).wait_for(state="visible", timeout=15000)
    adset = adsets.nth(adset_idx)

    # Если адсет свёрнут — раскрываем.
    expanded = await adset.get_attribute("aria-expanded")
    if expanded == "false":
        # Сначала пробуем найти chevron/кнопку раскрытия внутри узла.
        chevron = adset.locator(
            '[aria-label*="азверн" i], [aria-label*="аскры" i], [aria-label*="xpand" i]'
        ).first
        try:
            if await chevron.count() > 0 and await chevron.is_visible():
                await chevron.click()
            else:
                await adset.click()
        except Exception:
            await adset.click()
        # Дожидаемся фактического раскрытия.
        try:
            await page.wait_for_function(
                """(el) => el && el.getAttribute('aria-expanded') === 'true'""",
                arg=await adset.element_handle(),
                timeout=8000,
            )
        except Exception:
            pass

    ads = adset.locator('[role="treeitem"][aria-level="3"]')
    # Ждём, что появилось хотя бы одно объявление под адсетом.
    await ads.first.wait_for(state="visible", timeout=15000)
    return ads


async def click_more_actions(item: Locator) -> None:
    """Клик по кнопке «···» (More actions) на узле дерева."""
    await item.hover()
    btn = item.locator(
        'button[aria-label*="ополнительн" i], [role="button"][aria-label*="ополнительн" i]'
    ).first
    await btn.wait_for(state="visible", timeout=5000)
    await btn.click()


async def menu_click(page: Page, label: str) -> None:
    """Клик по menuitem по тексту."""
    item = page.get_by_role("menuitem", name=label).first
    await item.wait_for(state="visible", timeout=5000)
    await item.click()


async def get_item_name(item: Locator) -> str:
    """Имя узла дерева — берём из aria-label или первого видимого текста."""
    label = await item.get_attribute("aria-label")
    if label:
        return label.strip()
    return (await item.inner_text()).strip().split("\n")[0]
