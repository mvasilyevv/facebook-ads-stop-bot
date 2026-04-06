# -*- coding: utf-8 -*-
"""Тесты humanizer: профили, perlin-шум, idle-паузы, скролл по таблице."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class FakeTableMouse:
    """Фейковая мышь, которая раскрывает строку только при скролле над таблицей."""

    def __init__(self, page) -> None:
        self._page = page
        self.last_position = (0.0, 0.0)

    async def move(self, x: float, y: float) -> None:
        self.last_position = (x, y)

    async def wheel(self, _delta_x: int, _delta_y: int) -> None:
        x, y = self.last_position
        if 100 <= x <= 300 and 100 <= y <= 300:
            self._page.revealed = True


class FakeTablePage:
    """Фейковая страница, где элемент появляется только после скролла над таблицей."""

    def __init__(self) -> None:
        self.viewport_size = {"width": 1000, "height": 800}
        self.revealed = False
        self.element = object()
        self.mouse = FakeTableMouse(self)

    async def query_selector(self, _selector: str):
        return self.element if self.revealed else None

    async def evaluate(self, _script: str):
        return {"x": 200, "y": 200}


# Тест: human_scroll_to_find должен крутить колесо над самой таблицей, а не по центру окна.
@pytest.mark.asyncio
async def test_human_scroll_to_find_uses_ads_table_anchor():
    """Если таблица имеет свой контейнер, скролл должен привязываться именно к нему."""
    from core.browser.humanizer import human_scroll_to_find

    page = FakeTablePage()

    with patch("core.browser.humanizer.asyncio.sleep", new=AsyncMock()):
        element = await human_scroll_to_find(
            page,
            '[data-surface*="table_row:120241979860890176"]',
            max_steps=2,
            step_px=220,
        )

    assert element is page.element
    assert 100 <= page.mouse.last_position[0] <= 300
    assert 100 <= page.mouse.last_position[1] <= 300


# Тест: HumanProfile создаётся с валидными диапазонами полей.
def test_human_profile_fields_in_range():
    """Все числовые поля профиля должны быть в разумных диапазонах."""
    from core.browser.humanizer import HumanProfile

    for _ in range(20):
        p = HumanProfile()
        assert 0.5 <= p.speed_factor <= 1.6
        assert 0.4 <= p.jitter_factor <= 1.7
        assert 0.5 <= p.pause_factor <= 1.5
        assert 0.05 <= p.overshoot_chance <= 0.35
        assert 0.01 <= p.idle_chance <= 0.10
        assert p.idle_duration[0] < p.idle_duration[1]
        assert p.bezier_steps_range[0] < p.bezier_steps_range[1]


# Тест: get_default_profile возвращает один и тот же объект (синглтон).
def test_default_profile_is_singleton():
    """Повторный вызов get_default_profile должен вернуть тот же объект."""
    from core.browser.humanizer import get_default_profile

    p1 = get_default_profile()
    p2 = get_default_profile()
    assert p1 is p2


# Тест: reset_default_profile создаёт новый объект.
def test_reset_default_profile_creates_new():
    """reset_default_profile должен вернуть новый профиль, отличный от старого."""
    from core.browser.humanizer import get_default_profile, reset_default_profile

    old = get_default_profile()
    new = reset_default_profile()
    assert new is not old
    # Новый профиль теперь является синглтоном
    assert get_default_profile() is new


# Тест: _perlin_delay возвращает положительное число и не выходит за разумные пределы.
def test_perlin_delay_positive_and_bounded():
    """Perlin-задержка должна быть положительной и в пределах базового диапазона."""
    from core.browser.humanizer import _perlin_delay

    for seed in [0, 1.5, 42.0, 99.9]:
        for t in [0.0, 0.25, 0.5, 0.75, 1.0]:
            delay = _perlin_delay(0.01, 0.05, t, seed)
            assert delay > 0, f"Задержка должна быть > 0, получено {delay}"
            # Не должна быть абсурдно большой
            assert delay < 0.2, f"Задержка слишком большая: {delay}"


# Тест: _perlin_delay даёт разные значения для разных t (не константа).
def test_perlin_delay_varies_with_t():
    """Perlin-шум должен давать вариацию задержек по траектории."""
    from core.browser.humanizer import _perlin_delay

    seed = 7.77
    delays = [_perlin_delay(0.01, 0.05, t / 10, seed) for t in range(11)]
    # Не все значения должны совпадать
    unique = set(round(d, 8) for d in delays)
    assert len(unique) > 3, f"Ожидалась вариация, получено {len(unique)} уникальных значений"


# Тест: human_move передаёт профиль в _bezier_path.
@pytest.mark.asyncio
async def test_human_move_uses_profile():
    """human_move должен учитывать переданный профиль (кастомные шаги Безье)."""
    from core.browser.humanizer import HumanProfile, human_move

    # Профиль с минимальными шагами — меньше вызовов mouse.move
    profile = HumanProfile(
        speed_factor=0.1,
        jitter_factor=0.1,
        pause_factor=0.01,
        overshoot_chance=0.0,
        idle_chance=0.0,
        idle_duration=(0.0, 0.01),
        bezier_steps_range=(3, 4),
    )

    move_calls: list[tuple[float, float]] = []

    class FakeMouse:
        async def move(self, x: float, y: float) -> None:
            move_calls.append((x, y))

    class FakePage:
        viewport_size = {"width": 800, "height": 600}
        mouse = FakeMouse()

    with patch("core.browser.humanizer.asyncio.sleep", new=AsyncMock()):
        await human_move(FakePage(), 400, 300, profile=profile)

    # 3–4 шага + 1 начальная точка = 4–5 вызовов move
    assert 3 <= len(move_calls) <= 6, f"Ожидалось 3–6 вызовов, получено {len(move_calls)}"


# Тест: human_click вызывает idle-паузу при idle_chance=1.0.
@pytest.mark.asyncio
async def test_human_click_idle_pause():
    """При idle_chance=1.0 human_click должен вызвать длинную idle-паузу."""
    from core.browser.humanizer import HumanProfile, human_click

    profile = HumanProfile(
        speed_factor=0.01,
        jitter_factor=0.1,
        pause_factor=0.01,
        overshoot_chance=0.0,
        idle_chance=1.0,  # Всегда idle
        idle_duration=(2.0, 3.0),
        bezier_steps_range=(2, 3),
    )

    sleep_durations: list[float] = []
    original_sleep = AsyncMock(side_effect=lambda d: sleep_durations.append(d))

    class FakeBox:
        pass

    class FakeElement:
        async def scroll_into_view_if_needed(self) -> None:
            pass

        async def bounding_box(self):
            return {"x": 100, "y": 100, "width": 50, "height": 30}

    class FakeMouse:
        async def move(self, x: float, y: float) -> None:
            pass

        async def down(self) -> None:
            pass

        async def up(self) -> None:
            pass

    class FakePage:
        viewport_size = {"width": 800, "height": 600}
        mouse = FakeMouse()

    with patch("core.browser.humanizer.asyncio.sleep", original_sleep):
        await human_click(FakePage(), FakeElement(), profile=profile)

    # Должна быть хотя бы одна пауза >= 2.0 секунд (idle)
    long_pauses = [d for d in sleep_durations if d >= 2.0]
    assert len(long_pauses) >= 1, f"Ожидалась idle-пауза >= 2с, паузы: {sleep_durations}"
