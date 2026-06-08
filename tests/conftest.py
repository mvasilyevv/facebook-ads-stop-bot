# -*- coding: utf-8 -*-
"""Конфигурация тестов."""

import pytest

from core.config import get_settings


@pytest.fixture(autouse=True)
def _disable_api_key_auth(monkeypatch):
    """H-3: тесты не шлют X-API-Key — отключаем enforcement глобально.

    Прод secure-by-default (require_api_key=True). Enforcement как таковой
    проверяется отдельным unit-тестом test_api_key_auth.py со своим settings.
    """
    monkeypatch.setattr(get_settings(), "require_api_key", False, raising=False)


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

    yield
