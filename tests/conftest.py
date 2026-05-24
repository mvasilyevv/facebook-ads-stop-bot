# -*- coding: utf-8 -*-
"""Конфигурация тестов."""

from unittest.mock import AsyncMock

import pytest

# Подменяем send_telegram_via_queue во всех местах импорта: тесты, мокающие
# tg_client, не должны ждать Redis-таймаут (2-7 сек на каждом алёрте).
_QUEUE_PATCH_TARGETS = (
    "core.alerts.send.send_telegram_via_queue",
    "core.alerts.send_telegram_via_queue",
    "apps.health_watchdog.main.send_telegram_via_queue",
    "core.observer.self_healing.send_telegram_via_queue",
    "bin.supervisor_crashmail.send_telegram_via_queue",
)


@pytest.fixture(autouse=True)
def _fast_test_env(monkeypatch, request):
    # В самих тестах очереди не патчим — там проверяется реальное поведение.
    if "test_alerts_queue" in str(request.fspath):
        yield
        return

    async def _direct_send(chat_id, text, *, fallback_client=None, **kwargs):
        if fallback_client is None:
            return
        await fallback_client.send_message(chat_id=chat_id, text=text, **kwargs)

    for target in _QUEUE_PATCH_TARGETS:
        try:
            monkeypatch.setattr(target, _direct_send, raising=False)
        except (AttributeError, ImportError, ModuleNotFoundError):
            pass

    # Глобально подменяем asyncio.sleep в health_watchdog: там ждёт 60 сек
    # перед verify-после-restart, что зашкаливает любой тестовый timeout.
    try:
        monkeypatch.setattr("apps.health_watchdog.main.asyncio.sleep", AsyncMock(), raising=False)
    except (AttributeError, ImportError, ModuleNotFoundError):
        pass

    yield
