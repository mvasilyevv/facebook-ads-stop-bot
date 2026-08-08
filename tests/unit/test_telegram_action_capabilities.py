from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from core.models.telegram.notification import TelegramActionToken, TelegramNavigationToken
from core.telegram.action_tokens import (
    ActionTokenClaim,
    claim_action_token,
    digest_action_token,
    generate_raw_action_token,
    mint_action_token,
    retire_replaced_action_tokens,
)
from core.telegram.handlers.alerts import handle_action_callback
from core.telegram.navigation_tokens import (
    digest_navigation_token,
    generate_raw_navigation_token,
)
from core.telegram.notification_renderer import render_notification
from core.telegram.schemas import (
    NotificationActionSpec,
    NotificationCardFacts,
    NotificationEventSpec,
)


def test_action_token_is_16_bytes_as_22_char_base64url() -> None:
    tokens = {generate_raw_action_token() for _ in range(128)}

    assert len(tokens) == 128
    assert all(len(token) == 22 for token in tokens)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{22}", token) for token in tokens)
    assert all(len(f"a:{token}".encode("utf-8")) <= 64 for token in tokens)


def test_action_token_digest_is_sha256_and_raw_value_has_no_model_column() -> None:
    raw = generate_raw_action_token()

    assert len(digest_action_token(raw)) == 32
    assert digest_action_token(raw) == digest_action_token(raw)
    assert "token_digest" in TelegramActionToken.__table__.columns
    assert "raw_token" not in TelegramActionToken.__table__.columns


def test_navigation_token_is_digest_only_and_canonical() -> None:
    raw = generate_raw_navigation_token()

    assert re.fullmatch(r"[A-Za-z0-9_-]{22}", raw)
    assert len(digest_navigation_token(raw)) == 32
    assert "token_digest" in TelegramNavigationToken.__table__.columns
    assert "raw_token" not in TelegramNavigationToken.__table__.columns


@pytest.mark.parametrize("invalid", ["", "short", "a" * 21, "a" * 23, "bad:token" + "a" * 13])
def test_action_token_digest_rejects_noncanonical_input(invalid: str) -> None:
    with pytest.raises(ValueError, match="invalid Telegram action token"):
        digest_action_token(invalid)


class _InsertResult:
    def __init__(self, token_id: uuid.UUID) -> None:
        self.token_id = token_id

    def first(self):
        return (self.token_id,)


class _MintConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        return _InsertResult(uuid.uuid4())


class _FirstResult:
    def __init__(self, row=SimpleNamespace()) -> None:
        self._row = row

    def first(self):
        return self._row

    def all(self):
        return []


class _HandlerConnection:
    def __init__(self, *, already_committed: bool = False) -> None:
        self.already_committed = already_committed
        self.recipient_id = uuid.uuid4()
        self.statements: list[str] = []

    async def execute(self, statement, _params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT t.recipient_id" in sql:
            return _FirstResult(SimpleNamespace(recipient_id=self.recipient_id, incident_id=None))
        return _FirstResult(SimpleNamespace(already_committed=self.already_committed))

    async def scalar(self, statement, _params=None):
        self.statements.append(str(statement))
        return 1


class _HandlerTransaction:
    def __init__(self, conn: _HandlerConnection) -> None:
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *_args):
        return None


class _HandlerEngine:
    def __init__(self, *, already_committed: bool = False) -> None:
        self.conn = _HandlerConnection(already_committed=already_committed)

    def begin(self):
        return _HandlerTransaction(self.conn)


