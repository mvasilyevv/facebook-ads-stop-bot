from __future__ import annotations

import ast
import json
import uuid
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from apps.api.main import create_app
from apps.api.routers.v1.schemas.settings_telegram import (
    TelegramRecipientPreferenceRequest,
)
from core.models.telegram.notification import (
    NotificationDelivery,
    TelegramMessageSlot,
    TelegramUpdateInbox,
)
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
)
from core.telegram.handlers._send import send_text
from core.telegram.notifications import (
    ClaimedNotificationDelivery,
    claim_notification_delivery,
    decide_delivery_failure,
    mark_delivery_failure,
    recipient_delivery_schedule,
)
from core.telegram.schemas import (
    NotificationCardFacts,
    NotificationEventSpec,
    TelegramWebhookUpdate,
)
from core.telegram.update_inbox import (
    ClaimedTelegramUpdate,
    claim_telegram_update,
    mark_telegram_update_failed,
    persist_telegram_update,
)


class _Result:
    def __init__(self, row: Any = None, rowcount: int = 0) -> None:
        self._row = row
        self.rowcount = rowcount

    def first(self) -> Any:
        return self._row

    def scalar_one(self) -> Any:
        return self._row

    def scalar_one_or_none(self) -> Any:
        return self._row


class _Connection:
    def __init__(self, results: list[_Result] | None = None) -> None:
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []
        self.results = list(results or [_Result()])

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        self.statements.append(str(statement))
        self.params.append(params or {})
        return self.results.pop(0) if self.results else _Result()

    async def scalar(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        return (await self.execute(statement, params)).scalar_one_or_none()


class _Context:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def begin(self) -> _Context:
        return _Context(self.connection)


def test_notification_message_ids_cannot_use_zero_sentinel() -> None:
    assert NotificationDelivery.__table__.c.telegram_message_id.nullable is True
    delivery_checks = " ".join(
        str(item.sqltext)
        for item in NotificationDelivery.__table__.constraints
        if hasattr(item, "sqltext")
    )
    slot_checks = " ".join(
        str(item.sqltext)
        for item in TelegramMessageSlot.__table__.constraints
        if hasattr(item, "sqltext")
    )

    assert "telegram_message_id > 0" in delivery_checks
    assert "message_id > 0" in slot_checks


def test_outbox_and_inbox_have_partial_claim_indexes() -> None:
    delivery_index = next(
        index
        for index in NotificationDelivery.__table__.indexes
        if index.name == "ix_notification_delivery_claim"
    )
    inbox_index = next(
        index
        for index in TelegramUpdateInbox.__table__.indexes
        if index.name == "ix_telegram_update_inbox_claim"
    )

    assert "pending" in str(delivery_index.dialect_options["postgresql"]["where"])
    assert "pending" in str(inbox_index.dialect_options["postgresql"]["where"])


@pytest.mark.asyncio
async def test_delivery_claim_uses_skip_locked() -> None:
    connection = _Connection([_Result(rowcount=0), _Result(row=7), _Result(row=None)])

    claim = await claim_notification_delivery(  # type: ignore[arg-type]
        _Engine(connection),
        worker_id="worker-1",
        gateway_generation=7,
        credential_fingerprint="0" * 64,
    )

    assert claim is None
    sql = connection.statements[2].upper()
    assert "FOR UPDATE OF D, R SKIP LOCKED" in sql
    assert "STATE IN ('PENDING','RETRY')" in sql
    assert "IN_FLIGHT.STATE = 'LEASED'" in sql
    assert "TELEGRAM:BOT-AUTH" in sql


@pytest.mark.asyncio
async def test_update_claim_uses_skip_locked() -> None:
    connection = _Connection([_Result(rowcount=0), _Result(row=None)])

    claim = await claim_telegram_update(  # type: ignore[arg-type]
        _Engine(connection), worker_id="worker-1"
    )

    assert claim is None
    assert "FOR UPDATE OF I SKIP LOCKED" in connection.statements[1].upper()


@pytest.mark.asyncio
async def test_webhook_inbox_persists_complete_unknown_fields() -> None:
    token_id = uuid.uuid4()
    raw_token = "A" * 22
    connection = _Connection([_Result(row=1), _Result(row=(token_id,)), _Result(rowcount=1)])
    update = TelegramWebhookUpdate.model_validate(
        {
            "update_id": 99,
            "callback_query": {
                "id": "cb",
                "data": "a:" + raw_token,
                "from": {"id": 8},
                "message": {"chat": {"id": 7}},
            },
            "future_bot_api_field": {"kept": True},
        }
    )

    inserted = await persist_telegram_update(  # type: ignore[arg-type]
        connection,
        update,
        bot_generation=1,
    )

    assert inserted is True
    stored = json.loads(connection.params[2]["payload"])
    assert stored["update_id"] == 99
    assert stored["future_bot_api_field"] == {"kept": True}
    assert raw_token not in connection.params[2]["payload"]
    assert stored["callback_query"]["data"] == "a:redacted"
    assert stored["callback_query"]["_fb_action_token_id"] == str(token_id)


def test_rate_limit_policy_persists_full_retry_after() -> None:
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    error = TelegramGatewayError(
        method="sendMessage",
        kind=TelegramFailureKind.RATE_LIMITED,
        error_code=429,
        retry_after=137,
    )

    decision = decide_delivery_failure(error, attempt_count=1, max_attempts=8, now=now)

    assert decision.state == "retry"
    assert (decision.scheduled_at - now).total_seconds() == 137


def test_rate_limit_policy_overrides_attempt_exhaustion() -> None:
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    error = TelegramGatewayError(
        method="sendMessage",
        kind=TelegramFailureKind.RATE_LIMITED,
        error_code=429,
        retry_after=137,
    )

    decision = decide_delivery_failure(error, attempt_count=8, max_attempts=8, now=now)

    assert decision.state == "retry"
    assert decision.error_code == "telegram_rate_limited"
    assert (decision.scheduled_at - now).total_seconds() == 137


@pytest.mark.asyncio
async def test_update_send_helper_propagates_gateway_failure_to_inbox_policy() -> None:
    error = TelegramGatewayError(
        method="sendMessage",
        kind=TelegramFailureKind.UNKNOWN,
    )
    client = AsyncMock()
    client.send_message.side_effect = error

    with pytest.raises(TelegramGatewayError) as raised:
        await send_text(client, chat_id=42, text="Ответ")

    assert raised.value is error


@pytest.mark.asyncio
async def test_callback_rate_limit_persists_full_retry_after_without_sleep() -> None:
    connection = _Connection([_Result(rowcount=1)])
    claim = ClaimedTelegramUpdate(
        bot_generation=1,
        update_id=101,
        payload={"callback_query": {"id": "cb"}},
        attempt_count=5,
        lease_token=uuid.uuid4(),
    )

    applied = await mark_telegram_update_failed(  # type: ignore[arg-type]
        _Engine(connection),
        claim=claim,
        error_code="ignored",
        gateway_error=TelegramGatewayError(
            method="answerCallbackQuery",
            kind=TelegramFailureKind.RATE_LIMITED,
            error_code=429,
            retry_after=137,
        ),
    )

    assert applied is True
    assert connection.params[0]["state"] == "retry"
    assert connection.params[0]["delay"] == 137
    assert connection.params[0]["error_code"] == "telegram_rate_limited"


def test_forbidden_revokes_recipient_and_unknown_never_auto_retries() -> None:
    forbidden = decide_delivery_failure(
        TelegramGatewayError(method="sendMessage", kind=TelegramFailureKind.FORBIDDEN),
        attempt_count=1,
        max_attempts=8,
    )
    unknown = decide_delivery_failure(
        TelegramGatewayError(method="sendMessage", kind=TelegramFailureKind.UNKNOWN),
        attempt_count=1,
        max_attempts=8,
    )

    assert forbidden.state == "dead"
    assert forbidden.disable_recipient_delivery is True
    assert unknown.state == "unknown"
    assert unknown.scheduled_at is None


def test_unauthorized_halts_globally_without_dead_lettering_delivery() -> None:
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    decision = decide_delivery_failure(
        TelegramGatewayError(
            method="sendMessage",
            kind=TelegramFailureKind.UNAUTHORIZED,
            error_code=401,
        ),
        attempt_count=1,
        max_attempts=8,
        now=now,
    )

    assert decision.state == "retry"
    assert decision.error_code == "telegram_unauthorized"
    assert decision.scheduled_at == now.replace(minute=5)


def test_recipient_policy_shifts_warning_but_critical_bypasses_quiet_hours() -> None:
    base = datetime(2026, 7, 18, 22, 0, tzinfo=timezone.utc)
    warning = NotificationEventSpec(
        event_type="incident_warning",
        severity="warning",
        facts=NotificationCardFacts(title="Risk"),
        dedupe_key="policy:warning",
        scheduled_at=base,
    )
    critical = warning.model_copy(
        update={
            "event_type": "incident_critical",
            "severity": "critical",
            "dedupe_key": "policy:critical",
        }
    )
    common = {
        "timezone_name": "UTC",
        "min_severity": "warning",
        "categories": {},
        "quiet_hours_start": time(21, 0),
        "quiet_hours_end": time(7, 0),
        "has_incident_slot": False,
    }

    assert recipient_delivery_schedule(warning, **common) == datetime(
        2026, 7, 19, 7, 0, tzinfo=timezone.utc
    )
    assert recipient_delivery_schedule(critical, **common) == base


def test_recipient_policy_applies_category_threshold_and_recovery_slot() -> None:
    base = datetime(2026, 7, 18, 22, 0, tzinfo=timezone.utc)
    incident_id = uuid.uuid4()
    warning = NotificationEventSpec(
        event_type="incident_warning",
        severity="warning",
        facts=NotificationCardFacts(title="Risk"),
        dedupe_key="policy:disabled-category",
        scheduled_at=base,
    )
    recovery = NotificationEventSpec(
        event_type="incident_recovered",
        severity="ok",
        facts=NotificationCardFacts(title="Recovered"),
        dedupe_key="policy:recovery",
        scheduled_at=base,
        incident_id=incident_id,
    )
    common = {
        "timezone_name": "UTC",
        "min_severity": "critical",
        "quiet_hours_start": time(21, 0),
        "quiet_hours_end": time(7, 0),
    }

    assert (
        recipient_delivery_schedule(
            warning,
            categories={"incidents": "off"},
            has_incident_slot=False,
            **common,
        )
        is None
    )
    assert (
        recipient_delivery_schedule(
            recovery,
            categories={},
            has_incident_slot=False,
            **common,
        )
        is None
    )
    assert (
        recipient_delivery_schedule(
            recovery,
            categories={},
            has_incident_slot=True,
            **common,
        )
        == base
    )


def test_worker_event_with_incident_id_uses_incident_preferences() -> None:
    base = datetime(2026, 7, 18, 22, 0, tzinfo=timezone.utc)
    event = NotificationEventSpec(
        event_type="worker_observer_degraded",
        severity="critical",
        facts=NotificationCardFacts(title="Observer degraded"),
        dedupe_key="policy:worker-incident",
        scheduled_at=base,
        incident_id=uuid.uuid4(),
    )

    assert (
        recipient_delivery_schedule(
            event,
            timezone_name="UTC",
            min_severity="critical",
            categories={"system": "off", "incidents": "critical"},
            quiet_hours_start=time(21, 0),
            quiet_hours_end=time(7, 0),
            has_incident_slot=False,
        )
        == base
    )


def test_incident_card_lifecycle_bypasses_threshold_and_quiet_hours_only_with_slot() -> None:
    event_type = "incident_acknowledged"
    base = datetime(2026, 7, 18, 22, 0, tzinfo=timezone.utc)
    lifecycle = NotificationEventSpec(
        event_type=event_type,
        severity="warning",
        facts=NotificationCardFacts(title="Incident lifecycle"),
        dedupe_key=f"policy:{event_type}",
        scheduled_at=base,
        incident_id=uuid.uuid4(),
    )
    common = {
        "timezone_name": "UTC",
        "min_severity": "critical",
        "categories": {},
        "quiet_hours_start": time(21, 0),
        "quiet_hours_end": time(7, 0),
    }

    assert (
        recipient_delivery_schedule(
            lifecycle,
            has_incident_slot=False,
            **common,
        )
        is None
    )
    assert (
        recipient_delivery_schedule(
            lifecycle,
            has_incident_slot=False,
            has_incident_delivery=True,
            **common,
        )
        == base
    )
    assert (
        recipient_delivery_schedule(
            lifecycle,
            has_incident_slot=True,
            **common,
        )
        == base
    )


def test_explicit_reissue_bypasses_slot_threshold_and_quiet_hours() -> None:
    base = datetime(2026, 7, 18, 22, 0, tzinfo=timezone.utc)
    lifecycle = NotificationEventSpec(
        event_type="incident_snapshot_reissued",
        severity="warning",
        facts=NotificationCardFacts(title="Explicit replacement"),
        dedupe_key="policy:incident_snapshot_reissued",
        scheduled_at=base,
        incident_id=uuid.uuid4(),
    )

    assert (
        recipient_delivery_schedule(
            lifecycle,
            timezone_name="UTC",
            min_severity="critical",
            categories={},
            quiet_hours_start=time(21, 0),
            quiet_hours_end=time(7, 0),
            has_incident_slot=False,
            has_incident_delivery=False,
        )
        == base
    )


@pytest.mark.asyncio
async def test_stale_delivery_lease_cannot_disable_recipient_delivery() -> None:
    connection = _Connection(
        [
            _Result(row=datetime(2026, 7, 22, tzinfo=timezone.utc)),
            _Result(rowcount=0),
        ]
    )
    claim = ClaimedNotificationDelivery(
        delivery_id=1,
        bot_generation=7,
        lease_token=uuid.uuid4(),
        attempt_count=1,
        max_attempts=8,
        recipient_id=uuid.uuid4(),
        chat_id=7,
        telegram_user_id=8,
        recipient_role="owner",
        event_id=uuid.uuid4(),
        incident_id=None,
        incident_generation=None,
        incident_status=None,
        event=NotificationEventSpec(
            event_type="incident.opened",
            severity="critical",
            facts=NotificationCardFacts(title="Alert"),
            dedupe_key="incident:stale-lease",
        ),
        slot_message_id=None,
    )

    await mark_delivery_failure(  # type: ignore[arg-type]
        _Engine(connection),
        claim=claim,
        error=TelegramGatewayError(
            method="sendMessage",
            kind=TelegramFailureKind.FORBIDDEN,
        ),
    )

    assert len(connection.statements) == 3
    assert "pg_advisory_xact_lock" in connection.statements[1]
    assert "UPDATE notification_deliveries" in connection.statements[2]
    assert all("telegram_recipient_preferences" not in sql for sql in connection.statements)


def test_target_telegram_workers_have_no_rich_polling_or_redis_path() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            "core/telegram/gateway.py",
            "apps/telegram_delivery_worker/main.py",
            "apps/telegram_update_worker/main.py",
        )
    )

    assert "sendRichMessage" not in sources
    assert "getUpdates" not in sources
    assert "AlertQueue" not in sources
    assert "core.alerts.queue" not in sources

    for relative in (
        "apps/telegram_delivery_worker/main.py",
        "apps/telegram_update_worker/main.py",
    ):
        tree = ast.parse((root / relative).read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imports.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert "core.worker_heartbeat" not in imports
        assert not any(name == "redis" or name.startswith("redis.") for name in imports)


def test_telegram_preferences_validate_timezone_quiet_pair_and_categories() -> None:
    with pytest.raises(ValidationError):
        TelegramRecipientPreferenceRequest(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        TelegramRecipientPreferenceRequest(quiet_hours_start=time(22, 0))
    with pytest.raises(ValidationError):
        TelegramRecipientPreferenceRequest(categories={"incidents": "invalid"})

    valid = TelegramRecipientPreferenceRequest(
        timezone="Europe/Kaliningrad",
        quiet_hours_start=time(22, 0),
        quiet_hours_end=time(7, 0),
        categories={"incidents": "critical", "digests": "off"},
    )
    assert valid.categories["incidents"] == "critical"


def test_openapi_exposes_preferences_and_notification_diagnostics() -> None:
    paths = create_app().openapi()["paths"]
    preference_path = "/api/settings/telegram/recipients/{recipient_id}/preferences"
    assert set(paths[preference_path]) >= {"get", "put"}
    assert "get" in paths["/api/settings/telegram/diagnostics"]
