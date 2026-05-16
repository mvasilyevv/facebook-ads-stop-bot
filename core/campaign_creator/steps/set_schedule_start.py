# -*- coding: utf-8 -*-
"""Шаг: дата и время начала кампании (00:00 в выбранный день).

Дата вычисляется из имени кампании по фрагменту 'DD.MM'. Год —
текущий, если дата ещё впереди, иначе следующий. Время — 00:00.

UI: textbox 'Инструмент выбора даты' открывает календарь; день —
кнопка с aria-label вида 'Thursday, 14 May 2026' (английская локаль
независимо от языка интерфейса). Часы/минуты — spinbutton'ы.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from playwright.async_api import Page

from core.campaign_creator.humanizer import human_wait

from .base import BaseStep, StepContext, StepResult

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})(?!\d)")

_EN_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_EN_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def parse_start_date(campaign_name: str, today: datetime | None = None) -> datetime:
    """Извлечь 'DD.MM' из имени кампании и подобрать ближайший такой день в будущем (или сегодня)."""
    today = today or datetime.now()
    m = _DATE_RE.search(campaign_name)
    if not m:
        raise ValueError(f"В имени кампании не найдена дата DD.MM: {campaign_name!r}")
    day, month = int(m.group(1)), int(m.group(2))
    year = today.year
    candidate = datetime(year, month, day)
    if candidate.date() < today.date():
        candidate = datetime(year + 1, month, day)
    return candidate


def en_day_aria_label(d: datetime) -> str:
    """'Thursday, 14 May 2026' — формат, который FB использует в aria-label дня."""
    return f"{_EN_WEEKDAYS[d.weekday()]}, {d.day} {_EN_MONTHS[d.month - 1]} {d.year}"


class SetScheduleStartStep(BaseStep):
    """Установить дату начала кампании (время 00:00)."""

    name = "set_schedule_start"
    is_checkpoint = False
    idempotent = True

    async def execute(
        self, page: Page, context: StepContext, params: dict | None = None
    ) -> StepResult:
        try:
            campaign_name = (params or {}).get("campaign_name", context.campaign_name)
            start = parse_start_date(campaign_name)
            await self._open_calendar(page)
            await human_wait(300, 600)
            await self._pick_day(page, start)
            await human_wait(200, 400)
            await self._set_time(page, hours=0, minutes=0)
            logger.info("Дата начала: %s 00:00", start.strftime("%Y-%m-%d"))
            return StepResult(
                success=True,
                message=f"Начало: {start.strftime('%d.%m.%Y')} 00:00",
            )
        except Exception as exc:
            return StepResult(success=False, message=f"Ошибка set_schedule_start: {exc}")

    async def _open_calendar(self, page: Page) -> None:
        textbox = page.get_by_role("textbox", name="Инструмент выбора даты").first
        await textbox.wait_for(state="visible", timeout=8000)
        await textbox.scroll_into_view_if_needed()
        await human_wait(80, 180)
        await textbox.hover()
        await human_wait(50, 120)
        await textbox.click()

    async def _pick_day(self, page: Page, target: datetime) -> None:
        aria = en_day_aria_label(target)
        # Если нужный месяц ещё не показан — нажимаем стрелку «вперёд» до 12 раз.
        for _ in range(12):
            btn = page.get_by_role("button", name=aria).first
            try:
                if await btn.count() and await btn.is_visible():
                    await btn.scroll_into_view_if_needed()
                    await human_wait(80, 180)
                    await btn.hover()
                    await human_wait(50, 120)
                    await btn.click()
                    return
            except Exception:
                pass
            # Кнопка следующего месяца в date-picker FB обычно называется так.
            next_btn = page.get_by_role(
                "button", name=re.compile(r"^(Next month|Следующий месяц)$")
            ).first
            if not (await next_btn.count() and await next_btn.is_visible()):
                break
            await next_btn.click()
            await human_wait(200, 400)
        raise RuntimeError(f"Не удалось найти день {aria!r} в календаре")

    async def _set_time(self, page: Page, *, hours: int, minutes: int) -> None:
        for name, value in (("часы", hours), ("минуты", minutes)):
            spin = page.get_by_role("spinbutton", name=name).first
            await spin.wait_for(state="visible", timeout=6000)
            await spin.scroll_into_view_if_needed()
            await human_wait(80, 180)
            await spin.click()
            await human_wait(60, 140)
            # Тройной клик выделяет всё значение, fill заменяет одной операцией.
            try:
                await spin.click(click_count=3, timeout=1500)
            except Exception:
                pass
            await human_wait(40, 100)
            await spin.fill(str(value))
            await human_wait(80, 160)
            await spin.press("Tab")
            await human_wait(120, 240)
            # FB хранит реальное значение в aria-valuenow; visible value может быть пустым.
            current_aria = await spin.get_attribute("aria-valuenow")
            if current_aria and current_aria.strip() != str(value):
                logger.warning("Поле %s ожидали %s, aria-valuenow=%r", name, value, current_aria)
