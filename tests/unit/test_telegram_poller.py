# -*- coding: utf-8 -*-
"""Тесты для Telegram poller: обработка update и управление offset."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


# Тест: offset должен сохраняться после успешно обработанного update
@pytest.mark.asyncio
async def test_poller_keeps_offset_after_failed_update():
    """Неудача в одном update не должна сдвигать offset дальше него."""
    from apps.telegram_poller.main import poller_loop

    updates = [{"update_id": 10}, {"update_id": 11}]
    client = SimpleNamespace(
        get_updates=AsyncMock(side_effect=[updates, asyncio.CancelledError()]),
    )

    async def fake_handle_update(_client, update):
        if update["update_id"] == 11:
            raise RuntimeError("временная ошибка")

    with patch("apps.telegram_poller.main.handle_update", new=fake_handle_update):
        with pytest.raises(asyncio.CancelledError):
            await poller_loop(client)

    assert client.get_updates.await_args_list[0].kwargs["offset"] is None
    assert client.get_updates.await_args_list[1].kwargs["offset"] == 11
