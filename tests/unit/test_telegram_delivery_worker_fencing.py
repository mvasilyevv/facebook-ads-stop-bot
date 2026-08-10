from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.telegram_delivery_worker.main as worker


class _Authority:
    def __init__(self, authorized: bool = True) -> None:
        self.authorized = authorized

    async def __aenter__(self) -> bool:
        return self.authorized

    async def __aexit__(self, *_args) -> None:
        return None


@pytest.mark.parametrize(
    ("recipient_role", "required_role", "allowed"),
    [
        ("owner", "owner", True),
        ("owner", "recipient", True),
        ("recipient", "recipient", True),
        ("recipient", "owner", False),
    ],
)
def test_actions_are_filtered_before_token_mint(
    recipient_role: str,
    required_role: str,
    allowed: bool,
) -> None:
    assert (
        worker._recipient_can_use_action(
            recipient_role=recipient_role,
            required_role=required_role,
        )
        is allowed
    )


class _Result:
    def first(self):
        return None


class _Connection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement, params):
        self.statements.append(str(statement))
        assert params["delivery_id"] == 7
        return _Result()


class _Engine:
    def __init__(self) -> None:
        self.connection = _Connection()

    def begin(self):
        connection = self.connection

        class _Context:
            async def __aenter__(self):
                return connection

            async def __aexit__(self, *_args):
                return False

        return _Context()


@pytest.mark.asyncio
async def test_stale_delivery_cannot_revoke_or_mint_capabilities(monkeypatch) -> None:
    engine = _Engine()
    mint_action = AsyncMock()
    mint_navigation = AsyncMock()
    monkeypatch.setattr(worker, "load_web_app_url", AsyncMock(return_value=None))
    monkeypatch.setattr(worker, "mint_action_token", mint_action)
    monkeypatch.setattr(worker, "mint_navigation_token", mint_navigation)
    claim = SimpleNamespace(
        delivery_id=7,
        bot_generation=7,
        lease_token=uuid.uuid4(),
        event=SimpleNamespace(
            actions=[SimpleNamespace(key="pause")],
            facts=SimpleNamespace(open_target=SimpleNamespace(kind="ad", target_id="1")),
        ),
    )

    with pytest.raises(worker.LostDeliveryLeaseError):
        await worker._mint_delivery_capabilities(engine, claim)

    assert "lease_expires_at > clock_timestamp()" in engine.connection.statements[0]
    mint_action.assert_not_awaited()
    mint_navigation.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("unchanged", [False, True])
async def test_delivery_retires_navigation_only_after_confirmed_replacement(
    monkeypatch,
    unchanged: bool,
) -> None:
    action_token_id = uuid.uuid4()
    navigation_token_id = uuid.uuid4()
    claim = SimpleNamespace(
        delivery_id=7,
        bot_generation=7,
        lease_token=uuid.uuid4(),
        chat_id=700,
        slot_message_id=701,
        incident_id=uuid.uuid4(),
        event=SimpleNamespace(event_type="incident_warning", severity="critical"),
        event_created_at=None,
    )
    gateway = SimpleNamespace(
        edit_message=AsyncMock(),
        credential_fingerprint="0" * 64,
    )
    if unchanged:
        gateway.edit_message.side_effect = worker.TelegramGatewayError(
            method="editMessageText",
            kind=worker.TelegramFailureKind.INVALID_REQUEST,
            description="Bad Request: message is not modified",
        )
    finalize = AsyncMock(return_value=True)
    monkeypatch.setattr(
        worker,
        "claim_notification_delivery",
        AsyncMock(return_value=claim),
    )
    monkeypatch.setattr(
        worker,
        "_mint_delivery_capabilities",
        AsyncMock(
            return_value=(
                {"pause": "a:token"},
                "https://example.test/?nav=token",
                (action_token_id,),
                (navigation_token_id,),
            )
        ),
    )
    monkeypatch.setattr(
        worker,
        "render_notification",
        lambda *_args, **_kwargs: SimpleNamespace(
            text="card",
            reply_markup={"inline_keyboard": []},
            render_hash=b"x" * 32,
        ),
    )
    monkeypatch.setattr(
        worker,
        "mark_delivery_external_started",
        AsyncMock(return_value="ready"),
    )
    monkeypatch.setattr(worker, "mark_delivery_sent", finalize)
    monkeypatch.setattr(
        worker,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(),
    )
    monkeypatch.setattr(
        worker, "record_notification_delivery_transition", lambda *_args, **_kw: None
    )

    assert await worker.process_one_delivery(
        object(),
        gateway=gateway,
        gateway_generation=7,
        worker_id="worker",
    )

    assert finalize.await_args.kwargs["active_action_token_ids"] == (
        () if unchanged else (action_token_id,)
    )
    assert finalize.await_args.kwargs["active_navigation_token_ids"] == (
        () if unchanged else (navigation_token_id,)
    )


@pytest.mark.asyncio
async def test_delivery_authority_rejection_makes_zero_gateway_calls(monkeypatch) -> None:
    claim = SimpleNamespace(
        delivery_id=8,
        bot_generation=7,
        lease_token=uuid.uuid4(),
        chat_id=700,
        slot_message_id=None,
        incident_id=None,
        event=SimpleNamespace(event_type="system_warning", severity="warning"),
        event_created_at=None,
    )
    gateway = SimpleNamespace(
        send_message=AsyncMock(return_value={"message_id": 55}),
        edit_message=AsyncMock(),
        credential_fingerprint="0" * 64,
    )
    boundary = AsyncMock(return_value="superseded")
    finalized = AsyncMock(return_value=True)
    monkeypatch.setattr(
        worker,
        "claim_notification_delivery",
        AsyncMock(return_value=claim),
    )
    monkeypatch.setattr(
        worker,
        "_mint_delivery_capabilities",
        AsyncMock(return_value=({}, None, (), ())),
    )
    monkeypatch.setattr(
        worker,
        "render_notification",
        lambda *_args, **_kwargs: SimpleNamespace(
            text="card",
            reply_markup=None,
            render_hash=b"x" * 32,
        ),
    )
    monkeypatch.setattr(worker, "mark_delivery_external_started", boundary)
    monkeypatch.setattr(worker, "mark_delivery_sent", finalized)
    monkeypatch.setattr(
        worker,
        "hold_telegram_outbound_authority",
        lambda *_args, **_kwargs: _Authority(False),
    )

    assert await worker.process_one_delivery(
        object(),
        gateway=gateway,
        gateway_generation=7,
        worker_id="worker",
    )

    boundary.assert_awaited_once()
    gateway.send_message.assert_not_awaited()
    gateway.edit_message.assert_not_awaited()
    finalized.assert_not_awaited()
