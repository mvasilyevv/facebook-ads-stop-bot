# -*- coding: utf-8 -*-
"""Unit-тесты для health_watchdog."""

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

# Фиктивное время mtime для "свежего" лога (не вызывает alert)
_FRESH_MTIME = time.time()


def _fresh_stat(path: str) -> MagicMock:
    """Возвращает stat-результат с актуальным mtime (лог только что обновлён)."""
    s = MagicMock()
    s.st_mtime = _FRESH_MTIME
    return s


def _make_healthy_details() -> HealthDetails:
    """Возвращает полностью здоровый HealthDetails — все воркеры alive."""
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
            "health_watchdog": WorkerHealth(healthy=False),
        },
        browser_agent=ExternalServiceHealth(healthy=True),
        vision=ExternalServiceHealth(healthy=True),
        queues=QueueCounts(),
        last_successful_scan=LastScanInfo(),
    )


def _make_unhealthy_worker(worker_name: str, age: float = 200.0) -> HealthDetails:
    """Возвращает HealthDetails только с одним нездоровым воркером (остальные живые)."""
    details = _make_healthy_details()
    details.overall_healthy = False
    details.workers[worker_name] = WorkerHealth(healthy=False, heartbeat_age_seconds=age)
    return details


def _ctx_mock() -> MagicMock:
    """Возвращает async context manager mock для session factory."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# Все компоненты здоровы — watchdog не должен ничего делать
@pytest.mark.asyncio
async def test_healthy_no_actions():
    """Сценарий 1: overall_healthy=true — ни TG, ни supervisor вызовов нет."""
    healthy = _make_healthy_details()

    tg_mock = AsyncMock()
    tg_mock.send_message = AsyncMock()

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(return_value=healthy),
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()) as restart_mock,
        patch("apps.health_watchdog.main.os.stat", side_effect=_fresh_stat),
    ):
        from apps.health_watchdog.main import _run_iteration

        await _run_iteration(tg_mock, "chat123")

    tg_mock.send_message.assert_not_called()
    restart_mock.assert_not_called()


# Observer heartbeat устарел — должен прийти TG-алерт и supervisor restart
@pytest.mark.asyncio
async def test_observer_stale_heartbeat_triggers_restart():
    """Сценарий 2: observer heartbeat устарел → TG-алерт + supervisor stop+start."""
    from apps.health_watchdog.main import _cooldown

    _cooldown._last_sent.clear()

    unhealthy = _make_unhealthy_worker("observer", age=200.0)
    recovered = _make_healthy_details()

    tg_mock = AsyncMock()
    tg_mock.send_message = AsyncMock()

    call_count = 0

    async def _collect_side_effect(db):
        nonlocal call_count
        call_count += 1
        # Первый вызов — нездоровый, второй (после рестарта) — здоровый
        return unhealthy if call_count == 1 else recovered

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            side_effect=_collect_side_effect,
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()) as restart_mock,
        patch("apps.health_watchdog.main.asyncio.sleep", new=AsyncMock()),
        patch("apps.health_watchdog.main.os.stat", side_effect=_fresh_stat),
    ):
        from apps.health_watchdog.main import _run_iteration

        await _run_iteration(tg_mock, "chat123")

    tg_mock.send_message.assert_called()
    # Первый TG-алерт должен быть про observer
    first_text = tg_mock.send_message.call_args_list[0][1]["text"]
    assert "observer" in first_text.lower()
    restart_mock.assert_called_with("observer_worker")


# browser_agent gRPC нездоров — должен перезапуститься через supervisor
@pytest.mark.asyncio
async def test_browser_agent_unhealthy_triggers_restart():
    """Сценарий 3: browser_agent unhealthy → restart 'browser_agent' через supervisor."""
    from apps.health_watchdog.main import _cooldown

    _cooldown._last_sent.clear()

    details = _make_healthy_details()
    details.overall_healthy = False
    details.browser_agent = ExternalServiceHealth(healthy=False, error="connection refused")

    recovered = _make_healthy_details()

    call_count = 0

    async def _collect_side_effect(db):
        nonlocal call_count
        call_count += 1
        return details if call_count == 1 else recovered

    tg_mock = AsyncMock()
    tg_mock.send_message = AsyncMock()

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            side_effect=_collect_side_effect,
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()) as restart_mock,
        patch("apps.health_watchdog.main.asyncio.sleep", new=AsyncMock()),
        patch("apps.health_watchdog.main.os.stat", side_effect=_fresh_stat),
    ):
        from apps.health_watchdog.main import _run_iteration

        await _run_iteration(tg_mock, "chat123")

    tg_mock.send_message.assert_called()
    restart_mock.assert_called_with("browser_agent")


# Vision unhealthy — только TG-алерт, без рестарта
@pytest.mark.asyncio
async def test_vision_unhealthy_no_restart():
    """Сценарий 4: Vision unhealthy → только TG-алерт, без restart."""
    from apps.health_watchdog.main import _cooldown

    _cooldown._last_sent.clear()

    details = _make_healthy_details()
    details.overall_healthy = False
    details.vision = ExternalServiceHealth(healthy=False, error="timeout")

    tg_mock = AsyncMock()
    tg_mock.send_message = AsyncMock()

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(return_value=details),
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()) as restart_mock,
        patch("apps.health_watchdog.main.os.stat", side_effect=_fresh_stat),
    ):
        from apps.health_watchdog.main import _run_iteration

        await _run_iteration(tg_mock, "chat123")

    # Должен быть хотя бы один Vision-алерт
    assert tg_mock.send_message.called
    texts = [c[1]["text"] for c in tg_mock.send_message.call_args_list]
    assert any("Vision" in t for t in texts)
    restart_mock.assert_not_called()


# Повторный unhealthy в течение cooldown — второй TG-алерт не должен уйти
@pytest.mark.asyncio
async def test_cooldown_suppresses_duplicate_alert():
    """Сценарий 5: cooldown активен — повторный unhealthy не шлёт второй TG."""
    from apps.health_watchdog.main import _cooldown

    _cooldown._last_sent.clear()
    # Имитируем, что vision-алерт только что отправлен
    _cooldown._last_sent["alert:vision:unhealthy"] = time.monotonic()

    details = _make_healthy_details()
    details.overall_healthy = False
    details.vision = ExternalServiceHealth(healthy=False, error="timeout")

    tg_mock = AsyncMock()
    tg_mock.send_message = AsyncMock()

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(return_value=details),
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()),
        patch("apps.health_watchdog.main.os.stat", side_effect=_fresh_stat),
    ):
        from apps.health_watchdog.main import _run_iteration

        await _run_iteration(tg_mock, "chat123")

    tg_mock.send_message.assert_not_called()


# Лог не рос 5 минут — должен прийти алерт
@pytest.mark.asyncio
async def test_stale_log_triggers_alert():
    """Сценарий 6: лог-файл не обновлялся 5 минут → TG-алерт."""
    from apps.health_watchdog.main import _LOG_STALE_THRESHOLD, _cooldown

    _cooldown._last_sent.clear()

    # HealthDetails полностью здоровый — проверяем только лог-мониторинг
    healthy = _make_healthy_details()

    tg_mock = AsyncMock()
    tg_mock.send_message = AsyncMock()

    stale_mtime = time.time() - (_LOG_STALE_THRESHOLD + 60)

    def _stale_stat(path: str) -> MagicMock:
        s = MagicMock()
        s.st_mtime = stale_mtime
        return s

    with (
        patch(
            "apps.health_watchdog.main.collect_health_details",
            new=AsyncMock(return_value=healthy),
        ),
        patch(
            "apps.health_watchdog.main.get_session_factory",
            return_value=MagicMock(return_value=_ctx_mock()),
        ),
        patch("apps.health_watchdog.main.os.stat", side_effect=_stale_stat),
        patch("apps.health_watchdog.main.restart_via_supervisor", new=AsyncMock()),
    ):
        from apps.health_watchdog.main import _run_iteration

        await _run_iteration(tg_mock, "chat123")

    # Должен быть хотя бы один алерт о stale-логе
    assert tg_mock.send_message.called
    texts = [c[1]["text"] for c in tg_mock.send_message.call_args_list]
    assert any("не пишет" in t for t in texts)