@pytest.mark.asyncio
async def test_mint_keeps_predecessor_valid_until_replacement_is_confirmed() -> None:
    conn = _MintConnection()

    await mint_action_token(
        conn,
        recipient_id=uuid.uuid4(),
        delivery_id=71,
        action_key="pause",
        action_kind="pause_ad",
        target_type="fb_ad",
        target_id="123",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    assert len(conn.calls) == 1
    assert "INSERT INTO telegram_action_tokens" in conn.calls[0][0]
    assert "SET revoked_at" not in conn.calls[0][0]


@pytest.mark.asyncio
async def test_confirmed_replacement_retires_only_equivalent_unclaimed_tokens() -> None:
    conn = AsyncMock()
    conn.execute.return_value = SimpleNamespace(rowcount=2)
    active_id = uuid.uuid4()

    retired = await retire_replaced_action_tokens(
        conn,
        delivery_id=71,
        recipient_id=uuid.uuid4(),
        active_token_ids=[active_id],
    )

    assert retired == 2
    sql = str(conn.execute.await_args.args[0])
    assert "active.delivery_id = :delivery_id" in sql
    assert "previous.target_payload = active.target_payload" in sql
    assert "previous.claimed_at IS NULL" in sql
    assert "previous.revoked_at IS NULL" in sql
    assert "NOT (previous.id = ANY" in sql


@pytest.mark.asyncio
async def test_action_claim_key_cannot_alias_a_stored_prefix() -> None:
    engine = AsyncMock()

    with pytest.raises(ValueError, match="must not exceed 128"):
        await claim_action_token(
            engine,
            raw_token="A" * 22,
            chat_id=7,
            telegram_user_id=8,
            claim_key="x" * 129,
        )

    engine.begin.assert_not_called()


def test_short_renderer_escapes_content_and_never_emits_tables_or_uuid() -> None:
    event = NotificationEventSpec(
        event_type="incident.opened",
        severity="critical",
        facts=NotificationCardFacts(
            title="GH <CR2>",
            summary="Spend $18.40 & growing",
            lines=[f"internal {uuid.uuid4()}", "42 clicks", "5 registrations"],
            risk="spend without FTD",
            open_target={"kind": "incident", "target_id": "incident-1"},
        ),
        actions=[
            NotificationActionSpec(
                key="pause",
                label="Отключить",
                kind="pause_ad",
                target_type="fb_ad",
                target_id="123",
            )
        ],
        dedupe_key="incident:test:1",
    )

    navigation_url = "https://app.example.test/tma?nav=" + "N" * 22
    rendered = render_notification(
        event,
        action_callbacks={"pause": "a:" + "A" * 22},
        navigation_url=navigation_url,
    )

    assert len(rendered.text) <= 700
    assert "<table" not in rendered.text
    assert "&lt;CR2&gt;" in rendered.text
    assert "Spend $18.40 &amp; growing" in rendered.text
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        rendered.text,
    )
    buttons = [row[0] for row in rendered.reply_markup["inline_keyboard"]]
    assert len(buttons) == 2
    assert buttons[0] == {"text": "Открыть", "url": navigation_url}
    assert buttons[1]["callback_data"].startswith("a:")


def test_renderer_bounds_html_after_escape_expansion() -> None:
    event = NotificationEventSpec(
        event_type="incident.opened",
        severity="critical",
        facts=NotificationCardFacts(
            title="&" * 200,
            summary="<" * 280,
            lines=[">" * 180 for _ in range(5)],
            risk="&" * 170,
            status="<" * 70,
        ),
        dedupe_key="incident:html-expansion",
    )

    rendered = render_notification(event)

    assert len(rendered.text) <= 700
    assert rendered.text.startswith("<b>🛑 ")
    assert rendered.text.endswith("</b>") or "\n" in rendered.text
    assert "&amp;" in rendered.text
    assert "&lt;" in rendered.text


def test_renderer_fails_closed_without_minted_action_callback() -> None:
    event = NotificationEventSpec(
        event_type="incident.opened",
        severity="warning",
        facts=NotificationCardFacts(title="Alert"),
        actions=[
            NotificationActionSpec(
                key="pause",
                label="Отключить",
                kind="pause_ad",
                target_type="fb_ad",
                target_id="123",
            )
        ],
        dedupe_key="incident:test:2",
    )

    rendered = render_notification(event)

    assert rendered.reply_markup is None


