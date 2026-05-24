# -*- coding: utf-8 -*-
"""Unit-тесты для реформы health-алертов: sleep-detector, гейт, batched, auto-resolve."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.api.routers.health import (
    DbHealth,
    ExternalServiceHealth,
    HealthDetails,
    LastScanInfo,
    QueueCounts,
    WorkerHealth,
)
from apps.health_watchdog import main as watchdog
from apps.health_watchdog.alert_state import AlertCooldown, IncidentTracker

# Свежий mtime для лога (не вызывает stale-алерт)
_FRESH_MTIME = time.time()


def _fresh_stat(path: str) -> MagicMock:
    s = MagicMock()
    s.st_mtime = _FRESH_MTIME
    return s


def _ctx_mock() -> MagicMock:
    """Мок async context manager для session factory."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _healthy_details() -> HealthDetails:
    return HealthDetails(
        overall_healthy=True,
        checked_at=datetime.now(UTC),
        database=DbHealth(healthy=True, latency_ms=1),
        workers={
            "observer": WorkerHealth(healthy=True, heartbeat_age_seconds=5.0),
            "telegram_poller": WorkerHealth(healthy=True, heartbeat_age_seconds=5.0),
            "disable": WorkerHealth(healthy=True, heartbeat_age_seconds=5.0),
            "enable": WorkerHealth(healthy=True, heartbeat_age_seconds=5.0),
            "enable_recommendation": WorkerHealth(healthy=True, heartbeat_age_seconds=5.0),
            "health_watchdog": WorkerHealth(healthy=True, heartbeat_age_seconds=5.0),
        },
        browser_agent=ExternalServiceHealth(healthy=True),
        vision=ExternalServiceHealth(healthy=True),
        queues=QueueCounts(),
        last_successful_scan=LastScanInfo(),
    )


def _reset_state() -> None:
    """Сбрасывает глобальный state watchdog между тестами."""
    watchdog._cooldown = watchdog._AlertCooldown()
    watchdog._incidents = IncidentTracker()
    watchdog._reset_sleep_detector()


# Сценарий 1: sleep-detector триггерится при wall-clock jump > 90с
def test_sleep_detector_triggers_on_jump():
    _reset_state()

    fake_times = iter([100.0, 300.0])  # jump 200с > 90с порога
    with patch("apps.health_watchdog.main.time.monotonic", side_effect=lambda: next(fake_times)):
        # Первый вызов: инициализирует state, возвращает False.
        assert watchdog._detect_wake_from_sleep() is False
        # Второй вызов: jump 200с — должен вернуть True.
        assert watchdog._detect_wake_from_sleep() is True


# Сценарий 2: normal interval (30с) не триггерит sleep-detector
def test_sleep_detector_normal_interval():
    _reset_state()
    fake_times = iter([100.0, 130.0, 160.0])  # +30с, +30с — норма
    with patch("apps.health_watchdog.main.time.monotonic", side_effect=lambda: next(fake_times)):
        assert watchdog._detect_wake_from_sleep() is False
        assert watchdog._detect_wake_from_sleep() is False
        assert watchdog._detect_wake_from_sleep() is False


# Сценарий 3: _run_iteration при detected sleep пропускает работу и сбрасывает cooldown
@pytest.mark.asyncio
async def test_run_iteration_skips_on_sleep_and_resets_cooldown():
    _reset_state()
    # Засеваем cooldown — должен быть очищен после sleep-detect
    watchdog._cooldown.mark_sent("alert:dummy:key")

    tg = AsyncMock()
    tg.send_message = AsyncMock()

    with (
        patch("apps.health_watchdog.main._detect_wake_from_sleep", return_value=True),
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(),
        ) as collect_mock,
    ):
        await watchdog._run_iteration(tg, "chat")

    collect_mock.assert_not_called()
    tg.send_message.assert_not_called()
    # Cooldown сброшен — следующий алерт должен пройти
    assert watchdog._cooldown.can_send("alert:dummy:key") is True


