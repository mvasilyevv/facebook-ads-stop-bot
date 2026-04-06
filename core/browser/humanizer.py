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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchright.async_api import ElementHandle, Page


@dataclass(frozen=True)
class HumanProfile:
    """Профиль «почерка» пользователя — генерируется один раз на сессию.

    Каждый профиль задаёт персональные диапазоны скоростей, jitter и пауз,
    чтобы бот вёл себя консистентно в рамках одной сессии, но отличался
    между сессиями.
    """

    # Множитель скорости движения мыши (меньше = быстрее)
    speed_factor: float = field(default_factory=lambda: random.uniform(0.7, 1.4))
    # Множитель jitter при наведении на элемент
    jitter_factor: float = field(default_factory=lambda: random.uniform(0.6, 1.5))
    # Множитель пауз перед/после действий
    pause_factor: float = field(default_factory=lambda: random.uniform(0.7, 1.3))
    # Шанс overshoot при движении (базовый 0.20)
    overshoot_chance: float = field(default_factory=lambda: random.uniform(0.10, 0.30))
    # Шанс случайной idle-паузы «отвлёкся» (2–5 секунд)
    idle_chance: float = field(default_factory=lambda: random.uniform(0.02, 0.08))
    # Диапазон idle-паузы в секундах
    idle_duration: tuple[float, float] = field(
        default_factory=lambda: (random.uniform(1.5, 2.5), random.uniform(4.0, 6.0))
    )
    # Количество шагов Безье (больше = плавнее)
    bezier_steps_range: tuple[int, int] = field(
        default_factory=lambda: (
            random.randint(18, 24),
            random.randint(30, 40),
        )
    )


# Профиль по умолчанию — для обратной совместимости вызовов без profile
_DEFAULT_PROFILE: HumanProfile | None = None


def get_default_profile() -> HumanProfile:
    """Возвращает синглтон-профиль для текущего процесса (создаётся один раз)."""
    global _DEFAULT_PROFILE  # noqa: PLW0603
    if _DEFAULT_PROFILE is None:
        _DEFAULT_PROFILE = HumanProfile()
    return _DEFAULT_PROFILE


def reset_default_profile() -> HumanProfile:
    """Пересоздаёт профиль по умолчанию (вызывать при старте новой сессии)."""
    global _DEFAULT_PROFILE  # noqa: PLW0603
    _DEFAULT_PROFILE = HumanProfile()
    return _DEFAULT_PROFILE


def _perlin_delay(base_min: float, base_max: float, t: float, seed: float) -> float:
    """Микро-вариация задержки через простой синусоидальный шум.

    Добавляет волнообразное ускорение/замедление поверх базового диапазона,
    имитируя неравномерность движений реального человека.
    """
    noise = math.sin(t * 7.3 + seed) * 0.3 + math.sin(t * 13.1 + seed * 2.7) * 0.2
    mid = (base_min + base_max) / 2
    spread = (base_max - base_min) / 2
    return max(base_min * 0.5, mid + spread * noise)


