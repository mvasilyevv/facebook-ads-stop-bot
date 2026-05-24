# -*- coding: utf-8 -*-
"""Тесты авторизации callback-кнопок disable_confirm / disable_execute."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.domain import TelegramUserRole


def _callback_update(*, data: str, message_id: int = 41) -> dict:
    """Формирует Telegram callback_query update для теста."""
    return {
        "callback_query": {
            "id": "cb-test",
            "data": data,
            "message": {
                "chat": {"id": "-1003701505954", "type": "supergroup"},
                "message_id": message_id,
                "message_thread_id": 13,
            },
            "from": {"id": 99, "username": "intruder"},
        }
    }


# Recipient не должен иметь права создать DisableTask через disable_confirm.
@pytest.mark.asyncio
async def test_disable_confirm_blocked_for_non_owner_recipient():
    """Non-owner recipient получает OWNER_ONLY текст, _create_disable_task не дергается."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.RECIPIENT.value)

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_render_disable_confirm",
            new=AsyncMock(return_value=("never", {"inline_keyboard": []})),
        ) as render_confirm,
        patch.object(
            bot_handler,
            "_create_disable_task",
            new=AsyncMock(return_value={"fb_ad_id": "ad-1", "ad_name": "x"}),
        ) as create_task,
    ):
        await bot_handler.handle_update(client, _callback_update(data="disable_confirm:token-x"))

    render_confirm.assert_not_awaited()
    create_task.assert_not_awaited()
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.kwargs["text"] == bot_handler.OWNER_ONLY_TEXT


# Recipient не должен иметь права выполнить disable_execute.
@pytest.mark.asyncio
async def test_disable_execute_blocked_for_non_owner_recipient():
    """Non-owner не должен инициировать создание DisableTask через disable_execute."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.RECIPIENT.value)

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_create_disable_task",
            new=AsyncMock(return_value=None),
        ) as create_task,
        patch.object(
            bot_handler,
            "broadcast_disable_task_queue_message",
            new=AsyncMock(),
        ) as broadcast_queue,
    ):
        await bot_handler.handle_update(client, _callback_update(data="disable_execute:token-x:41"))

    create_task.assert_not_awaited()
    broadcast_queue.assert_not_awaited()
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.kwargs["text"] == bot_handler.OWNER_ONLY_TEXT


# Owner проходит проверку и DisableTask создаётся.
@pytest.mark.asyncio
async def test_disable_execute_allowed_for_owner_baseline():
    """Owner успешно проходит проверку — задача создаётся, broadcast вызывается."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    task_info = {
        "fb_ad_id": "ad-77",
        "ad_name": "Owner Ad",
        "created_new": True,
        "incident_key": "token-fresh",
        "message_context": SimpleNamespace(),
    }

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_create_disable_task",
            new=AsyncMock(return_value=task_info),
        ) as create_task,
        patch.object(bot_handler, "_ack_disable_task_messages", new=AsyncMock()),
        patch.object(
            bot_handler,
            "broadcast_disable_task_queue_message",
            new=AsyncMock(),
        ) as broadcast_queue,
    ):
        # Без fb_ad_id-сегмента — это legacy формат, который не проверяет stale-токен.
        await bot_handler.handle_update(
            client, _callback_update(data="disable_execute:token-fresh:41")
        )

    create_task.assert_awaited_once()
    broadcast_queue.assert_awaited_once()


# disable_confirm тоже должен проходить для owner и рендерить confirm-экран.
@pytest.mark.asyncio
async def test_disable_confirm_allowed_for_owner_baseline():
    """Owner получает confirm-экран без отказа."""
    from core.telegram import bot_handler

    client = AsyncMock()
    access = SimpleNamespace(role=TelegramUserRole.OWNER.value)

    with (
        patch.object(
            bot_handler,
            "resolve_telegram_access",
            new=AsyncMock(return_value=access),
        ),
        patch.object(
            bot_handler,
            "_render_disable_confirm",
            new=AsyncMock(return_value=("Подтверждение", {"inline_keyboard": []})),
        ) as render_confirm,
    ):
        await bot_handler.handle_update(client, _callback_update(data="disable_confirm:token-x"))

    render_confirm.assert_awaited_once()
