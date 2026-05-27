# -*- coding: utf-8 -*-
"""Unit-тесты для apps/cleanup_worker/retention.py — парсинг retention строк."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from apps.cleanup_worker.retention import (
    RetentionParseError,
    cutoff_datetime,
    get_default_policy,
    is_special,
    parse_duration,
)


# Базовый кейс: правильно парсит "14 days"
def test_parse_duration_days() -> None:
    assert parse_duration("14 days") == timedelta(days=14)


# Hours и minutes тоже должны работать
def test_parse_duration_hours_minutes() -> None:
    assert parse_duration("12 hours") == timedelta(hours=12)
    assert parse_duration("30 minutes") == timedelta(minutes=30)


# Year/month — приближённо как 365d/30d
def test_parse_duration_year_month() -> None:
    assert parse_duration("1 year") == timedelta(days=365)
    assert parse_duration("3 months") == timedelta(days=90)


# Специальные значения должны бросать ошибку
def test_parse_duration_special_raises() -> None:
    for special in ["forever", "immediate", "redis_ttl_only"]:
        with pytest.raises(RetentionParseError):
            parse_duration(special)


# Невалидная строка — ошибка
def test_parse_duration_invalid() -> None:
    with pytest.raises(RetentionParseError):
        parse_duration("nonsense")
    with pytest.raises(RetentionParseError):
        parse_duration("14 lightyears")


# is_special корректно отличает специальные от обычных
def test_is_special() -> None:
    assert is_special("forever") is True
    assert is_special("immediate") is True
    assert is_special("redis_ttl_only") is True
    assert is_special("14 days") is False
    assert is_special("FOREVER") is True  # case-insensitive


# cutoff_datetime считает now - duration корректно
def test_cutoff_datetime() -> None:
    now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
    cutoff = cutoff_datetime("14 days", now=now)
    assert cutoff == datetime(2026, 5, 12, 12, 0, 0, tzinfo=timezone.utc)


# Дефолтная политика содержит все 17 ключей из спецификации DB_REDESIGN.md
def test_default_policy_keys() -> None:
    policy = get_default_policy()
    expected_keys = {
        "ad_library_scan",
        "ad_library_ad_orphan",
        "ad_library_snapshot",
        "ad_library_media_orphan",
        "ad_metrics",
        "alert_events",
        "scan_runs",
        "meta_api_audit_log",
        "meta_api_webhook_event",
        "tracker_postback",
        "task_queue_completed",
        "task_queue_failed",
        "enable_recommendations",
        "telegram_invites_expired",
        "cabinet_day_archives",
        "ad_library_winner_archive",
        "ai_cache",
    }
    assert set(policy.keys()) == expected_keys


# Дефолтная политика для winner_archive — "forever"
def test_default_policy_winner_forever() -> None:
    policy = get_default_policy()
    assert policy["ad_library_winner_archive"] == "forever"
    assert is_special(policy["ad_library_winner_archive"])
