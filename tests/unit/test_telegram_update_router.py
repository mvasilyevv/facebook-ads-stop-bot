# -*- coding: utf-8 -*-
"""Unit coverage for the active durable Telegram update router."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.telegram.handlers.router import handle_update
from core.telegram.service import Recipient


def _message(text: str, *, chat_type: str = "private") -> dict:
    return {
        "message": {
            "chat": {"id": 123, "type": chat_type},
            "message_id": 1,
            "from": {"id": 555, "username": "alice"},
            "text": text,
        }
    }


@pytest.mark.asyncio
async def test_unregistered_free_text_is_ignored() -> None:
    client = MagicMock(send_message=AsyncMock())
    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=None),
    ):
        await handle_update(
            engine=MagicMock(), client=client, update=_message("привет"), bot_generation=1
        )
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_without_code_requests_invite() -> None:
    client = MagicMock(send_message=AsyncMock())
    await handle_update(
        engine=MagicMock(), client=client, update=_message("/start"), bot_generation=1
    )
    assert "код-приглашение" in client.send_message.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_help_is_available_to_recipient() -> None:
    client = MagicMock(send_message=AsyncMock())
    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=Recipient(123, 555, "alice", "recipient")),
    ):
        await handle_update(
            engine=MagicMock(), client=client, update=_message("/help"), bot_generation=1
        )
    text = client.send_message.await_args.kwargs["text"]
    assert "веб-интерфейсе" in text
    assert "/help" in text


@pytest.mark.asyncio
async def test_removed_command_has_no_compatibility_stub() -> None:
    client = MagicMock(send_message=AsyncMock())
    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=Recipient(123, 555, "alice", "owner")),
    ):
        await handle_update(
            engine=MagicMock(), client=client, update=_message("/ads"), bot_generation=1
        )
    text = client.send_message.await_args.kwargs["text"]
    assert "Неизвестная команда" in text
    assert "миграц" not in text.lower()


@pytest.mark.asyncio
async def test_bot_username_suffix_is_normalized() -> None:
    client = MagicMock(send_message=AsyncMock())
    with patch(
        "core.telegram.handlers.router.find_recipient",
        new=AsyncMock(return_value=Recipient(123, 555, "alice", "recipient")),
    ):
        await handle_update(
            engine=MagicMock(),
            client=client,
            update=_message("/help@my_test_bot"),
            bot_generation=1,
        )
    assert "/help" in client.send_message.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_group_commands_are_ignored_dm_only() -> None:
    client = MagicMock(send_message=AsyncMock())
    await handle_update(
        engine=MagicMock(),
        client=client,
        update=_message("/help", chat_type="group"),
        bot_generation=1,
    )
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_callback_is_acknowledged() -> None:
    client = MagicMock(send_message=AsyncMock(), answer_callback_query=AsyncMock())
    await handle_update(
        engine=MagicMock(),
        client=client,
        update={
            "callback_query": {
                "id": "1",
                "data": "noop",
                "from": {"id": 555, "username": "alice"},
                "message": {
                    "chat": {"id": 123, "type": "private"},
                    "message_id": 1,
                },
            }
        },
        bot_generation=1,
    )
    client.send_message.assert_not_awaited()
    assert "формат" in client.answer_callback_query.await_args.kwargs["text"].lower()
