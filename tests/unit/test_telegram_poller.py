# -*- coding: utf-8 -*-
"""Тесты для Telegram poller: устойчивость offset и runtime-ротация токена."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# Проверяем, что битый update не зацикливает poller на одном и том же offset.
@pytest.mark.asyncio
async def test_poller_advances_offset_after_failed_update():
    """Даже при ошибке handle_update _process_updates должен переходить к следующему update_id."""
    from apps.telegram_poller.main import _process_updates

    updates = [{"update_id": 10}, {"update_id": 11}]
    client = SimpleNamespace(
        get_updates=AsyncMock(return_value=updates),
        answer_callback_query=AsyncMock(),
    )

    async def fake_handle_update(_client, update):
        if update["update_id"] == 11:
            raise RuntimeError("битый callback")

    with patch("apps.telegram_poller.main.handle_update", new=fake_handle_update):
        new_offset = await _process_updates(client, offset=None)

    assert client.get_updates.await_args.kwargs["offset"] is None
    assert new_offset == 12


class _FakeRuntimeClient:
    """Фейковый клиент poller-а с управляемым сценарием get_updates."""

    def __init__(self, token: str, *, updates_side_effect) -> None:
        self.token = token
        self.get_updates = AsyncMock(side_effect=updates_side_effect)
        self.close = AsyncMock()


# Проверяем, что poller может дождаться токена и не требует ручного рестарта.
@pytest.mark.asyncio
async def test_poller_runtime_waits_for_token_and_then_starts_polling():
    """Runtime-цикл должен пережить пустой токен и начать polling после его появления."""
    from apps.telegram_poller import main as poller_main

    token_loader = AsyncMock(side_effect=["", "token-after-start"])
    created_clients: list[_FakeRuntimeClient] = []

    def client_factory(token: str):
        client = _FakeRuntimeClient(
            token,
            updates_side_effect=[asyncio.CancelledError()],
        )
        created_clients.append(client)
        return client

    with (
        patch.object(poller_main, "touch_poller_heartbeat", new=AsyncMock()) as heartbeat,
        patch.object(poller_main.asyncio, "sleep", new=AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await poller_main.poller_runtime_loop(
                token_loader=token_loader,
                client_factory=client_factory,
                reload_interval_seconds=0,
            )

    assert token_loader.await_count >= 2
    assert len(created_clients) == 1
    assert created_clients[0].token == "token-after-start"
    created_clients[0].get_updates.assert_awaited_once_with(offset=None)
    heartbeat.assert_awaited()


# Проверяем, что poller закрывает старый клиент при ротации bot token.
@pytest.mark.asyncio
async def test_poller_runtime_rotates_client_when_token_changes():
    """При смене токена poller должен закрыть старый клиент и открыть новый."""
    from apps.telegram_poller import main as poller_main

    token_loader = AsyncMock(side_effect=["token-a", "token-b"])
    created_clients: list[_FakeRuntimeClient] = []

    def client_factory(token: str):
        if token == "token-a":
            client = _FakeRuntimeClient(token, updates_side_effect=[[]])
        else:
            client = _FakeRuntimeClient(token, updates_side_effect=[asyncio.CancelledError()])
        created_clients.append(client)
        return client

    with patch.object(poller_main, "touch_poller_heartbeat", new=AsyncMock()):
        with pytest.raises(asyncio.CancelledError):
            await poller_main.poller_runtime_loop(
                token_loader=token_loader,
                client_factory=client_factory,
                reload_interval_seconds=0,
            )

    assert [client.token for client in created_clients] == ["token-a", "token-b"]
    created_clients[0].close.assert_awaited_once()
    created_clients[1].get_updates.assert_awaited_once_with(offset=None)
