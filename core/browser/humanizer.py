# -*- coding: utf-8 -*-
"""Имитация человеческого поведения мыши в Playwright.

Используется в disable/enable worker чтобы клик на переключатель
выглядел как действие живого человека:
  - Движение по кривой Безье с jitter
  - Случайная точка попадания внутри элемента (не всегда центр)
  - Небольшой overshoot перед остановкой
  - Случайная задержка mousedown → mouseup
  - Микропаузы между действиями
"""

from __future__ import annotations

import asyncio
import math
import random
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchright.async_api import ElementHandle, Page


def _bezier_point(
    t: float,
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
) -> tuple[float, float]:
    """Точка на кубической кривой Безье при параметре t ∈ [0, 1]."""
    u = 1 - t
    x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
    return x, y


def _bezier_path(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int = 25,
) -> list[tuple[float, float]]:
    """Генерирует список точек по кривой Безье от start до end.

    Контрольные точки расставляются случайно — движение получается
    плавным, но не прямолинейным, как у живого пользователя.
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy)

    # Контрольные точки с отклонением ±30% от прямой линии
    spread = min(dist * 0.35, 120)
    cp1 = (
        start[0] + dx * random.uniform(0.2, 0.4) + random.uniform(-spread, spread),
        start[1] + dy * random.uniform(0.2, 0.4) + random.uniform(-spread, spread),
    )
    cp2 = (
        start[0] + dx * random.uniform(0.6, 0.8) + random.uniform(-spread, spread),
        start[1] + dy * random.uniform(0.6, 0.8) + random.uniform(-spread, spread),
    )

    return [_bezier_point(i / steps, start, cp1, cp2, end) for i in range(steps + 1)]


async def human_move(
    page: "Page",
    target_x: float,
    target_y: float,
    *,
    current_pos: tuple[float, float] | None = None,
) -> None:
    """Плавно перемещает мышь к (target_x, target_y) по кривой Безье.

    Args:
        current_pos: текущая позиция мыши; если None — угадывается от центра viewport.
    """
    if current_pos is None:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        current_pos = (vp["width"] * random.uniform(0.3, 0.7), vp["height"] * random.uniform(0.3, 0.7))

    # Случайная точка попадания (не всегда точно в centre)
    jitter = random.uniform(1, 4)
    dest = (
        target_x + random.uniform(-jitter, jitter),
        target_y + random.uniform(-jitter, jitter),
    )

    points = _bezier_path(current_pos, dest, steps=random.randint(20, 35))

    # Скорость: ускорение в начале, замедление в конце (ease in-out)
    n = len(points)
    for i, (x, y) in enumerate(points):
        await page.mouse.move(x, y)
        # ease in-out: пауза в начале и конце длиннее
        t = i / max(n - 1, 1)
        ease = 4 * t * (1 - t)  # ∈ [0, 1], макс в середине
        delay = random.uniform(0.005, 0.025) * (1.5 - ease)
        await asyncio.sleep(delay)

    # Небольшой overshoot + возврат (~20% шанс)
    if random.random() < 0.20:
        over_x = dest[0] + random.uniform(-8, 8)
        over_y = dest[1] + random.uniform(-4, 4)
        await page.mouse.move(over_x, over_y)
        await asyncio.sleep(random.uniform(0.04, 0.10))
        await page.mouse.move(dest[0], dest[1])
        await asyncio.sleep(random.uniform(0.03, 0.07))


async def human_click(
    page: "Page",
    element: "ElementHandle",
    *,
    double_check_pause: bool = True,
) -> None:
    """Кликает на элемент с полной имитацией поведения человека.

    1. Прокручивает элемент в видимую область
    2. Получает bounding box элемента
    3. Плавно перемещает мышь по кривой Безье к случайной точке внутри элемента
    4. Небольшая пауза «наведения» перед кликом
    5. mousedown → случайная задержка → mouseup (имитация длины нажатия)

    Args:
        double_check_pause: добавить паузу «прочитал и решил нажать» перед кликом.
    """
    await element.scroll_into_view_if_needed()
    await asyncio.sleep(random.uniform(0.2, 0.5))

    box = await element.bounding_box()
    if box is None:
        # Fallback на простой клик
        await element.click()
        return

    # Случайная точка внутри элемента (не центр — человек кликает немного вразброс)
    click_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
    click_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)

    # Плавное движение к элементу
    await human_move(page, click_x, click_y)

    # Пауза «наведения» — человек немного задерживается перед кликом
    await asyncio.sleep(random.uniform(0.08, 0.25))

    # Пауза «читаю и принимаю решение» (только для важных действий)
    if double_check_pause:
        await asyncio.sleep(random.uniform(0.3, 1.2))

    # Нажатие: mousedown → пауза → mouseup
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.06, 0.18))  # длина нажатия
    await page.mouse.up()

    # Небольшое движение после клика (человек не держит мышь идеально)
    await asyncio.sleep(random.uniform(0.05, 0.15))
    drift_x = click_x + random.uniform(-6, 6)
    drift_y = click_y + random.uniform(-3, 3)
    await page.mouse.move(drift_x, drift_y)


async def human_scroll_to_find(
    page: "Page",
    selector: str,
    *,
    max_steps: int = 30,
    step_px: int | None = None,
) -> "object | None":
    """Прокручивает страницу вниз пока не найдёт элемент по selector.

    Скролл имитирует человека: переменный шаг, случайные паузы, движение мыши.
    Возвращает найденный элемент или None.
    """
    vp = page.viewport_size or {"width": 1280, "height": 800}
    scroll_x = vp["width"] * random.uniform(0.35, 0.65)
    scroll_y = vp["height"] * random.uniform(0.40, 0.60)

    await page.mouse.move(scroll_x, scroll_y)
    await asyncio.sleep(random.uniform(0.1, 0.3))

    for _ in range(max_steps):
        el = await page.query_selector(selector)
        if el is not None:
            return el

        # Переменный шаг скролла
        px = step_px or random.randint(250, 550)
        await page.mouse.wheel(0, px)

        # Случайные паузы с редким «замедлением» как у живого пользователя
        if random.random() < 0.15:
            await asyncio.sleep(random.uniform(0.8, 2.0))  # задумался
        else:
            await asyncio.sleep(random.uniform(0.25, 0.65))

        # Изредка слегка двигаем мышь (человек не держит курсор неподвижно)
        if random.random() < 0.30:
            jx = random.uniform(-20, 20)
            jy = random.uniform(-10, 10)
            await page.mouse.move(scroll_x + jx, scroll_y + jy)

    return None
