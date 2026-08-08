# -*- coding: utf-8 -*-
"""Unit-тесты catch-up логики is_in_send_window.

Поведение после фикса MID #17:
- target_time <= now < конец суток UTC → True
- now < target_time → False
- следующие сутки (по UTC) — False
"""

from __future__ import annotations

from datetime import datetime, timezone

from apps.digest_scheduler.main import DigestWindow, is_in_send_window

_WINDOW = DigestWindow(hour=9, minute=0)


# В 12:00 после 09:00 — окно ещё открыто (catch-up)
def test_catchup_at_noon_when_target_was_9() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, _WINDOW) is True


# 23:55 UTC — последние минуты «сегодняшнего» окна
def test_catchup_at_late_evening() -> None:
    now = datetime(2026, 5, 27, 23, 55, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, _WINDOW) is True


# 00:01 следующего дня UTC — окно «вчерашнего» дня уже закрылось.
def test_no_catchup_after_midnight() -> None:
    now = datetime(2026, 5, 28, 0, 1, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, _WINDOW) is False


# До планового времени окно закрыто
def test_before_target_time_is_closed() -> None:
    now = datetime(2026, 5, 27, 8, 59, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, _WINDOW) is False


# Edge: ровно в момент target — True (включительно)
def test_exactly_at_target_is_open() -> None:
    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, _WINDOW) is True
