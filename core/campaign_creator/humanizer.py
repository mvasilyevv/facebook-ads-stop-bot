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
    """Клик с предварительным наведением и микропаузами.

    Поддерживает обычные CSS-селекторы и Playwright-локаторы вида
    'role=button[name="..."]' или 'text=...'.
    """
    locator = _resolve_locator(page, selector)
    await locator.wait_for(state="visible", timeout=timeout)
    await locator.scroll_into_view_if_needed()
    await human_wait()
    await locator.hover()
    await human_wait(50, 150)
    await locator.click()
    await human_wait()


async def human_type(
    page: Page,
    selector: str,
    text: str,
    *,
    timeout: int = 10000,  # noqa: ASYNC109 — Playwright-стиль параметра, не asyncio.timeout
    clear: bool = True,
) -> None:
    """Посимвольный ввод с псевдослучайной скоростью.

    По умолчанию очищает поле перед вводом — иначе вставка идёт в середину
    существующего значения (FB часто префиллит дефолтное название).
    """
    locator = _resolve_locator(page, selector)
    await locator.wait_for(state="visible", timeout=timeout)
    await locator.scroll_into_view_if_needed()
    await human_wait()
    await locator.click()
    await human_wait(50, 150)
    if clear:
        # Тройной клик выделяет всё содержимое поля.
        try:
            await locator.click(click_count=3, timeout=1500)
        except Exception:
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Control+A")
        await human_wait(40, 100)
        await page.keyboard.press("Backspace")
        await human_wait(80, 160)
    for ch in text:
        await locator.type(ch, delay=random.uniform(40, 110))
    await human_wait()


async def human_select(page: Page, selector: str, value: str, *, timeout: int = 10000) -> None:  # noqa: ASYNC109
    """Выбор опции в <select>."""
    locator = _resolve_locator(page, selector)
    await locator.wait_for(state="visible", timeout=timeout)
    await human_wait()
    await locator.select_option(value)
    await human_wait()


def _resolve_locator(page: Page, selector: str):
    """Поддержка role-based и text-based селекторов наряду с CSS.

    'role=button[name="X"]' → page.get_by_role('button', name='X')
    'text=X'               → page.get_by_text('X', exact=True)
    остальное              → page.locator(selector).first
    """
    import re

    if selector.startswith("role="):
        m = re.match(r'role=([a-z]+)\[name="(.+)"\]$', selector)
        if m:
            role, name = m.group(1), m.group(2)
            return page.get_by_role(role, name=name).first
    if selector.startswith("text="):
        return page.get_by_text(selector[len("text=") :], exact=True).first
    return page.locator(selector).first


def _label_locators(page: Page, label: str) -> list:
    """Стратегии поиска кликабельного элемента по человекочитаемой подписи."""
    import re

    rx = re.compile(rf"^\s*\+?\s*{re.escape(label)}\s*$", re.IGNORECASE)
    return [
        page.get_by_role("button", name=rx),
        page.get_by_role("menuitem", name=rx),
        page.get_by_role("radio", name=rx),
        page.get_by_role("checkbox", name=rx),
        page.get_by_role("tab", name=rx),
        page.get_by_role("link", name=rx),
        page.get_by_role("row", name=rx),
        page.get_by_role("gridcell", name=rx),
        page.locator(f'[aria-label="{label}"]'),
        page.locator(f'[aria-label*="{label}" i]'),
        page.locator(f'label:has-text("{label}")'),
        page.locator(f'[role="row"]:has-text("{label}")'),
        page.locator(f'[role="gridcell"]:has-text("{label}")'),
        page.locator(f'div[role="button"]:has-text("{label}")'),
        page.get_by_text(rx),
        page.locator(f'span:has-text("{label}")'),
    ]


def _option_locators(page: Page, label: str) -> list:
    """Стратегии поиска варианта в выпадающих списках/комбобоксах."""
    import re

    rx = re.compile(rf"^\s*{re.escape(label)}\s*$", re.IGNORECASE)
    return [
        page.get_by_role("option", name=rx),
        page.get_by_role("menuitemradio", name=rx),
        page.get_by_role("menuitem", name=rx),
        page.locator(f'[role="option"]:has-text("{label}")'),
        page.locator(f'[role="menuitemradio"]:has-text("{label}")'),
        page.locator(f'[role="listbox"] :text-is("{label}")'),
        page.get_by_text(rx),
    ]


async def human_click_label(
    page: Page,
    label: str,
    *,
    total_timeout_ms: int = 12000,
    option: bool = False,
) -> str:
    """Найти и кликнуть элемент по подписи, перебирая разные стратегии.

    Если option=True — ищет среди вариантов dropdown/listbox.
    Возвращает имя сработавшей стратегии. Бросает PWTimeout по дедлайну.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    deadline = asyncio.get_event_loop().time() + total_timeout_ms / 1000
    attempt = 0
    last_exc: Exception | None = None
    build = _option_locators if option else _label_locators
    last_scroll_y: float | None = None
    bottom_hits = 0

    while asyncio.get_event_loop().time() < deadline:
        attempt += 1
        for locator in build(page, label):
            try:
                first = locator.first
                if await first.count() == 0:
                    continue
                try:
                    await first.scroll_into_view_if_needed(timeout=1500)
                except Exception:
                    pass
                if not await first.is_visible():
                    continue
                await human_wait(80, 180)
                await first.hover(timeout=1500)
                await human_wait(50, 120)
                await first.click(timeout=2500)
                await human_wait()
                return f"#{attempt} {locator}"
            except Exception as exc:
                last_exc = exc
                continue
        # Скроллим вниз — но останавливаемся, если уже упёрлись в подвал.
        if not option:
            try:
                cur_y = await page.evaluate("() => window.scrollY")
                if last_scroll_y is not None and abs(cur_y - last_scroll_y) < 2:
                    bottom_hits += 1
                    if bottom_hits >= 2:
                        # Дальше прокрутки не будет — нужный элемент тут точно отсутствует.
                        break
                else:
                    bottom_hits = 0
                last_scroll_y = cur_y
                await page.mouse.wheel(0, 600)
            except Exception:
                pass
        await asyncio.sleep(0.4)

    raise PWTimeout(
        f'Не удалось найти кликабельный элемент "{label}" за {total_timeout_ms}ms'
        + (f" (последняя ошибка: {last_exc})" if last_exc else "")
    )


async def human_pick_option(page: Page, label: str, *, total_timeout_ms: int = 10000) -> str:
    """Кликнуть пункт в открытом dropdown/listbox по подписи."""
    return await human_click_label(page, label, total_timeout_ms=total_timeout_ms, option=True)


async def human_scroll(page: Page, *, distance: int = 300) -> None:
    """Плавный скролл страницы вниз на заданное расстояние."""
    steps = random.randint(3, 6)
    step = distance // steps
    for _ in range(steps):
        await page.mouse.wheel(0, step)
        await human_wait(60, 150)
