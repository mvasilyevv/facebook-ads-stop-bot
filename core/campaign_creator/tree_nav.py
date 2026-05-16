# -*- coding: utf-8 -*-
"""Навигация по дереву кампания/адсет/объявление в Ads Manager."""

from __future__ import annotations

from playwright.async_api import Locator, Page


async def adset_items(page: Page) -> Locator:
    """Все треюитемы адсетов (второй уровень дерева)."""
    # Адсеты — прямые дочерние группы treeitem от treeitem кампании.
    return page.locator('[role="tree"] [role="treeitem"][aria-level="2"]')


async def ad_items_for_adset(page: Page, adset_idx: int) -> Locator:
    """Все треюитемы объявлений внутри адсета по индексу."""
    adsets = page.locator('[role="tree"] [role="treeitem"][aria-level="2"]')
    return adsets.nth(adset_idx).locator('[role="treeitem"][aria-level="3"]')


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