def test_renderer_rejects_noncanonical_action_callback() -> None:
    event = NotificationEventSpec(
        event_type="incident.opened",
        severity="warning",
        facts=NotificationCardFacts(title="Alert"),
        actions=[
            NotificationActionSpec(
                key="pause",
                label="Отключить",
                kind="pause_ad",
                target_type="fb_ad",
                target_id="123",
            )
        ],
        dedupe_key="incident:test:invalid-callback",
    )

    with pytest.raises(ValueError, match="Invalid opaque"):
        render_notification(event, action_callbacks={"pause": "a:short"})


def test_renderer_rejects_raw_navigation_url() -> None:
    event = NotificationEventSpec(
        event_type="incident.opened",
        severity="warning",
        facts=NotificationCardFacts(
            title="Alert",
            open_target={"kind": "incident", "target_id": "raw-id"},
        ),
        dedupe_key="incident:test:raw-navigation",
    )

    with pytest.raises(ValueError, match="Invalid opaque Telegram navigation URL"):
        render_notification(event, navigation_url="https://app.example.test/incidents/raw-id")


@pytest.mark.asyncio
async def test_opaque_callback_uses_canonical_command_service(monkeypatch) -> None:
    import core.telegram.handlers.alerts as alerts

    call_order: list[str] = []
    token_id = uuid.uuid4()
    claim = ActionTokenClaim(
        status="claimed",
        token_id=token_id,
        action_kind="activate_ad",
        target_type="fb_ad",
        target_id="123",
        target_payload={},
    )

    async def claim_token(*_args, **_kwargs):
        call_order.append("claim")
        return claim

    async def complete_token(*_args, **_kwargs):
        call_order.append("complete")
        return True

    monkeypatch.setattr(alerts, "claim_action_token", AsyncMock(side_effect=claim_token))
    complete = AsyncMock(side_effect=complete_token)
    monkeypatch.setattr(alerts, "complete_action_token", complete)

    async def enqueue_action(*_args, **_kwargs):
        call_order.append("command")
        return SimpleNamespace(task_id=77, created=True, state="queued")

    enqueue = AsyncMock(side_effect=enqueue_action)

    class _CommandService:
        def __init__(self, engine) -> None:
            self.engine = engine

        enqueue_ad_action = enqueue

    monkeypatch.setattr(alerts, "CommandService", _CommandService)
    client = AsyncMock()
    engine = _HandlerEngine()

    await handle_action_callback(
        engine=engine,
        client=client,
        cq_id="callback-1",
        raw_token="A" * 22,
        chat_id=7,
        telegram_user_id=8,
        username="owner",
        bot_generation=1,
    )

    assert call_order == ["claim", "command", "complete"]
    enqueue.assert_awaited_once_with(
        action_kind="activate_ad",
        fb_ad_id="123",
        requested_by="tg:8",
        idempotency_key=f"telegram-action:{token_id}",
        correlation_id=None,
        created_by_chat_id=7,
        connection=ANY,
        transaction_authorizer=ANY,
    )
    complete.assert_awaited_once_with(
        engine,
        token_id=token_id,
        task_id=77,
        connection=ANY,
    )
    assert "#77" in client.answer_callback_query.await_args.kwargs["text"]


def test_rotated_equivalent_incident_tokens_share_money_action_idempotency() -> None:
    import core.telegram.handlers.alerts as alerts

    incident_id = uuid.uuid4()
    first = ActionTokenClaim(
        status="claimed",
        token_id=uuid.uuid4(),
        action_kind="pause_ad",
        target_type="fb_ad",
        target_id="123",
        target_payload={"incident_key": "spend:123"},
        incident_id=incident_id,
        incident_generation=4,
    )
    replacement = ActionTokenClaim(
        status="claimed",
        token_id=uuid.uuid4(),
        action_kind=first.action_kind,
        target_type=first.target_type,
        target_id=first.target_id,
        target_payload={"incident_key": "spend:123"},
        incident_id=incident_id,
        incident_generation=4,
    )

    first_key = alerts._command_idempotency_key(first)
    replacement_key = alerts._command_idempotency_key(replacement)

    assert first_key == replacement_key
    assert first_key.startswith("telegram-incident-action:")
    assert len(first_key) <= 128


