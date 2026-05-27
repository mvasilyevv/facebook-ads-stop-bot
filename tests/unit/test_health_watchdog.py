# -*- coding: utf-8 -*-
"""Unit-тесты для apps/health_watchdog/main.py — pure-функции."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from apps.health_watchdog.main import (
    check_observer_runtime_freshness,
    parse_expected_workers,
    should_alert,
)


# Базовый случай: CSV → нормализованный список без дубликатов и пустых
def test_parse_expected_workers_basic() -> None:
    assert parse_expected_workers("observer,disable,enable") == [
        "observer",
        "disable",
        "enable",
    ]


# Пробелы и пустые элементы отбрасываются, порядок сохраняется
def test_parse_expected_workers_strips_and_dedups() -> None:
    assert parse_expected_workers(" observer , , disable, observer , enable ") == [
        "observer",
        "disable",
        "enable",
    ]


# None и пустая строка дают пустой список
def test_parse_expected_workers_empty() -> None:
    assert parse_expected_workers(None) == []
    assert parse_expected_workers("") == []
    assert parse_expected_workers("   ") == []


# Нет ключа observer:runtime → stale=True с reason="missing"
def test_check_observer_runtime_freshness_missing() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    is_stale, reason = check_observer_runtime_freshness(None, now=now)
    assert is_stale is True
    assert reason == "missing"


# Свежий updated_at (1 минута назад) → not stale
def test_check_observer_runtime_freshness_fresh() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    updated = (now - timedelta(minutes=1)).isoformat()
    payload = json.dumps({"worker_status": "scanning", "updated_at": updated})
    is_stale, reason = check_observer_runtime_freshness(payload, now=now)
    assert is_stale is False
    assert reason is None


# updated_at старше 5 минут → stale с информативным reason
def test_check_observer_runtime_freshness_stale() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    updated = (now - timedelta(minutes=15)).isoformat()
    payload = json.dumps({"worker_status": "idle", "updated_at": updated})
    is_stale, reason = check_observer_runtime_freshness(payload, now=now)
    assert is_stale is True
    assert reason is not None
    assert "15" in reason


# Невалидный JSON → stale с reason="invalid_json"
def test_check_observer_runtime_freshness_invalid_json() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    is_stale, reason = check_observer_runtime_freshness("{not valid", now=now)
    assert is_stale is True
    assert reason == "invalid_json"


# Нет updated_at в JSON → stale
def test_check_observer_runtime_freshness_no_updated_at() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    payload = json.dumps({"worker_status": "scanning"})
    is_stale, reason = check_observer_runtime_freshness(payload, now=now)
    assert is_stale is True
    assert reason == "missing_updated_at"


# naive datetime в payload должен интерпретироваться как UTC
def test_check_observer_runtime_freshness_naive_datetime_is_utc() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    naive = (now - timedelta(minutes=1)).replace(tzinfo=None).isoformat()
    payload = json.dumps({"worker_status": "scanning", "updated_at": naive})
    is_stale, reason = check_observer_runtime_freshness(payload, now=now)
    assert is_stale is False
    assert reason is None


# Кастомный max_age_seconds — короче дефолта
def test_check_observer_runtime_freshness_custom_max_age() -> None:
    now = datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc)
    updated = (now - timedelta(seconds=90)).isoformat()
    payload = json.dumps({"worker_status": "scanning", "updated_at": updated})
    is_stale, _ = check_observer_runtime_freshness(payload, now=now, max_age_seconds=60)
    assert is_stale is True


# heartbeat есть → не алертим
def test_should_alert_heartbeat_alive() -> None:
    assert should_alert("alive", None) is False


# heartbeat истёк + дедуп уже стоит → не алертим повторно
def test_should_alert_dedup_active() -> None:
    assert should_alert(None, "1") is False


# heartbeat истёк + дедупа нет → алертим
def test_should_alert_heartbeat_dead_and_no_dedup() -> None:
    assert should_alert(None, None) is True