async def _resolve_scroll_anchor(page: "Page") -> tuple[float, float] | None:
    """Пытается найти координаты центра реальной скроллируемой области.

    Для Ads Manager обычный центр viewport часто попадает в шапку или боковую
    панель, из-за чего колесо мыши не двигает таблицу. Поэтому сначала ищем
    сетку/таблицу с объявлениями и только затем fallback на центр окна.
    """
    try:
        anchor = await page.evaluate("""() => {
            const selectors = [
                '[role="grid"]',
                '[role="table"]',
                '[aria-rowcount]',
                '[data-surface*="table_row:"]',
            ];

            for (const selector of selectors) {
                const node = document.querySelector(selector);
                if (!(node instanceof Element)) {
                    continue;
                }

                const rect = node.getBoundingClientRect();
                if (rect.width < 40 || rect.height < 40) {
                    continue;
                }

                return {
                    x: rect.left + rect.width * 0.5,
                    y: rect.top + rect.height * 0.5,
                };
            }

            return null;
        }""")
    except Exception:
        return None

    if not isinstance(anchor, dict):
        return None

    try:
        return float(anchor["x"]), float(anchor["y"])
    except (KeyError, TypeError, ValueError):
        return None


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
    *,
    profile: HumanProfile | None = None,
) -> list[tuple[float, float]]:
    """Генерирует список точек по кривой Безье от start до end.

    Контрольные точки расставляются случайно — движение получается
    плавным, но не прямолинейным, как у живого пользователя.
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    dist = math.hypot(dx, dy)

    # Контрольные точки с отклонением, масштабированным профилем
    jf = (profile or get_default_profile()).jitter_factor
    spread = min(dist * 0.35 * jf, 120 * jf)
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
    profile: HumanProfile | None = None,
) -> None:
    """Плавно перемещает мышь к (target_x, target_y) по кривой Безье.

    Args:
        current_pos: текущая позиция мыши; если None — угадывается от центра viewport.
        profile: профиль «почерка»; если None — используется синглтон.
    """
    prof = profile or get_default_profile()

    if current_pos is None:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        current_pos = (
            vp["width"] * random.uniform(0.3, 0.7),
            vp["height"] * random.uniform(0.3, 0.7),
        )

    # Случайная точка попадания с учётом jitter профиля
    jitter = random.uniform(1, 4) * prof.jitter_factor
    dest = (
        target_x + random.uniform(-jitter, jitter),
        target_y + random.uniform(-jitter, jitter),
    )

    steps = random.randint(*prof.bezier_steps_range)
    points = _bezier_path(current_pos, dest, steps=steps, profile=prof)

    # Seed для perlin-шума — уникален на каждое движение
    noise_seed = random.uniform(0, 100)

    # Скорость: ease in-out + perlin-шум для микро-ускорений
    n = len(points)
    for i, (x, y) in enumerate(points):
        await page.mouse.move(x, y)
        t = i / max(n - 1, 1)
        ease = 4 * t * (1 - t)  # ∈ [0, 1], макс в середине
        base_delay = random.uniform(0.005, 0.025) * (1.5 - ease) * prof.speed_factor
        delay = _perlin_delay(base_delay * 0.5, base_delay * 1.5, t, noise_seed)
        await asyncio.sleep(delay)

    # Overshoot + возврат (шанс из профиля)
    if random.random() < prof.overshoot_chance:
        over_x = dest[0] + random.uniform(-8, 8) * prof.jitter_factor
        over_y = dest[1] + random.uniform(-4, 4) * prof.jitter_factor
        await page.mouse.move(over_x, over_y)
        await asyncio.sleep(random.uniform(0.04, 0.10) * prof.pause_factor)
        await page.mouse.move(dest[0], dest[1])
        await asyncio.sleep(random.uniform(0.03, 0.07) * prof.pause_factor)


async def human_click(
    page: "Page",
    element: "ElementHandle",
    *,
    double_check_pause: bool = True,
    profile: HumanProfile | None = None,
) -> None:
    """Кликает на элемент с полной имитацией поведения человека.

    1. Прокручивает элемент в видимую область
    2. Получает bounding box элемента
    3. Плавно перемещает мышь по кривой Безье к случайной точке внутри элемента
    4. Небольшая пауза «наведения» перед кликом
    5. mousedown → случайная задержка → mouseup (имитация длины нажатия)

    Args:
        double_check_pause: добавить паузу «прочитал и решил нажать» перед кликом.
        profile: профиль «почерка»; если None — используется синглтон.
    """
    prof = profile or get_default_profile()

    await element.scroll_into_view_if_needed()
    await asyncio.sleep(random.uniform(0.2, 0.5) * prof.pause_factor)

    box = await element.bounding_box()
    if box is None:
        # Fallback на простой клик
        await element.click()
        return

    # Случайная точка внутри элемента (не центр — человек кликает немного вразброс)
    click_x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
    click_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)

    # Плавное движение к элементу
    await human_move(page, click_x, click_y, profile=prof)

    # Пауза «наведения» — человек немного задерживается перед кликом
    await asyncio.sleep(random.uniform(0.08, 0.25) * prof.pause_factor)

    # Пауза «читаю и принимаю решение» (только для важных действий)
    if double_check_pause:
        await asyncio.sleep(random.uniform(0.3, 1.2) * prof.pause_factor)

    # Случайная idle-пауза «отвлёкся» перед кликом
    if random.random() < prof.idle_chance:
        await asyncio.sleep(random.uniform(*prof.idle_duration))

    # Нажатие: mousedown → пауза → mouseup
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.06, 0.18) * prof.pause_factor)
    await page.mouse.up()

    # Небольшое движение после клика (человек не держит мышь идеально)
    await asyncio.sleep(random.uniform(0.05, 0.15) * prof.pause_factor)
    drift_x = click_x + random.uniform(-6, 6) * prof.jitter_factor
    drift_y = click_y + random.uniform(-3, 3) * prof.jitter_factor
    await page.mouse.move(drift_x, drift_y)


async def human_scroll_to_find(
    page: "Page",
    selector: str,
    *,
    max_steps: int = 30,
    step_px: int | None = None,
    profile: HumanProfile | None = None,
) -> "object | None":
    """Прокручивает страницу вниз пока не найдёт элемент по selector.

    Скролл имитирует человека: переменный шаг, случайные паузы, движение мыши.
    Возвращает найденный элемент или None.
    """
    prof = profile or get_default_profile()
    vp = page.viewport_size or {"width": 1280, "height": 800}
    anchor = await _resolve_scroll_anchor(page)
    if anchor is None:
        scroll_x = vp["width"] * random.uniform(0.35, 0.65)
        scroll_y = vp["height"] * random.uniform(0.40, 0.60)
    else:
        scroll_x = min(max(anchor[0], 24.0), max(vp["width"] - 24.0, 24.0))
        scroll_y = min(max(anchor[1], 24.0), max(vp["height"] - 24.0, 24.0))

    await page.mouse.move(scroll_x, scroll_y)
    await asyncio.sleep(random.uniform(0.1, 0.3) * prof.pause_factor)

    for step_i in range(max_steps):
        el = await page.query_selector(selector)
        if el is not None:
            return el

        # Переменный шаг скролла
        px = step_px or random.randint(250, 550)
        await page.mouse.wheel(0, px)

        # Случайная idle-пауза «отвлёкся» (2–5 секунд)
        if random.random() < prof.idle_chance:
            await asyncio.sleep(random.uniform(*prof.idle_duration))
        # Случайные паузы с редким «замедлением» как у живого пользователя
        elif random.random() < 0.15:
            await asyncio.sleep(random.uniform(0.8, 2.0) * prof.pause_factor)
        else:
            # Perlin-шум на обычных паузах скролла
            t = step_i / max(max_steps - 1, 1)
            delay = _perlin_delay(0.25, 0.65, t, scroll_x) * prof.pause_factor
            await asyncio.sleep(delay)

        # Изредка слегка двигаем мышь (человек не держит курсор неподвижно)
        if random.random() < 0.30:
            jx = random.uniform(-20, 20) * prof.jitter_factor
            jy = random.uniform(-10, 10) * prof.jitter_factor
            await page.mouse.move(scroll_x + jx, scroll_y + jy)

    return None


async def human_wheel_scroll(
    page: "Page",
    delta_y: int,
    *,
    anchor: tuple[float, float] | None = None,
    move_before: bool = True,
    settle_range: tuple[float, float] = (0.25, 0.65),
    drift_x_range: tuple[float, float] = (-20, 20),
    drift_y_range: tuple[float, float] = (-10, 10),
    profile: HumanProfile | None = None,
) -> tuple[float, float]:
    """Прокручивает колесо мыши через общий humanized-слой.

    Используется там, где важно не вызывать wheel напрямую из рабочих модулей.
    Возвращает последнюю позицию курсора после небольшого пост-скролл дрейфа.
    """
    prof = profile or get_default_profile()

    if anchor is None:
        vp = page.viewport_size or {"width": 1280, "height": 800}
        anchor = (
            vp["width"] * random.uniform(0.35, 0.65),
            vp["height"] * random.uniform(0.40, 0.60),
        )

    if move_before:
        await human_move(page, anchor[0], anchor[1], profile=prof)
        await asyncio.sleep(random.uniform(0.04, 0.12) * prof.pause_factor)

    await page.mouse.wheel(0, delta_y)

    # Случайная idle-пауза «отвлёкся» после скролла
    if random.random() < prof.idle_chance:
        await asyncio.sleep(random.uniform(*prof.idle_duration))

    drift_x = anchor[0] + random.uniform(*drift_x_range) * prof.jitter_factor
    drift_y = anchor[1] + random.uniform(*drift_y_range) * prof.jitter_factor
    await page.mouse.move(drift_x, drift_y)
    await asyncio.sleep(random.uniform(*settle_range) * prof.pause_factor)
    return drift_x, drift_y
