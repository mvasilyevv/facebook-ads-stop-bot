# -*- coding: utf-8 -*-
"""Тесты cross-link сообщения в General topic."""

from __future__ import annotations

import pytest

from core.domain import TelegramNotificationStream
from core.telegram.delivery import _build_supergroup_deeplink
from core.telegram.service import TelegramDestination


def _make_destination(*, thread_id_stop=200, thread_id_general=1):
    """Конструирует destination с заданными thread_id."""
    return TelegramDestination(
        chat_id="-1001234567890",
        telegram_user_id="42",
        role="owner",
        username="boss",
        first_name="Owner",
        is_primary=True,
        thread_id_stop=thread_id_stop,
        thread_id_general=thread_id_general,
    )


# deeplink должен корректно строиться по правилу t.me/c/<chat-100…>/<thread>/<msg>.
def test_build_supergroup_deeplink_strips_minus_100_prefix():
    url = _build_supergroup_deeplink("-1001234567890", 200, 5555)
    assert url == "https://t.me/c/1234567890/200/5555"


# Для positive chat_id (не супергруппа) deeplink не строится.
def test_build_supergroup_deeplink_returns_none_for_positive_chat_id():
    assert _build_supergroup_deeplink("123456", 200, 5555) is None


# При успешной отправке STOP в топик в General должна уйти короткая ссылка с deeplink-ом.
@pytest.mark.asyncio
async def test_maybe_post_general_link_sends_short_message_for_stop():
    from unittest.mock import AsyncMock

    from core.telegram.delivery import _maybe_post_general_link

    client = AsyncMock()
    destination = _make_destination()
    await _maybe_post_general_link(
        client,
        destination=destination,
        stream_kind=TelegramNotificationStream.STOP,
        ad_name="ADS_TEST_001",
        topic_thread_id=200,
        topic_message_id=5555,
    )

    client.send_message.assert_awaited_once()
    kwargs = client.send_message.await_args.kwargs
    assert kwargs["chat_id"] == "-1001234567890"
    assert kwargs["message_thread_id"] == 1
    assert "🛑 STOP" in kwargs["text"]
    assert "ADS_TEST_001" in kwargs["text"]
    assert "https://t.me/c/1234567890/200/5555" in kwargs["text"]


# Если thread_id_general не задан — cross-link не отправляется.
@pytest.mark.asyncio
async def test_maybe_post_general_link_skipped_when_general_not_bound():
    from unittest.mock import AsyncMock

    from core.telegram.delivery import _maybe_post_general_link

    client = AsyncMock()
    destination = _make_destination(thread_id_general=None)
    await _maybe_post_general_link(
        client,
        destination=destination,
        stream_kind=TelegramNotificationStream.STOP,
        ad_name="X",
        topic_thread_id=200,
        topic_message_id=5555,
    )

    client.send_message.assert_not_called()


# Для OPS-стрима cross-link тоже не нужен — General получает только WARNING/STOP/ENABLE.
@pytest.mark.asyncio
async def test_maybe_post_general_link_skipped_for_ops_stream():
    from unittest.mock import AsyncMock

    from core.telegram.delivery import _maybe_post_general_link

    client = AsyncMock()
    destination = _make_destination()
    await _maybe_post_general_link(
        client,
        destination=destination,
        stream_kind=TelegramNotificationStream.OPS,
        ad_name="X",
        topic_thread_id=300,
        topic_message_id=5555,
    )

    client.send_message.assert_not_called()
