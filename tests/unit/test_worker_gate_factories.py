# -*- coding: utf-8 -*-
"""Анти-регресс на прод gate-фабрики observer/disable/enable воркеров.

Контекст: фабрики звали `BrowserAgentClient()` без config + несуществующий `.connect()`.
Существующие тесты мокали gate целиком, поэтому баг не ловился и воркеры падали бы
на первой реальной задаче. Эти тесты проверяют контракт создания клиента:
конструктор получает config, вызывается start(), connect() НЕ вызывается.
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


# disable/enable: общий _make_browser_gate должен собрать клиента с config и вызвать start()
async def test_disable_make_browser_gate_uses_config_and_start() -> None:
    from apps.disable_worker.main import _make_browser_gate

    fake_client, fake_ctor = _fake_client_and_ctor()
    with (
        patch("clients.python_grpc.client.BrowserAgentClient", fake_ctor),
        patch("clients.python_grpc.client.BrowserAgentConfig", MagicMock()),
        patch("core.config.get_settings", return_value=_FAKE_SETTINGS),
    ):
        gate = await _make_browser_gate()

    assert gate is fake_client
    fake_ctor.assert_called_once()  # вызван с config-объектом, не пустой
    assert fake_ctor.call_args.args, "BrowserAgentClient должен получить config позиционно"
    fake_client.start.assert_awaited_once()


# enable_worker переиспользует _make_browser_gate из disable_worker — контракт тот же
async def test_enable_worker_reuses_disable_gate_factory() -> None:
    from apps.disable_worker.main import _make_browser_gate as disable_gate
    from apps.enable_worker.main import _make_browser_gate as enable_gate

    assert enable_gate is disable_gate


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