@pytest.mark.asyncio
async def test_opaque_callback_keeps_ambiguous_task_creation_retryable(monkeypatch) -> None:
    import core.telegram.handlers.alerts as alerts

    token_id = uuid.uuid4()
    monkeypatch.setattr(
        alerts,
        "claim_action_token",
        AsyncMock(
            return_value=ActionTokenClaim(
                status="claimed",
                token_id=token_id,
                action_kind="pause_ad",
                target_type="fb_ad",
                target_id="123",
                target_payload={},
            )
        ),
    )
    complete = AsyncMock(return_value=True)
    monkeypatch.setattr(alerts, "complete_action_token", complete)

    class _CommandService:
        def __init__(self, engine) -> None:
            self.engine = engine

        async def enqueue_ad_action(self, **_kwargs):
            raise RuntimeError("database response lost")

    monkeypatch.setattr(alerts, "CommandService", _CommandService)

    with pytest.raises(RuntimeError, match="response lost"):
        await handle_action_callback(
            engine=_HandlerEngine(),
            client=AsyncMock(),
            cq_id="callback-1",
            raw_token="A" * 22,
            chat_id=7,
            telegram_user_id=8,
            username="owner",
            bot_generation=1,
        )

    complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_opaque_callback_ack_failure_keeps_inbox_update_retryable(monkeypatch) -> None:
    """A committed task must not turn a lost callback ack into false success."""
    import core.telegram.handlers.alerts as alerts

    token_id = uuid.uuid4()
    monkeypatch.setattr(
        alerts,
        "claim_action_token",
        AsyncMock(
            return_value=ActionTokenClaim(
                status="claimed",
                token_id=token_id,
                action_kind="activate_ad",
                target_type="fb_ad",
                target_id="123",
                target_payload={},
            )
        ),
    )
    monkeypatch.setattr(
        alerts,
        "complete_action_token",
        AsyncMock(return_value=True),
    )

    enqueue = AsyncMock(return_value=SimpleNamespace(task_id=77, created=True, state="queued"))

    class _CommandService:
        def __init__(self, engine) -> None:
            self.engine = engine

        enqueue_ad_action = enqueue

    monkeypatch.setattr(alerts, "CommandService", _CommandService)
    client = AsyncMock()
    client.answer_callback_query.side_effect = RuntimeError("ack response lost")

    with pytest.raises(RuntimeError, match="ack response lost"):
        await handle_action_callback(
            engine=_HandlerEngine(),
            client=client,
            cq_id="callback-1",
            raw_token="A" * 22,
            chat_id=7,
            telegram_user_id=8,
            username="owner",
            bot_generation=1,
        )

    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_incident_ack_callback_uses_recipient_bound_generation(monkeypatch) -> None:
    import core.telegram.handlers.alerts as alerts

    token_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    claim = ActionTokenClaim(
        status="claimed",
        token_id=token_id,
        action_kind="ack_incident",
        target_type="incident",
        target_id=str(incident_id),
        target_payload={"generation": 4},
        incident_id=incident_id,
        incident_generation=4,
    )
    claim_token = AsyncMock(return_value=claim)
    acknowledge = AsyncMock(return_value=SimpleNamespace(was_changed=True))
    consume = AsyncMock(return_value=True)
    complete = AsyncMock(return_value=True)
    monkeypatch.setattr(alerts, "claim_action_token", claim_token)
    monkeypatch.setattr(alerts, "acknowledge_incident", acknowledge)
    monkeypatch.setattr(alerts, "consume_action_token", consume)
    monkeypatch.setattr(alerts, "complete_action_token", complete)
    client = AsyncMock()
    engine = _HandlerEngine()

    await handle_action_callback(
        engine=engine,
        client=client,
        cq_id="callback-ack-1",
        raw_token="A" * 22,
        chat_id=700,
        telegram_user_id=800,
        username="",
        bot_generation=1,
    )

    claim_token.assert_awaited_once_with(
        engine,
        raw_token="A" * 22,
        chat_id=700,
        telegram_user_id=800,
        claim_key="callback-ack-1",
    )
    acknowledge.assert_awaited_once_with(
        engine,
        incident_id=incident_id,
        acknowledged_by="tg:800",
        expected_generation=4,
        connection=ANY,
    )
    consume.assert_awaited_once_with(engine, token_id=token_id, connection=ANY)
    complete.assert_not_awaited()
    assert client.answer_callback_query.await_args.kwargs["text"] == "Инцидент принят"
    statements = engine.conn.statements
    incident_lock = next(i for i, sql in enumerate(statements) if "FOR UPDATE OF i" in sql)
    roster_lock = next(i for i, sql in enumerate(statements) if "hashtext(:lock_key)" in sql)
    recipient_advisory = next(
        i for i, sql in enumerate(statements) if "pg_advisory_xact_lock(:namespace" in sql
    )
    authority_recheck = next(
        i for i, sql in enumerate(statements) if "SELECT 1 FROM telegram_config" in sql
    )
    recipient_recheck = next(
        i
        for i, sql in enumerate(statements)
        if "JOIN telegram_recipients r" in sql and "FOR SHARE OF r" in sql
    )
    assert incident_lock < roster_lock < recipient_advisory < authority_recheck < recipient_recheck


