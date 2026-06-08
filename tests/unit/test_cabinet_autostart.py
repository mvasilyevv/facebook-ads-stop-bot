# -*- coding: utf-8 -*-
"""Unit-тесты pure-хелперов автостарта кабинета (core/scheduler/cabinet_autostart.py)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core.scheduler.cabinet_autostart import (
    DEFAULT_CONFIG,
    _normalize_config,
    autostart_done_key,
    is_in_autostart_window,
)


# Окно открывается ровно в HH:MM — это «в окне»
def test_is_in_window_at_boundary() -> None:
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    assert is_in_autostart_window(now, 6, 0) is True


# Сразу после планового времени — окно открыто (catch-up до конца суток)
def test_is_in_window_after_target() -> None:
    now = datetime(2026, 5, 29, 9, 30, 0, tzinfo=timezone.utc)
    assert is_in_autostart_window(now, 6, 0) is True


# 23:59 UTC — последняя минута суток, окно ещё открыто (catch-up)
def test_is_in_window_until_midnight() -> None:
    now = datetime(2026, 5, 29, 23, 59, 0, tzinfo=timezone.utc)
    assert is_in_autostart_window(now, 6, 0) is True


# Раньше планового времени — НЕ в окне
def test_is_in_window_before_target() -> None:
    now = datetime(2026, 5, 29, 5, 59, 0, tzinfo=timezone.utc)
    assert is_in_autostart_window(now, 6, 0) is False


# Окно с минутами: 06:30 — на 06:29 ещё рано, на 06:30 уже в окне
def test_is_in_window_with_minutes() -> None:
    assert is_in_autostart_window(datetime(2026, 5, 29, 6, 29, tzinfo=timezone.utc), 6, 30) is False
    assert is_in_autostart_window(datetime(2026, 5, 29, 6, 30, tzinfo=timezone.utc), 6, 30) is True


# Не-UTC tz приводится к UTC: 09:00 +3 == 06:00 UTC → в окне для 06:00
def test_is_in_window_converts_tz() -> None:
    plus3 = timezone(timedelta(hours=3))
    now = datetime(2026, 5, 29, 9, 0, 0, tzinfo=plus3)
    assert is_in_autostart_window(now, 6, 0) is True


# Naive datetime запрещён — функция требует timezone-aware
def test_is_in_window_rejects_naive() -> None:
    with pytest.raises(ValueError):
        is_in_autostart_window(datetime(2026, 5, 29, 6, 0, 0), 6, 0)


# Ключ дедупа содержит дату YYYY-MM-DD по UTC
def test_done_key_format() -> None:
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    assert autostart_done_key(now) == "cabinet:autostart:2026-05-29"


# Дата ключа берётся по UTC: 01:00 +3 == 22:00 предыдущего дня UTC
def test_done_key_uses_utc() -> None:
    plus3 = timezone(timedelta(hours=3))
    now = datetime(2026, 5, 30, 1, 0, 0, tzinfo=plus3)
    assert autostart_done_key(now) == "cabinet:autostart:2026-05-29"


# Naive datetime запрещён и в autostart_done_key
def test_done_key_rejects_naive() -> None:
    with pytest.raises(ValueError):
        autostart_done_key(datetime(2026, 5, 29, 6, 0, 0))


# Пустой/None конфиг нормализуется в дефолты (фича выключена, дат нет)
def test_normalize_empty_returns_defaults() -> None:
    cfg = _normalize_config(None)
    assert cfg["enabled"] is False
    assert cfg["dates"] == []
    assert cfg["hour_utc"] == DEFAULT_CONFIG["hour_utc"]
    assert cfg["minute_utc"] == DEFAULT_CONFIG["minute_utc"]


# Нормализация чистит пустые строки в датах и приводит типы
def test_normalize_cleans_dates_and_types() -> None:
    cfg = _normalize_config(
        {"enabled": 1, "hour_utc": "7", "minute_utc": "15", "dates": ["22.05", " ", "25.05"]}
    )
    assert cfg["enabled"] is True
    assert cfg["hour_utc"] == 7
    assert cfg["minute_utc"] == 15
    assert cfg["dates"] == ["22.05", "25.05"]


# Кривой тип dates (не список) → пустой список (защита от падения)
def test_normalize_bad_dates_type() -> None:
    cfg = _normalize_config({"enabled": True, "dates": "22.05"})
    assert cfg["dates"] == []


# ====================== N6: ошибка Redis GET не пропускает день молча ==========


class _RaisingRedis:
    """fake Redis: .get падает (имитация недоступного Redis в окне автостарта)."""

    async def get(self, *_a, **_k):
        raise RuntimeError("redis down")


# N6: ошибка Redis GET дедуп-ключа → outcome 'redis_error' (retryable), НЕ 'already_done'.
# Иначе money-критичный автостарт молча пропускался бы на весь день при сбое Redis.
@pytest.mark.asyncio
async def test_redis_get_error_is_retryable_not_already_done(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    import apps.cabinet_scheduler.main as m

    monkeypatch.setattr(m, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        m,
        "read_autostart_config",
        AsyncMock(return_value={"enabled": True, "hour_utc": 6, "minute_utc": 0}),
    )
    now = datetime(2026, 5, 29, 9, 0, 0, tzinfo=timezone.utc)  # 09:00 UTC — в окне (после 6:00)

    summary = await m.run_one_tick(engine=object(), redis_client=_RaisingRedis(), now=now)

    # КЛЮЧЕВОЕ: НЕ 'already_done' (день не помечается выполненным) → след. тик повторит.
    assert summary["outcome"] == "redis_error"
