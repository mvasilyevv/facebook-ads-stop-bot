# -*- coding: utf-8 -*-
"""Unit-тесты parse_spy_args + format_short_summary (без БД)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from core.ad_library.spy_handler import (
    SpyRequest,
    format_short_summary,
    parse_spy_args,
)


# Базовый: 2 токена — slot и country
def test_parse_basic() -> None:
    req = parse_spy_args("aviator KE")
    assert isinstance(req, SpyRequest)
    assert req.slot == "aviator"
    assert req.country == "KE"


# Multi-word slot: country = последний токен
def test_parse_multi_word_slot() -> None:
    req = parse_spy_args("chicken road 2 KE")
    assert isinstance(req, SpyRequest)
    assert req.slot == "chicken road 2"
    assert req.country == "KE"


# Country приводится к UPPER
def test_parse_country_uppercase() -> None:
    req = parse_spy_args("aviator ke")
    assert isinstance(req, SpyRequest)
    assert req.country == "KE"


# Пустая строка → текст ошибки
def test_parse_empty() -> None:
    result = parse_spy_args("")
    assert isinstance(result, str)
    assert "Использование" in result


# Один токен → ошибка
def test_parse_one_token() -> None:
    result = parse_spy_args("aviator")
    assert isinstance(result, str)
    assert "минимум 2" in result


# Country не ISO-2 → ошибка
def test_parse_invalid_country() -> None:
    result = parse_spy_args("aviator XYZ")
    assert isinstance(result, str)
    assert "ISO-2" in result


# Format summary при пустом pool — честный пустой ответ
def test_format_empty_pool() -> None:
    @dataclass
    class FakeScan:
        slot: str = "aviator"
        country: str = "KE"
        ads_count: int = 0
        duration_ms: int = 1000
        status: str = "done"
        scan_id: uuid.UUID = uuid.uuid4()
        error: str | None = None

    @dataclass
    class FakeResult:
        scan: FakeScan
        tier_counts: dict = None
        report: dict = None
        media_counts: dict = None
        enriched: int = 0
        error: str | None = None

    summary = format_short_summary(FakeResult(scan=FakeScan()))
    assert "Пусто" in summary
    assert "aviator" in summary


# Format summary при failed → показывает ошибку
def test_format_failed_scan() -> None:
    @dataclass
    class FakeScan:
        slot: str = "aviator"
        country: str = "KE"
        ads_count: int = 0
        duration_ms: int = 0
        status: str = "failed"
        scan_id: uuid.UUID = uuid.uuid4()
        error: str = "session not found"

    @dataclass
    class FakeResult:
        scan: FakeScan
        tier_counts: dict = None
        report: dict = None
        media_counts: dict = None
        enriched: int = 0
        error: str | None = None

    summary = format_short_summary(FakeResult(scan=FakeScan()))
    assert "❌" in summary
    assert "session not found" in summary