# Сценарий 4: при is_scanning_enabled=False heartbeat_stale алерт для disable/enable/telegram_poller
# не отправляется.
@pytest.mark.asyncio
async def test_scanning_disabled_suppresses_all_workers():
    _reset_state()

    details = _healthy_details()
    details.overall_healthy = False
    # Делаем нескольких воркеров нездоровыми
    details.workers["disable"] = WorkerHealth(healthy=False, heartbeat_age_seconds=300.0)
    details.workers["enable"] = WorkerHealth(healthy=False, heartbeat_age_seconds=300.0)
    details.workers["telegram_poller"] = WorkerHealth(healthy=False, heartbeat_age_seconds=300.0)
    details.browser_agent = ExternalServiceHealth(healthy=False, error="down")
    details.vision = ExternalServiceHealth(healthy=False, error="down")

    tg = AsyncMock()
    tg.send_message = AsyncMock()

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(return_value=details),
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch(
            "apps.health_watchdog.main._is_scanning_enabled",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "apps.health_watchdog.main._get_telegram_settings",
            new=AsyncMock(return_value=("chat", None)),
        ),
        patch("apps.health_watchdog.main._check_log_growth", return_value=True),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()),
        patch("apps.health_watchdog.main.send_telegram_via_queue", new=AsyncMock()) as send_mock,
    ):
        await watchdog._run_iteration(tg, "chat")

    # send_telegram_via_queue ни разу не вызван — все алерты подавлены гейтом
    send_mock.assert_not_called()
    tg.send_message.assert_not_called()


# Сценарий 5: batched summary — 3 одновременных проблемы → 1 сообщение со списком 3 буллетов
@pytest.mark.asyncio
async def test_batched_summary_for_multiple_incidents():
    _reset_state()

    details = _healthy_details()
    details.overall_healthy = False
    details.workers["observer"] = WorkerHealth(healthy=False, heartbeat_age_seconds=200.0)
    details.browser_agent = ExternalServiceHealth(healthy=False, error="grpc fail")
    details.vision = ExternalServiceHealth(healthy=False, error="timeout 5s")

    tg = AsyncMock()
    tg.send_message = AsyncMock()

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(return_value=details),
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch(
            "apps.health_watchdog.main._is_scanning_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "apps.health_watchdog.main._get_telegram_settings",
            new=AsyncMock(return_value=("chat", None)),
        ),
        patch("apps.health_watchdog.main._check_log_growth", return_value=True),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()),
        patch("apps.health_watchdog.main.send_telegram_via_queue", new=AsyncMock()) as send_mock,
    ):
        await watchdog._run_iteration(tg, "chat")

    # Должен быть ровно один summary-алерт + опционально escalation-сообщения после рестарта.
    # Главная проверка: первое сообщение содержит 3 буллета.
    assert send_mock.call_count >= 1
    first_call_text = send_mock.call_args_list[0].kwargs["text"]
    assert "Система не в порядке" in first_call_text
    assert first_call_text.count("• ") == 3


# Сценарий 6: одна проблема → классический формат без буллетов
@pytest.mark.asyncio
async def test_single_incident_uses_classic_format():
    _reset_state()

    details = _healthy_details()
    details.overall_healthy = False
    details.vision = ExternalServiceHealth(healthy=False, error="timeout")

    tg = AsyncMock()
    tg.send_message = AsyncMock()

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(return_value=details),
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch(
            "apps.health_watchdog.main._is_scanning_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "apps.health_watchdog.main._get_telegram_settings",
            new=AsyncMock(return_value=("chat", None)),
        ),
        patch("apps.health_watchdog.main._check_log_growth", return_value=True),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()),
        patch("apps.health_watchdog.main.send_telegram_via_queue", new=AsyncMock()) as send_mock,
    ):
        await watchdog._run_iteration(tg, "chat")

    assert send_mock.call_count >= 1
    first_text = send_mock.call_args_list[0].kwargs["text"]
    # Классический формат: содержит "Vision" и описание, но не "Система не в порядке"
    assert "Vision" in first_text
    assert "Система не в порядке" not in first_text


