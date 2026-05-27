# -*- coding: utf-8 -*-
"""Unit-тесты pure-хелперов apps/digest_scheduler/main.py."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from apps.digest_scheduler.main import (
    DigestWindow,
    digest_sent_key,
    is_in_send_window,
)


# Окно начинается ровно в HH:MM — это «в окне»
def test_is_in_send_window_at_boundary() -> None:
    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, DigestWindow(hour=9, minute=0, window_minutes=5)) is True


# В пределах окна (через 3 минуты после старта) — ещё в окне
def test_is_in_send_window_inside() -> None:
    now = datetime(2026, 5, 27, 9, 3, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, DigestWindow(hour=9, minute=0, window_minutes=5)) is True


# Сразу после планового времени — окно открыто (catch-up до конца суток)
def test_is_in_send_window_just_after() -> None:
    now = datetime(2026, 5, 27, 9, 5, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, DigestWindow(hour=9, minute=0, window_minutes=5)) is True


# Раньше окна — не в окне
def test_is_in_send_window_before() -> None:
    now = datetime(2026, 5, 27, 8, 59, 0, tzinfo=timezone.utc)
    assert is_in_send_window(now, DigestWindow(hour=9, minute=0, window_minutes=5)) is False


# Сценарии catch-up: digest должен уйти от 09:00 до конца суток
def test_is_in_send_window_catchup_until_midnight() -> None:
    window = DigestWindow(hour=9, minute=0, window_minutes=5)
    # scheduler упал в 09:02, поднялся в 12:00 — окно ещё открыто
    assert is_in_send_window(datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc), window) is True
    # 23:59:59 UTC — последняя секунда «сегодня», окно ещё открыто
    assert is_in_send_window(datetime(2026, 5, 27, 23, 59, 0, tzinfo=timezone.utc), window) is True


# Окно с минутами: 09:30 — на 09:31 и после в окне (catch-up)
def test_is_in_send_window_with_minutes() -> None:
    window = DigestWindow(hour=9, minute=30, window_minutes=2)
    assert is_in_send_window(datetime(2026, 5, 27, 9, 31, 0, tzinfo=timezone.utc), window) is True
    assert is_in_send_window(datetime(2026, 5, 27, 9, 32, 0, tzinfo=timezone.utc), window) is True


# Naive datetime запрещён — функция требует timezone-aware
def test_is_in_send_window_rejects_naive() -> None:
    naive = datetime(2026, 5, 27, 9, 0, 0)
    with pytest.raises(ValueError):
        is_in_send_window(naive, DigestWindow(hour=9, minute=0, window_minutes=5))


# Не-UTC tzinfo приводится к UTC для расчёта
def test_is_in_send_window_converts_tz_to_utc() -> None:
    from datetime import timedelta as _td
    from datetime import timezone as _tz

    # 12:00 UTC+3 == 09:00 UTC → должно попасть в окно
    plus3 = _tz(_td(hours=3))
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=plus3)
    assert is_in_send_window(now, DigestWindow(hour=9, minute=0, window_minutes=5)) is True


# Ключ дедупа содержит дату в формате YYYY-MM-DD по UTC
def test_digest_sent_key_format() -> None:
    now = datetime(2026, 5, 27, 9, 0, 0, tzinfo=timezone.utc)
    assert digest_sent_key(now) == "digest:sent:2026-05-27"


# Naive datetime запрещён и в digest_sent_key
def test_digest_sent_key_rejects_naive() -> None:
    naive = datetime(2026, 5, 27, 9, 0, 0)
    with pytest.raises(ValueError):
        digest_sent_key(naive)


# Дата ключа — по UTC, не по локальному tz
def test_digest_sent_key_uses_utc() -> None:
    from datetime import timedelta as _td
    from datetime import timezone as _tz

    # 01:00 +3 == 22:00 предыдущего дня UTC
    plus3 = _tz(_td(hours=3))
    now = datetime(2026, 5, 28, 1, 0, 0, tzinfo=plus3)
    assert digest_sent_key(now) == "digest:sent:2026-05-27"
