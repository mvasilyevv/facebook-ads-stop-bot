# -*- coding: utf-8 -*-
"""Хьюманайзер для Playwright — имитирует человеческое поведение.

Все действия обёрнуты случайными задержками, мышь двигается с дрожанием,
текст печатается посимвольно. Это снижает риск ban-детекции FB.
"""

from __future__ import annotations

import asyncio
import random

from playwright.async_api import Page


async def human_wait(min_ms: int = 80, max_ms: int = 300) -> None:
    """Случайная пауза между действиями."""
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def human_click(page: Page, selector: str, *, timeout: int = 10000) -> None:  # noqa: ASYNC109
    """Клик с предварительным наведением и микропаузами."""
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=timeout)
    await locator.scroll_into_view_if_needed()
    await human_wait()
    await locator.hover()
    await human_wait(50, 150)
    await locator.click()
    await human_wait()


async def human_type(page: Page, selector: str, text: str, *, timeout: int = 10000) -> None:  # noqa: ASYNC109
    """Посимвольный ввод с псевдослучайной скоростью."""
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=timeout)
    await locator.scroll_into_view_if_needed()
    await human_wait()
    await locator.click()
    await human_wait(50, 150)
    for ch in text:
        await locator.type(ch, delay=random.uniform(40, 110))
    await human_wait()


async def human_select(page: Page, selector: str, value: str, *, timeout: int = 10000) -> None:  # noqa: ASYNC109
    """Выбор опции в <select>."""
    locator = page.locator(selector).first
    await locator.wait_for(state="visible", timeout=timeout)
    await human_wait()
    await locator.select_option(value)
    await human_wait()


async def human_scroll(page: Page, *, distance: int = 300) -> None:
    """Плавный скролл страницы вниз на заданное расстояние."""
    steps = random.randint(3, 6)
    step = distance // steps
    for _ in range(steps):
        await page.mouse.wheel(0, step)
        await human_wait(60, 150)