# Сценарий 7: auto-resolve — компонент unhealthy → healthy → летит resolve и cooldown сброшен.
@pytest.mark.asyncio
async def test_auto_resolve_sends_recovery_message():
    _reset_state()
    # Симулируем что vision-инцидент уже открыт
    watchdog._incidents.mark_open("alert:vision:unhealthy")
    watchdog._cooldown.mark_sent("alert:vision:unhealthy")

    details = _healthy_details()  # всё healthy сейчас

    tg = AsyncMock()
    tg.send_message = AsyncMock()

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(return_value=details),
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch(
            "apps.health_watchdog.main._is_scanning_enabled",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "apps.health_watchdog.main._get_telegram_settings",
            new=AsyncMock(return_value=("chat", None)),
        ),
        patch("apps.health_watchdog.main._check_log_growth", return_value=True),
        patch("apps.health_watchdog.main.send_telegram_via_queue", new=AsyncMock()) as send_mock,
    ):
        await watchdog._run_iteration(tg, "chat")

    # Должно прийти ровно одно сообщение — "✅ Vision API снова в порядке"
    send_mock.assert_called_once()
    text = send_mock.call_args.kwargs["text"]
    assert "✅" in text
    assert "Vision API" in text
    # Инцидент закрыт
    assert watchdog._incidents.is_open("alert:vision:unhealthy") is False
    # Cooldown сброшен — следующий инцидент уйдёт без задержки
    assert watchdog._cooldown.can_send("alert:vision:unhealthy") is True


# Сценарий 8: bin/supervisor_crashmail._is_scanning_enabled_sync — failsafe при ошибке БД
def test_supervisor_crashmail_failsafe_on_db_error():
    """Если asyncio.run выкидывает ошибку, _is_scanning_enabled_sync возвращает True."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
    from supervisor_crashmail import _is_scanning_enabled_sync

    fake_settings = MagicMock()
    fake_settings.postgres_host = "x"
    fake_settings.postgres_port = 1
    fake_settings.postgres_user = "x"
    fake_settings.postgres_password = "x"
    fake_settings.postgres_db = "x"

    with (
        patch("core.config.get_settings", return_value=fake_settings),
        patch("supervisor_crashmail.asyncio.run", side_effect=OSError("connection failed")),
    ):
        result = _is_scanning_enabled_sync()
    assert result is True


# Сценарий 8б: bin/supervisor_crashmail._is_scanning_enabled_sync — возвращает False
# когда asyncio.run возвращает False (поле в БД == False).
def test_supervisor_crashmail_returns_false_when_scanning_off():
    """Когда asyncio.run возвращает False, функция возвращает False (алерт нужно подавить)."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bin"))
    from supervisor_crashmail import _is_scanning_enabled_sync

    fake_settings = MagicMock()
    fake_settings.postgres_host = "x"
    fake_settings.postgres_port = 5433
    fake_settings.postgres_user = "x"
    fake_settings.postgres_password = "x"
    fake_settings.postgres_db = "x"

    with (
        patch("core.config.get_settings", return_value=fake_settings),
        patch("supervisor_crashmail.asyncio.run", return_value=False),
    ):
        result = _is_scanning_enabled_sync()
    assert result is False


# Сценарий 9: alert_state.AlertCooldown.reset_all очищает все ключи
def test_alert_cooldown_reset_all():
    cd = AlertCooldown(cooldown_seconds=60)
    cd.mark_sent("key_a")
    cd.mark_sent("key_b")
    assert cd.can_send("key_a") is False
    cd.reset_all()
    assert cd.can_send("key_a") is True
    assert cd.can_send("key_b") is True


# Сценарий 10: IncidentTracker — mark_open / resolve / is_open
def test_incident_tracker_lifecycle():
    tracker = IncidentTracker()
    assert tracker.is_open("k1") is False

    tracker.mark_open("k1")
    assert tracker.is_open("k1") is True
    assert "k1" in tracker.open_keys()

    assert tracker.resolve("k1") is True  # первый resolve возвращает True
    assert tracker.is_open("k1") is False
    assert tracker.resolve("k1") is False  # повторный — False (нечего закрывать)
