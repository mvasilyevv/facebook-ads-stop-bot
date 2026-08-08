# -*- coding: utf-8 -*-
"""ACL coverage for the compact durable Telegram router."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.telegram.handlers.router as router
from core.telegram.service import Recipient


def _owner() -> Recipient:
    return Recipient(chat_id=1, telegram_user_id=2, username="u", role="owner")


def _viewer() -> Recipient:
    return Recipient(chat_id=1, telegram_user_id=2, username="u", role="recipient")


def _cq(data: str) -> dict:
    return {
        "id": "cq1",
        "data": data,
        "from": {"id": 2, "username": "u"},
        "message": {"chat": {"id": 1, "type": "private"}, "message_id": 9},
    }


def _command(text: str, *, chat_type: str = "private") -> dict:
    return {
        "message": {
            "chat": {"id": 1, "type": chat_type},
            "message_id": 5,
            "from": {"id": 2, "username": "u"},
            "text": text,
        }
    }


@pytest.mark.asyncio
async def test_owner_can_execute_opaque_action(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_owner()))
    handler = AsyncMock()
    monkeypatch.setattr(router, "handle_action_callback", handler)

    await router._dispatch_callback_query(
        engine=object(),
        client=AsyncMock(),
        cq=_cq("a:" + "T" * 22),
        bot_generation=1,
    )

    handler.assert_awaited_once()
    assert handler.await_args.kwargs["raw_token"] == "T" * 22


@pytest.mark.asyncio
async def test_viewer_capability_is_authorized_by_atomic_token_claim(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_viewer()))
    handler = AsyncMock()
    monkeypatch.setattr(router, "handle_action_callback", handler)
    await router._dispatch_callback_query(
        engine=object(),
        client=AsyncMock(),
        cq=_cq("a:" + "T" * 22),
        bot_generation=1,
    )

    handler.assert_awaited_once()
    assert handler.await_args.kwargs["raw_token"] == "T" * 22


@pytest.mark.asyncio
async def test_group_callback_is_rejected_before_recipient_lookup(monkeypatch) -> None:
    lookup = AsyncMock()
    monkeypatch.setattr(router, "find_recipient", lookup)
    handler = AsyncMock()
    monkeypatch.setattr(router, "handle_action_callback", handler)
    client = AsyncMock()
    callback = _cq("a:" + "T" * 22)
    callback["message"]["chat"]["type"] = "group"

    await router._dispatch_callback_query(
        engine=object(),
        client=client,
        cq=callback,
        bot_generation=1,
    )

    lookup.assert_not_awaited()
    handler.assert_not_awaited()
    assert "только в личке" in client.answer_callback_query.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_internal_token_id_does_not_expose_raw_capability(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_owner()))
    handler = AsyncMock()
    monkeypatch.setattr(router, "handle_action_callback", handler)
    token_id = "8e64d150-f396-41a2-9667-a3181a0ec0ff"
    callback = _cq("a:redacted")
    callback["_fb_action_token_id"] = token_id

    await router._dispatch_callback_query(
        engine=object(),
        client=AsyncMock(),
        cq=callback,
        bot_generation=1,
    )

    handler.assert_awaited_once()
    assert handler.await_args.kwargs["raw_token"] is None
    assert str(handler.await_args.kwargs["token_id"]) == token_id


@pytest.mark.asyncio
async def test_revoked_recipient_can_only_resume_receipt_proven_internal_claim(
    monkeypatch,
) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=None))
    recovery = AsyncMock(return_value=True)
    monkeypatch.setattr(router, "is_claimed_action_recovery", recovery)
    handler = AsyncMock()
    monkeypatch.setattr(router, "handle_action_callback", handler)
    callback = _cq("a:redacted")
    callback["_fb_action_token_id"] = "8e64d150-f396-41a2-9667-a3181a0ec0ff"

    await router._dispatch_callback_query(
        engine=object(),
        client=AsyncMock(),
        cq=callback,
        bot_generation=7,
    )

    recovery.assert_awaited_once()
    handler.assert_awaited_once()
    assert handler.await_args.kwargs["bot_generation"] == 7


@pytest.mark.asyncio
async def test_revoked_recipient_raw_capability_never_bypasses_router_acl(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=None))
    recovery = AsyncMock()
    monkeypatch.setattr(router, "is_claimed_action_recovery", recovery)
    handler = AsyncMock()
    monkeypatch.setattr(router, "handle_action_callback", handler)

    await router._dispatch_callback_query(
        engine=object(),
        client=AsyncMock(),
        cq=_cq("a:" + "T" * 22),
        bot_generation=7,
    )

    recovery.assert_not_awaited()
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_callback_has_no_secondary_handler(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=_owner()))
    handler = AsyncMock()
    monkeypatch.setattr(router, "handle_action_callback", handler)
    client = AsyncMock()

    await router._dispatch_callback_query(
        engine=object(),
        client=client,
        cq=_cq("unknown:123"),
        bot_generation=1,
    )

    handler.assert_not_awaited()
    assert "Неизвестная" in client.answer_callback_query.await_args.kwargs["text"]


def test_recipient_is_owner_predicate() -> None:
    assert _owner().is_owner() is True
    assert _viewer().is_owner() is False


@pytest.mark.asyncio
async def test_group_command_is_ignored_dm_only(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=None))
    send = AsyncMock()
    monkeypatch.setattr(router, "send_text", send)

    await router.handle_update(
        engine=object(),
        client=AsyncMock(),
        update=_command("/unknown", chat_type="group"),
        bot_generation=1,
    )

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_bypasses_recipient_gate(monkeypatch) -> None:
    monkeypatch.setattr(router, "find_recipient", AsyncMock(return_value=None))
    handler = AsyncMock()
    monkeypatch.setattr(router, "handle_start", handler)

    await router.handle_update(
        engine=object(),
        client=AsyncMock(),
        update=_command("/start INVITECODE"),
        bot_generation=1,
    )

    handler.assert_awaited_once()
