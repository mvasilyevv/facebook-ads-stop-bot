from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.telegram_delivery_worker import main as delivery_worker
from apps.telegram_update_worker import main as update_worker
from core.telegram.command_replies import (
    ClaimedTelegramCommandReply,
    DurableTelegramUpdateClient,
)
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
)
from core.telegram.update_inbox import ClaimedTelegramUpdate


class _Authority:
    def __init__(self, authorized: bool = True) -> None:
        self._authorized = authorized

    async def __aenter__(self) -> bool:
        return self._authorized

    async def __aexit__(self, *_args) -> None:
        return None


@pytest.mark.asyncio
async def test_handler_client_captures_send_without_crossing_gateway() -> None:
    gateway = SimpleNamespace(send_message=AsyncMock())
    client = DurableTelegramUpdateClient(gateway)  # type: ignore[arg-type]

    result = await client.send_message(
        chat_id="42",
        text="<b>queued</b>",
        reply_to_message_id=7,
    )

    assert result == {"message_id": None, "durable": True}
    assert len(client.replies) == 1
    assert client.replies[0].chat_id == 42
    assert client.replies[0].reply_to_message_id == 7
    gateway.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_handler_client_exposes_only_explicit_idempotent_gateway_methods() -> None:
    gateway = SimpleNamespace(
        answer_callback_query=AsyncMock(),
        set_chat_menu_button=AsyncMock(),
    )
    client = DurableTelegramUpdateClient(gateway)  # type: ignore[arg-type]

    await client.answer_callback_query("callback-1", text="Принято")
    await client.set_chat_menu_button(
        web_app_url="https://app.example.test/tma",
        chat_id=42,
    )

    gateway.answer_callback_query.assert_awaited_once_with(
        "callback-1",
        text="Принято",
    )
    gateway.set_chat_menu_button.assert_awaited_once_with(
        web_app_url="https://app.example.test/tma",
        button_text="📱 Открыть",
        chat_id=42,
    )
    assert not hasattr(client, "get_updates")


@pytest.mark.asyncio
async def test_update_worker_commits_reply_intent_instead_of_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = ClaimedTelegramUpdate(
        bot_generation=7,
        update_id=101,
        payload={"update_id": 101},
        attempt_count=1,
        lease_token=uuid.uuid4(),
    )
    gateway = SimpleNamespace(
        send_message=AsyncMock(),
        credential_fingerprint="0" * 64,
    )
    finalize = AsyncMock(return_value=True)

    async def fake_handle_update(*, client, **_kwargs) -> None:
        await client.send_message(chat_id=42, text="durable reply")

    monkeypatch.setattr(update_worker, "claim_telegram_update", AsyncMock(return_value=claim))
    monkeypatch.setattr(
        update_worker,
        "telegram_update_claim_is_authoritative",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        update_worker,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(),
    )
    monkeypatch.setattr(update_worker, "handle_update", fake_handle_update)
    monkeypatch.setattr(update_worker, "finalize_update_with_replies", finalize)

    assert await update_worker.process_one_update(  # type: ignore[arg-type]
        SimpleNamespace(),
        gateway=gateway,
        worker_id="update-test",
    )
    gateway.send_message.assert_not_awaited()
    replies = finalize.await_args.kwargs["replies"]
    assert len(replies) == 1
    assert replies[0].text == "durable reply"


def _reply_claim() -> ClaimedTelegramCommandReply:
    return ClaimedTelegramCommandReply(
        reply_id=9,
        bot_generation=7,
        update_id=101,
        lease_token=uuid.uuid4(),
        attempt_count=1,
        max_attempts=8,
        chat_id=42,
        text="reply",
        parse_mode="HTML",
        reply_to_message_id=7,
        reply_markup=None,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_delivery_worker_marks_boundary_before_command_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _reply_claim()
    gateway = SimpleNamespace(
        send_message=AsyncMock(return_value={"message_id": 55}),
        credential_fingerprint="0" * 64,
    )
    boundary = AsyncMock(return_value=True)
    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(
        delivery_worker,
        "claim_telegram_command_reply",
        AsyncMock(return_value=claim),
    )
    monkeypatch.setattr(delivery_worker, "mark_command_reply_external_started", boundary)
    monkeypatch.setattr(delivery_worker, "mark_command_reply_sent", sent)
    monkeypatch.setattr(
        delivery_worker,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(),
    )

    assert await delivery_worker.process_one_command_reply(  # type: ignore[arg-type]
        SimpleNamespace(),
        gateway=gateway,
        gateway_generation=7,
        worker_id="delivery-test",
    )
    boundary.assert_awaited_once_with(
        SimpleNamespace(),
        claim=claim,
        gateway_generation=7,
        credential_fingerprint="0" * 64,
    )
    gateway.send_message.assert_awaited_once()
    sent.assert_awaited_once()


@pytest.mark.asyncio
async def test_command_reply_denied_outer_authority_terminalizes_without_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _reply_claim()
    engine = SimpleNamespace()
    gateway = SimpleNamespace(
        send_message=AsyncMock(),
        credential_fingerprint="0" * 64,
    )
    boundary = AsyncMock(return_value=False)
    sent = AsyncMock()
    monkeypatch.setattr(
        delivery_worker,
        "claim_telegram_command_reply",
        AsyncMock(return_value=claim),
    )
    monkeypatch.setattr(delivery_worker, "mark_command_reply_external_started", boundary)
    monkeypatch.setattr(delivery_worker, "mark_command_reply_sent", sent)
    monkeypatch.setattr(
        delivery_worker,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(False),
    )

    assert await delivery_worker.process_one_command_reply(  # type: ignore[arg-type]
        engine,
        gateway=gateway,
        gateway_generation=7,
        worker_id="delivery-test",
    )
    boundary.assert_awaited_once_with(
        engine,
        claim=claim,
        gateway_generation=7,
        credential_fingerprint="0" * 64,
    )
    gateway.send_message.assert_not_awaited()
    sent.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_command_send_is_persisted_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _reply_claim()
    error = TelegramGatewayError(
        method="sendMessage",
        kind=TelegramFailureKind.UNKNOWN,
    )
    gateway = SimpleNamespace(
        send_message=AsyncMock(side_effect=error),
        credential_fingerprint="0" * 64,
    )
    failure = AsyncMock()
    monkeypatch.setattr(
        delivery_worker,
        "claim_telegram_command_reply",
        AsyncMock(return_value=claim),
    )
    monkeypatch.setattr(
        delivery_worker,
        "mark_command_reply_external_started",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(delivery_worker, "mark_command_reply_failure", failure)
    monkeypatch.setattr(
        delivery_worker,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(),
    )

    assert await delivery_worker.process_one_command_reply(  # type: ignore[arg-type]
        SimpleNamespace(),
        gateway=gateway,
        gateway_generation=7,
        worker_id="delivery-test",
    )
    failure.assert_awaited_once_with(
        SimpleNamespace(),
        claim=claim,
        error=error,
        credential_fingerprint="0" * 64,
    )