@pytest.mark.asyncio
async def test_incident_ack_callback_fails_closed_on_generation_mismatch(monkeypatch) -> None:
    import core.telegram.handlers.alerts as alerts

    token_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    monkeypatch.setattr(
        alerts,
        "claim_action_token",
        AsyncMock(
            return_value=ActionTokenClaim(
                status="claimed",
                token_id=token_id,
                action_kind="ack_incident",
                target_type="incident",
                target_id=str(incident_id),
                target_payload={"generation": 3},
                incident_id=incident_id,
                incident_generation=4,
            )
        ),
    )
    acknowledge = AsyncMock()
    consume = AsyncMock()
    complete = AsyncMock(return_value=True)
    monkeypatch.setattr(alerts, "acknowledge_incident", acknowledge)
    monkeypatch.setattr(alerts, "consume_action_token", consume)
    monkeypatch.setattr(alerts, "complete_action_token", complete)
    client = AsyncMock()
    engine = _HandlerEngine()

    await handle_action_callback(
        engine=engine,
        client=client,
        cq_id="callback-ack-stale",
        raw_token="A" * 22,
        chat_id=700,
        telegram_user_id=800,
        username="owner",
        bot_generation=1,
    )

    acknowledge.assert_not_awaited()
    consume.assert_not_awaited()
    complete.assert_awaited_once_with(
        engine,
        token_id=token_id,
        failure_code="invalid_incident_action",
    )
    assert "устарело" in client.answer_callback_query.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_duplicate_incident_ack_callback_never_runs_service_again(monkeypatch) -> None:
    import core.telegram.handlers.alerts as alerts

    monkeypatch.setattr(
        alerts,
        "claim_action_token",
        AsyncMock(return_value=ActionTokenClaim(status="already_consumed")),
    )
    acknowledge = AsyncMock()
    consume = AsyncMock()
    monkeypatch.setattr(alerts, "acknowledge_incident", acknowledge)
    monkeypatch.setattr(alerts, "consume_action_token", consume)
    client = AsyncMock()

    await handle_action_callback(
        engine=_HandlerEngine(),
        client=client,
        cq_id="callback-ack-duplicate",
        raw_token="A" * 22,
        chat_id=700,
        telegram_user_id=800,
        username="owner",
        bot_generation=1,
    )

    acknowledge.assert_not_awaited()
    consume.assert_not_awaited()
    assert client.answer_callback_query.await_args.kwargs["text"] == "Действие уже завершено"
