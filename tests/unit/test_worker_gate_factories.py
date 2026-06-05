# -*- coding: utf-8 -*-
"""Анти-регресс на прод gate-фабрику observer-воркера.

Контекст: фабрика звала `BrowserAgentClient()` без config + несуществующий `.connect()`.
Существующие тесты мокали gate целиком, поэтому баг не ловился и воркер падал бы
на первой реальной задаче. Тесты проверяют контракт создания клиента:
конструктор получает config, вызывается start(), connect() НЕ вызывается.
(DOM disable/enable воркеры удалены — действие через Marketing API.)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_client_and_ctor() -> tuple[AsyncMock, MagicMock]:
    """Фейковый BrowserAgentClient: start() — корректный путь, connect() — запрещён."""
    fake_client = AsyncMock()
    fake_client.connect = AsyncMock(side_effect=AssertionError("connect() не должен вызываться"))
    fake_ctor = MagicMock(return_value=fake_client)
    return fake_client, fake_ctor


_FAKE_SETTINGS = MagicMock(
    vision_x_token="tok", vision_api_url="http://vision", vision_profile_id="pid"
)


# observer: _default_gate_factory собирает клиента с config, зовёт start(), отдаёт обёртку
async def test_observer_default_gate_factory_uses_config_and_start() -> None:
    from apps.observer_worker.main import _default_gate_factory

    fake_client, fake_ctor = _fake_client_and_ctor()
    with (
        patch("clients.python_grpc.client.BrowserAgentClient", fake_ctor),
        patch("clients.python_grpc.client.BrowserAgentConfig", MagicMock()),
        patch("core.config.get_settings", return_value=_FAKE_SETTINGS),
    ):
        gate = await _default_gate_factory()

    fake_ctor.assert_called_once()
    fake_client.start.assert_awaited_once()
    # Фабрика возвращает ScannerGate-обёртку с методом run_one_scan
    assert hasattr(gate, "run_one_scan")


# Прямой контроль: у BrowserAgentClient есть start(), но НЕТ connect() — источник бывшего бага
def test_browser_agent_client_has_start_not_connect() -> None:
    from clients.python_grpc.client import BrowserAgentClient

    assert hasattr(BrowserAgentClient, "start")
    assert not hasattr(BrowserAgentClient, "connect"), (
        "Если connect() появился — обнови фабрики; раньше они звали несуществующий connect()"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
