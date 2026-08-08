# -*- coding: utf-8 -*-
"""Unit tests for IANA cabinet-day boundaries and Meta timezone validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.dashboard.cabinet_spend import cabinet_day_start_utc
from core.meta_api.account_tz import (
    cabinet_day_end_for_timezone,
    cabinet_day_start_for_timezone,
    canonical_account_id,
    fetch_account_timezone,
    validated_timezone_name,
)


# UTC-кабинет: граница суток = полночь UTC того же дня.
def test_boundary_utc_offset_zero() -> None:
    now = datetime(2026, 6, 19, 14, 30, tzinfo=UTC)
    assert cabinet_day_start_utc(0.0, now) == datetime(2026, 6, 19, 0, 0, tzinfo=UTC)


# Калининград +2: полночь по локали = 22:00 UTC предыдущего дня.
def test_boundary_positive_offset() -> None:
    now = datetime(2026, 6, 19, 14, 30, tzinfo=UTC)
    # local = 16:30 19-го → local midnight = 00:00 19-го local = 22:00 18-го UTC
    assert cabinet_day_start_utc(2.0, now) == datetime(2026, 6, 18, 22, 0, tzinfo=UTC)


# Кабинет Hermosillo −7: ночь UTC попадает во «вчера» по локали кабинета.
def test_boundary_negative_offset() -> None:
    now = datetime(2026, 6, 19, 3, 0, tzinfo=UTC)
    # local = 20:00 18-го → local midnight = 00:00 18-го local = 07:00 18-го UTC
    assert cabinet_day_start_utc(-7.0, now) == datetime(2026, 6, 18, 7, 0, tzinfo=UTC)


# Дробный оффсет +5.5 (India): полночь по локали = 18:30 UTC предыдущего дня.
def test_boundary_fractional_offset() -> None:
    now = datetime(2026, 6, 19, 14, 30, tzinfo=UTC)
    assert cabinet_day_start_utc(5.5, now) == datetime(2026, 6, 18, 18, 30, tzinfo=UTC)


# Инвариант: для любого оффсета now ∈ [boundary, boundary + 24ч) — «сейчас» внутри текущих суток.
@pytest.mark.parametrize("offset", [-12.0, -7.0, 0.0, 2.0, 5.5, 13.0])
def test_now_within_cabinet_day(offset: float) -> None:
    now = datetime(2026, 6, 19, 11, 27, tzinfo=UTC)
    b = cabinet_day_start_utc(offset, now)
    assert b <= now < b + timedelta(days=1)


# Сразу после полуночи кабинета граница уже «сегодняшняя» (новые сутки начались).
def test_boundary_just_after_midnight() -> None:
    # offset 0, now = 00:01 UTC → граница = 00:00 того же дня (а не вчера).
    now = datetime(2026, 6, 19, 0, 1, tzinfo=UTC)
    assert cabinet_day_start_utc(0.0, now) == datetime(2026, 6, 19, 0, 0, tzinfo=UTC)


# --- Authoritative IANA account timezone ---


def test_account_id_and_iana_validation() -> None:
    assert canonical_account_id(" act_123 ") == "123"
    assert validated_timezone_name("Asia/Singapore") == "Asia/Singapore"
    assert validated_timezone_name("Definitely/Not-A-Timezone") is None


def test_iana_boundary_uses_offset_at_midnight_on_dst_transition() -> None:
    """New York noon is UTC-4, but midnight before spring shift was UTC-5."""
    now = datetime(2024, 3, 10, 12, 0, tzinfo=UTC)
    assert cabinet_day_start_for_timezone("America/New_York", now) == datetime(
        2024, 3, 10, 5, 0, tzinfo=UTC
    )
    assert cabinet_day_end_for_timezone("America/New_York", now) == datetime(
        2024, 3, 11, 4, 0, tzinfo=UTC
    )


class _FakeGraphClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    async def execute_graph_call(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_fetch_account_timezone_keeps_valid_iana_name() -> None:
    client = _FakeGraphClient({"timezone_offset_hours_utc": 8, "timezone_name": "Asia/Singapore"})
    timezone_name = await fetch_account_timezone(client, "act_123")
    assert timezone_name == "Asia/Singapore"
    assert client.calls[0]["endpoint"] == "/act_123"
    assert client.calls[0]["query_params"] == {"fields": "timezone_name"}


@pytest.mark.asyncio
async def test_fetch_account_timezone_rejects_numeric_offset_without_iana_name() -> None:
    client = _FakeGraphClient(
        {"timezone_offset_hours_utc": -7, "timezone_name": "Definitely/Not-A-Timezone"}
    )
    assert await fetch_account_timezone(client, "123") is None
