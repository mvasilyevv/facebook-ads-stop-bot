# -*- coding: utf-8 -*-
"""Unit-тесты для apps/health_watchdog/main.py — pure-функции."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from apps.health_watchdog.main import (
    DesyncedStopAd,
    StuckPauseTask,
    build_autostop_channel_alert,
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


# H4: money-критичные воркеры обязаны быть в дефолтном списке мониторинга
def test_default_expected_workers_covers_money_critical() -> None:
    from apps.health_watchdog.main import DEFAULT_EXPECTED_WORKERS

    workers = parse_expected_workers(DEFAULT_EXPECTED_WORKERS)
    for name in ("meta_api", "cabinet_scheduler", "tracker_aggregator", "observer"):
        assert name in workers, f"{name} должен мониториться (money-критичный)"
    # disable/enable удалены (DOM-канал) — их не должно быть в дефолте
    assert "disable" not in workers
    assert "enable" not in workers


# H4: дефолт watchdog согласован с health_details (UI и алертинг видят один набор;
# health_watchdog мониторит всё, КРОМЕ себя — если он сам мёртв, алертить некому)
def test_default_expected_workers_synced_with_health_details() -> None:
    from apps.api.routers.v1.health_details import _DEFAULT_EXPECTED_WORKERS
    from apps.health_watchdog.main import DEFAULT_EXPECTED_WORKERS

    watchdog = set(parse_expected_workers(DEFAULT_EXPECTED_WORKERS))
    details = set(_DEFAULT_EXPECTED_WORKERS)
    # health_details дополнительно содержит только health_watchdog (для UI-статуса)
    assert details - watchdog == {"health_watchdog"}, (
        f"рассинхрон watchdog↔health_details: {details ^ watchdog}"
    )


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


# ====================== канал авто-стопа: build_autostop_channel_alert ======================


# Оба триггера пусты (канал жив) → алерта нет
def test_autostop_alert_no_triggers_returns_none() -> None:
    assert build_autostop_channel_alert([], []) is None


# Только застрявшие задачи pause_ad → алерт с count, target_id, попытками и last_error
def test_autostop_alert_stuck_tasks_only() -> None:
    tasks = [
        StuckPauseTask(
            task_id=101,
            target_id="23001",
            attempt_count=16,
            age_minutes=42,
            last_error="Failed to fetch",
        )
    ]
    text = build_autostop_channel_alert(tasks, [])
    assert text is not None
    # Money-критичный CRITICAL-маркер и контекст канала
    assert "авто-стоп" in text.lower()
    assert "23001" in text  # target_id (fb_ad_id) пострадавшего объявления
    assert "16" in text  # число попыток
    assert "42" in text  # возраст застревания в минутах
    assert "Failed to fetch" in text  # диагностика (триггер 1 как обогащение)


# Только рассинхрон (stop_sent при ACTIVE) → алерт со списком fb_ad_id
def test_autostop_alert_desync_only() -> None:
    desynced = [DesyncedStopAd(fb_ad_id="987654", age_minutes=30)]
    text = build_autostop_channel_alert([], desynced)
    assert text is not None
    assert "987654" in text
    assert "30" in text
    # Рассинхрон-симптом: объявление в stop_sent, но крутится (ACTIVE)
    assert "stop_sent" in text or "ACTIVE" in text


# Оба триггера сразу → в тексте оба раздела
def test_autostop_alert_both_triggers() -> None:
    tasks = [
        StuckPauseTask(task_id=1, target_id="111", attempt_count=5, age_minutes=20, last_error=None)
    ]
    desynced = [DesyncedStopAd(fb_ad_id="222", age_minutes=25)]
    text = build_autostop_channel_alert(tasks, desynced)
    assert text is not None
    assert "111" in text
    assert "222" in text


# last_error=None не должен ронять рендер (например задача ещё ни разу не падала)
def test_autostop_alert_stuck_task_without_error() -> None:
    tasks = [
        StuckPauseTask(task_id=1, target_id="111", attempt_count=0, age_minutes=20, last_error=None)
    ]
    text = build_autostop_channel_alert(tasks, [])
    assert text is not None
    assert "111" in text


# Длинные списки усекаются (не раздуваем TG-сообщение), с пометкой «ещё N»
def test_autostop_alert_truncates_long_lists() -> None:
    tasks = [
        StuckPauseTask(
            task_id=i, target_id=f"ad{i}", attempt_count=3, age_minutes=20, last_error="x"
        )
        for i in range(25)
    ]
    text = build_autostop_channel_alert(tasks, [])
    assert text is not None
    # Общее число должно фигурировать целиком
    assert "25" in text
    # Но не все 25 строк перечислены поимённо — есть пометка про остаток
    assert "ещё" in text.lower()
    # Telegram-лимит сообщения 4096 — не превышаем
    assert len(text) < 4096
