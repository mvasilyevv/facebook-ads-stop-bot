# -*- coding: utf-8 -*-
"""Тесты для гейта health_watchdog по флагу is_scanning_enabled."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.health_watchdog import main as watchdog


def _make_health(*, scanning_ok: bool = False) -> MagicMock:
    """Симулирует HealthDetails: vision/browser/observer лежат, БД и health_watchdog ок."""
    health = MagicMock()
    health.overall_healthy = scanning_ok
    health.database.healthy = True

    observer_h = MagicMock()
    observer_h.healthy = scanning_ok
    observer_h.heartbeat_age_seconds = 999

    health.workers = {
        "observer": observer_h,
        "health_watchdog": MagicMock(healthy=True),
    }
    health.browser_agent.healthy = scanning_ok
    health.browser_agent.error = "All connection attempts failed"
    health.vision.healthy = scanning_ok
    health.vision.error = "All connection attempts failed"
    health.queues.disable_running = 0
    health.queues.enable_running = 0
    return health


@pytest.mark.asyncio
async def test_iteration_suppresses_observer_and_vision_alerts_when_scanning_disabled(tmp_path):
    # При выключенном сканировании watchdog молчит про observer/Vision/browser_agent.
    tg = MagicMock()
    tg.send_message = AsyncMock()

    health = _make_health(scanning_ok=False)

    with (
        patch.object(watchdog, "collect_health_details", AsyncMock(return_value=health)),
        patch.object(watchdog, "_is_scanning_enabled", AsyncMock(return_value=False)),
        patch.object(watchdog, "_get_telegram_settings", AsyncMock(return_value=("123", None))),
        patch.object(watchdog, "_check_log_growth", return_value=True),
        patch.object(watchdog, "get_session_factory") as factory_mock,
    ):
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        session_cm.__aexit__ = AsyncMock(return_value=False)
        factory_mock.return_value = MagicMock(return_value=session_cm)

        await watchdog._run_iteration(tg, "123")

    tg.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_iteration_sends_vision_alert_when_scanning_enabled(tmp_path):
    # При включённом сканировании Vision-алерт уходит как раньше.
    tg = MagicMock()
    tg.send_message = AsyncMock()

    health = _make_health(scanning_ok=False)

    # Сбросим cooldown между тестами
    watchdog._cooldown = watchdog._AlertCooldown()

    with (
        patch.object(watchdog, "collect_health_details", AsyncMock(return_value=health)),
        patch.object(watchdog, "_is_scanning_enabled", AsyncMock(return_value=True)),
        patch.object(watchdog, "_get_telegram_settings", AsyncMock(return_value=("123", None))),
        patch.object(watchdog, "_check_log_growth", return_value=True),
        patch.object(watchdog, "restart_via_supervisor", AsyncMock()),
        patch.object(watchdog, "asyncio") as asyncio_mock,
        patch.object(watchdog, "get_session_factory") as factory_mock,
    ):
        asyncio_mock.sleep = AsyncMock()
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        session_cm.__aexit__ = AsyncMock(return_value=False)
        factory_mock.return_value = MagicMock(return_value=session_cm)

        await watchdog._run_iteration(tg, "123")

    sent_texts = [c.kwargs.get("text", "") for c in tg.send_message.call_args_list]
    assert any("Vision" in t for t in sent_texts)


@pytest.mark.asyncio
async def test_iteration_routes_ops_alerts_to_owner_dm():
    # Ops-алерты (Vision/browser_agent/queues/worker) уходят в личку владельца, не в супергруппу.
    tg = MagicMock()
    tg.send_message = AsyncMock()
    settings = MagicMock(spec=watchdog.TelegramSettings)
    settings.owner_telegram_user_id = "777"
    health = _make_health(scanning_ok=False)

    watchdog._cooldown = watchdog._AlertCooldown()

    with (
        patch.object(watchdog, "collect_health_details", AsyncMock(return_value=health)),
        patch.object(watchdog, "_is_scanning_enabled", AsyncMock(return_value=True)),
        patch.object(
            watchdog, "_get_telegram_settings", AsyncMock(return_value=("-100123", settings))
        ),
        patch.object(watchdog, "_check_log_growth", return_value=True),
        patch.object(watchdog, "restart_via_supervisor", AsyncMock()),
        patch.object(watchdog, "asyncio") as asyncio_mock,
        patch.object(watchdog, "get_session_factory") as factory_mock,
    ):
        asyncio_mock.sleep = AsyncMock()
        session_cm = MagicMock()
        session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
        session_cm.__aexit__ = AsyncMock(return_value=False)
        factory_mock.return_value = MagicMock(return_value=session_cm)

        await watchdog._run_iteration(tg, "-100123")

    chat_ids = {c.kwargs["chat_id"] for c in tg.send_message.call_args_list}
    assert chat_ids == {"777"}, f"ожидаем только личку владельца, получили {chat_ids}"
