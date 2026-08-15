from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.commands.service as service_module
from core.commands import (
    CommandConflictError,
    CommandIdentityError,
    CommandPreconditionError,
    CommandService,
    principal_scoped_idempotency_key,
)
from core.observer.scan_tasks import ObserverScanReceipt


class _Result:
    def __init__(self, row=None) -> None:
        self._row = row

    def first(self):
        return self._row


def test_operator_idempotency_keys_are_scoped_by_verified_principal() -> None:
    raw_key = "7dc788f1-a771-4404-b8bd-80e207b6655c"
    owner_a = principal_scoped_idempotency_key(
        principal="tma:1001",
        client_key=raw_key,
    )

    assert owner_a == principal_scoped_idempotency_key(
        principal="tma:1001",
        client_key=raw_key,
    )
    assert owner_a != principal_scoped_idempotency_key(
        principal="tma:2002",
        client_key=raw_key,
    )
    assert owner_a != principal_scoped_idempotency_key(
        principal="tma:1001",
        client_key="2ea8bda5-8394-4d5e-889e-f1a9a7754618",
    )
    assert owner_a.startswith("operator:v1:")
    assert len(owner_a) <= 128


@pytest.mark.asyncio
async def test_scan_retry_returns_existing_running_task_without_enqueue(monkeypatch) -> None:
    correlation_id = uuid.uuid4()
    connection = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=1842,
                        status="running",
                        result=None,
                        correlation_id=correlation_id,
                    )
                ),
            ]
        )
    )
    enqueue = AsyncMock(side_effect=AssertionError("duplicate scan enqueue"))
    monkeypatch.setattr(service_module, "enqueue_observer_scan", enqueue)

    receipt = await CommandService(SimpleNamespace()).enqueue_scan_retry(
        requested_by="operator:web",
        idempotency_key="operator:scan:first",
        connection=connection,
    )

    assert receipt.task_id == 1842
    assert receipt.created is False
    assert receipt.state == "running"
    assert receipt.correlation_id == correlation_id
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_retry_promotes_existing_background_task_without_enqueue(monkeypatch) -> None:
    correlation_id = uuid.uuid4()
    connection = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=1842,
                        status="pending",
                        result=None,
                        correlation_id=correlation_id,
                        lane="background",
                        priority=10,
                    )
                ),
                _Result(),
            ]
        )
    )
    enqueue = AsyncMock(side_effect=AssertionError("duplicate scan enqueue"))
    monkeypatch.setattr(service_module, "enqueue_observer_scan", enqueue)

    receipt = await CommandService(SimpleNamespace()).enqueue_scan_retry(
        requested_by="operator:web",
        idempotency_key="operator:scan:promote",
        connection=connection,
    )

    assert receipt.task_id == 1842
    assert receipt.created is False
    assert receipt.state == "queued"
    promote_call = connection.execute.await_args_list[2]
    assert "SET lane = 'interactive'" in str(promote_call.args[0])
    assert promote_call.args[1]["task_id"] == 1842
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_scan_retry_enqueues_one_interactive_task(monkeypatch) -> None:
    correlation_id = uuid.uuid4()
    connection = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        status="pending",
                        result=None,
                        correlation_id=correlation_id,
                    )
                ),
            ]
        )
    )
    enqueue = AsyncMock(
        return_value=ObserverScanReceipt(
            task_id=1843,
            created=True,
            correlation_id=correlation_id,
        )
    )
    monkeypatch.setattr(service_module, "enqueue_observer_scan", enqueue)

    receipt = await CommandService(SimpleNamespace()).enqueue_scan_retry(
        requested_by="operator:web",
        idempotency_key="operator:scan:second",
        connection=connection,
    )

    assert receipt.task_id == 1843
    assert receipt.created is True
    assert receipt.state == "queued"
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["lane"] == "interactive"
    assert enqueue.await_args.kwargs["priority"] == 75
    assert enqueue.await_args.kwargs["connection"] is connection


@pytest.mark.parametrize(
    ("principal", "client_key"),
    [("", "key"), ("operator:web", ""), ("x" * 65, "key"), ("operator:web", "x" * 129)],
)
def test_operator_idempotency_scope_rejects_unbounded_identity(
    principal: str,
    client_key: str,
) -> None:
    with pytest.raises(ValueError):
        principal_scoped_idempotency_key(
            principal=principal,
            client_key=client_key,
        )


@pytest.mark.asyncio
async def test_command_preconditions_must_be_complete_before_transaction() -> None:
    engine = SimpleNamespace(begin=MagicMock(side_effect=AssertionError("must not open")))

    with pytest.raises(ValueError, match="preconditions must be provided together"):
        await CommandService(engine).enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id="230011223344",
            requested_by="operator:web",
            idempotency_key="web:incomplete-precondition",
            expected_as_of=datetime.now(UTC),
        )

    engine.begin.assert_not_called()


@pytest.mark.asyncio
async def test_existing_connection_is_reused_without_nested_transaction(monkeypatch) -> None:
    correlation_id = uuid.uuid4()
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        ad_account_id="act_42",
                        ad_name="Ad",
                        delivery_status="ACTIVE",
                        metrics_as_of=None,
                        cabinet_timezone="UTC",
                        currency="USD",
                        currency_observed_at=datetime.now(UTC),
                    )
                ),
                _Result(SimpleNamespace(idempotency_key="bound")),
            ]
        ),
    )
    engine = SimpleNamespace(begin=MagicMock(side_effect=AssertionError("nested transaction")))
    create = AsyncMock(return_value=734)
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    receipt = await CommandService(engine).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id=" 230011223344 ",
        requested_by="bot_auto_stop",
        idempotency_key="auto:pause_ad:230011223344:token",
        correlation_id=correlation_id,
        max_attempts=15,
        connection=connection,
    )

    assert receipt.task_id == 734
    assert receipt.created is True
    assert receipt.correlation_id == correlation_id
    engine.begin.assert_not_called()
    create.assert_awaited_once()
    kwargs = create.await_args.kwargs
    assert kwargs["connection"] is connection
    assert kwargs["max_attempts"] == 15
    assert kwargs["priority"] == 200
    assert kwargs["correlation_id"] == correlation_id
    assert kwargs["payload"].target_id == "230011223344"
    assert kwargs["payload"].ad_account_id == "42"


@pytest.mark.asyncio
async def test_missing_catalog_account_rejects_without_enqueue(monkeypatch) -> None:
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        ad_account_id=None,
                        delivery_status="ACTIVE",
                        metrics_as_of=None,
                    )
                ),
            ]
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    with pytest.raises(CommandIdentityError, match="command rejected"):
        await CommandService(SimpleNamespace()).enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id="230011223344",
            requested_by="bot_auto_stop",
            idempotency_key="auto:missing-account",
            connection=connection,
        )

    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_kind", "delivery_status", "currency", "currency_evidence"),
    [
        ("pause_ad", "ACTIVE", "EUR", "fresh"),
        ("activate_ad", "PAUSED", "EUR", "fresh"),
        ("pause_ad", "ACTIVE", None, "missing"),
        ("activate_ad", "PAUSED", None, "missing"),
        ("pause_ad", "ACTIVE", "USD", "stale"),
        ("activate_ad", "PAUSED", "USD", "future"),
        ("pause_ad", "ACTIVE", "USD", "naive"),
    ],
)
async def test_operator_command_requires_confirmed_usd_before_enqueue(
    monkeypatch,
    action_kind,
    delivery_status,
    currency,
    currency_evidence,
) -> None:
    evidence_now = datetime.now(UTC)
    currency_observed_at = {
        "fresh": evidence_now,
        "missing": None,
        "stale": evidence_now - timedelta(hours=25),
        "future": evidence_now + timedelta(minutes=6),
        "naive": evidence_now.replace(tzinfo=None),
    }[currency_evidence]
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        ad_account_id="42",
                        ad_name="Ad",
                        delivery_status=delivery_status,
                        metrics_as_of=None,
                        cabinet_timezone="UTC",
                        currency=currency,
                        currency_observed_at=currency_observed_at,
                    )
                ),
            ]
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    with pytest.raises(CommandPreconditionError, match="confirmed USD"):
        await CommandService(SimpleNamespace()).enqueue_ad_action(
            action_kind=action_kind,
            fb_ad_id="230011223344",
            requested_by="operator:web",
            idempotency_key=(f"web:{action_kind}:currency:{currency or 'missing'}"),
            connection=connection,
        )

    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_kind", "delivery_status"),
    [("pause_ad", "ACTIVE"), ("activate_ad", "PAUSED")],
)
async def test_operator_command_enqueues_with_confirmed_usd(
    monkeypatch,
    action_kind,
    delivery_status,
) -> None:
    observed_at = datetime.now(UTC)
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        ad_account_id="42",
                        ad_name="Ad",
                        delivery_status=delivery_status,
                        metrics_as_of=None,
                        cabinet_timezone="UTC",
                        currency="USD",
                        currency_observed_at=observed_at,
                    )
                ),
                _Result(SimpleNamespace(idempotency_key="bound")),
            ]
        ),
    )
    create = AsyncMock(return_value=735)
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    receipt = await CommandService(SimpleNamespace()).enqueue_ad_action(
        action_kind=action_kind,
        fb_ad_id="230011223344",
        requested_by="operator:web",
        idempotency_key=f"web:{action_kind}:currency:usd",
        connection=connection,
    )

    assert receipt.task_id == 735
    payload = create.await_args.kwargs["payload"]
    assert payload.currency == "USD"
    assert payload.account_context_observed_at == observed_at.isoformat()
    assert "currency_unknown" not in payload.account_context_issues


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_kind", "delivery_status"),
    [
        ("activate_ad", "ACTIVE"),
        ("pause_ad", "PAUSED"),
        ("pause_ad", "ARCHIVED"),
        ("activate_ad", None),
    ],
)
async def test_new_command_requires_actionable_delivery_even_without_client_precondition(
    monkeypatch,
    action_kind,
    delivery_status,
) -> None:
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        ad_account_id="42",
                        delivery_status=delivery_status,
                        metrics_as_of=None,
                    )
                ),
            ]
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    with pytest.raises(CommandPreconditionError, match="is not allowed"):
        await CommandService(SimpleNamespace()).enqueue_ad_action(
            action_kind=action_kind,
            fb_ad_id="230011223344",
            requested_by="operator:web",
            idempotency_key=f"web:{action_kind}:{delivery_status}",
            connection=connection,
        )

    create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_by", ["operator:web", "tg:42", "bot_auto_stop"])
async def test_safety_shaped_params_do_not_bypass_shared_delivery_guard(
    monkeypatch,
    requested_by,
) -> None:
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        ad_account_id="42",
                        delivery_status="PAUSED",
                        metrics_as_of=None,
                    )
                ),
            ]
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    with pytest.raises(CommandPreconditionError, match="is not allowed"):
        await CommandService(SimpleNamespace()).enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id="230011223344",
            requested_by=requested_by,
            idempotency_key=f"{requested_by}:unverified-compensation",
            params={
                "safety_compensation": "activation_without_grace",
                "supersedes_activation_task_id": 91,
            },
            connection=connection,
        )

    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_verified_safety_compensation_requires_direct_active_status() -> None:
    engine = SimpleNamespace(begin=MagicMock(side_effect=AssertionError("must not open")))

    with pytest.raises(CommandPreconditionError, match="is not allowed"):
        await CommandService(engine).enqueue_verified_pause_compensation(
            fb_ad_id="230011223344",
            idempotency_key="internal:unverified-status",
            reason="activation_without_grace",
            source_task_id=91,
            observed_delivery_status="PAUSED",
        )

    engine.begin.assert_not_called()


@pytest.mark.asyncio
async def test_verified_safety_compensation_rejects_unknown_recovery_reason() -> None:
    engine = SimpleNamespace(begin=MagicMock(side_effect=AssertionError("must not open")))

    with pytest.raises(ValueError, match="unsupported safety compensation reason"):
        await CommandService(engine).enqueue_verified_pause_compensation(
            fb_ad_id="230011223344",
            idempotency_key="internal:unknown-recovery",
            reason="unknown_recovery",  # type: ignore[arg-type]
            source_task_id=91,
            observed_delivery_status="ACTIVE",
        )

    engine.begin.assert_not_called()


@pytest.mark.asyncio
async def test_opposite_active_action_is_rejected_under_same_target_lock(monkeypatch) -> None:
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=91,
                        correlation_id=uuid.uuid4(),
                        status="running",
                        result=None,
                        action_kind="activate_ad",
                    )
                ),
            ]
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    with pytest.raises(CommandConflictError, match="active activate_ad"):
        await CommandService(SimpleNamespace()).enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id="230011223344",
            requested_by="bot_auto_stop",
            idempotency_key="auto:pause_ad:230011223344:token",
            connection=connection,
        )

    lock_sql = str(connection.execute.await_args_list[0].args[0])
    assert "pg_advisory_xact_lock" in lock_sql
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_active_action_is_shared_across_channels(monkeypatch) -> None:
    correlation_id = uuid.uuid4()
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=91,
                        correlation_id=correlation_id,
                        status="pending",
                        result=None,
                        action_kind="pause_ad",
                    )
                ),
                _Result(),
                _Result(SimpleNamespace(idempotency_key="bound")),
            ]
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    receipt = await CommandService(SimpleNamespace()).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id="230011223344",
        requested_by="bot_auto_stop",
        idempotency_key="auto:pause_ad:230011223344:another-token",
        connection=connection,
    )

    assert receipt.task_id == 91
    assert receipt.created is False
    assert receipt.correlation_id == correlation_id
    budget_sql = str(connection.execute.await_args_list[4].args[0])
    assert "GREATEST(max_attempts" in budget_sql
    assert "GREATEST(priority" in budget_sql
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_unknown_same_action_is_shared_without_revalidation(monkeypatch) -> None:
    correlation_id = uuid.uuid4()
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=93,
                        correlation_id=correlation_id,
                        status="failed",
                        result={"outcome": "UNKNOWN", "reconcile_required": False},
                        action_kind="pause_ad",
                    )
                ),
                _Result(SimpleNamespace(idempotency_key="bound")),
            ]
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    receipt = await CommandService(SimpleNamespace()).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id="230011223344",
        requested_by="operator:web",
        idempotency_key="web:unknown-alias",
        expected_delivery_status="ACTIVE",
        expected_as_of=datetime.now(UTC),
        connection=connection,
    )

    assert receipt == service_module.CommandReceipt(
        task_id=93,
        created=False,
        state="unknown",
        correlation_id=correlation_id,
    )
    assert len(connection.execute.await_args_list) == 5
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_unknown_opposite_action_fails_closed(monkeypatch) -> None:
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=False),
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=94,
                        correlation_id=uuid.uuid4(),
                        status="failed",
                        result={"outcome": "UNKNOWN"},
                        action_kind="activate_ad",
                    )
                ),
            ]
        ),
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    with pytest.raises(CommandConflictError, match="unresolved activate_ad"):
        await CommandService(SimpleNamespace()).enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id="230011223344",
            requested_by="bot_auto_stop",
            idempotency_key="auto:unknown-opposite",
            connection=connection,
        )

    assert len(connection.execute.await_args_list) == 4
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_idempotency_replay_reports_confirmed_not_queued(monkeypatch) -> None:
    correlation_id = uuid.uuid4()
    connection = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=92,
                        bound_action_kind="pause_ad",
                        bound_target_id="230011223344",
                        task_type="meta_api_mutation",
                        payload={"mutation_kind": "pause_ad", "target_id": "230011223344"},
                        correlation_id=correlation_id,
                        status="succeeded",
                        result={"outcome": "CONFIRMED"},
                    )
                ),
                _Result(),
            ]
        )
    )
    monkeypatch.setattr(
        service_module,
        "create_mutation_task",
        AsyncMock(return_value=None),
    )

    receipt = await CommandService(SimpleNamespace()).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id="230011223344",
        requested_by="operator:web",
        idempotency_key="web:terminal-replay",
        connection=connection,
    )

    assert receipt.created is False
    assert receipt.state == "confirmed"


@pytest.mark.asyncio
async def test_rejected_autostop_receipt_creates_a_new_attempt(monkeypatch) -> None:
    incident_correlation_id = uuid.uuid4()
    stale_scan_correlation_id = uuid.uuid4()
    command_key = "auto:pause_ad:230011223344:open-token"
    rejected = SimpleNamespace(
        id=92,
        bound_action_kind="pause_ad",
        bound_target_id="230011223344",
        task_type="meta_api_mutation",
        payload={"mutation_kind": "pause_ad", "target_id": "230011223344"},
        correlation_id=incident_correlation_id,
        status="failed",
        result={"outcome": "REJECTED"},
    )
    connection = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(rejected),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=92,
                        correlation_id=incident_correlation_id,
                        status="failed",
                        result={"outcome": "REJECTED"},
                        action_kind="pause_ad",
                        has_post_evidence=False,
                    )
                ),
                _Result(
                    SimpleNamespace(
                        ad_account_id="42",
                        ad_name="Ad",
                        delivery_status="ACTIVE",
                        metrics_as_of=None,
                        cabinet_timezone="UTC",
                        currency="USD",
                        currency_observed_at=datetime.now(UTC),
                    )
                ),
                _Result(SimpleNamespace(idempotency_key="bound")),
            ]
        )
    )
    create = AsyncMock(return_value=93)
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    receipt = await CommandService(SimpleNamespace()).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id="230011223344",
        requested_by="bot_auto_stop",
        idempotency_key=command_key,
        correlation_id=stale_scan_correlation_id,
        max_attempts=15,
        connection=connection,
    )

    assert receipt == service_module.CommandReceipt(
        task_id=93,
        created=True,
        state="queued",
        correlation_id=incident_correlation_id,
    )
    assert create.await_args.kwargs["idempotency_key"].startswith("auto:pause_ad:retry:")
    assert create.await_args.kwargs["correlation_id"] == incident_correlation_id


@pytest.mark.asyncio
async def test_repeated_scan_reuses_live_replacement_attempt(monkeypatch) -> None:
    incident_correlation_id = uuid.uuid4()
    rejected = SimpleNamespace(
        id=92,
        bound_action_kind="pause_ad",
        bound_target_id="230011223344",
        task_type="meta_api_mutation",
        payload={"mutation_kind": "pause_ad", "target_id": "230011223344"},
        correlation_id=incident_correlation_id,
        status="failed",
        result={"outcome": "REJECTED"},
    )
    connection = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(),
                _Result(rejected),
                _Result(),
                _Result(),
                _Result(
                    SimpleNamespace(
                        id=93,
                        correlation_id=incident_correlation_id,
                        status="pending",
                        result=None,
                        action_kind="pause_ad",
                        has_post_evidence=False,
                    )
                ),
                _Result(),
            ]
        )
    )
    create = AsyncMock()
    monkeypatch.setattr(service_module, "create_mutation_task", create)

    receipt = await CommandService(SimpleNamespace()).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id="230011223344",
        requested_by="bot_auto_stop",
        idempotency_key="auto:pause_ad:230011223344:open-token",
        max_attempts=15,
        connection=connection,
    )

    assert receipt == service_module.CommandReceipt(
        task_id=93,
        created=False,
        state="queued",
        correlation_id=incident_correlation_id,
    )
    create.assert_not_awaited()
    reopen_sql = str(connection.execute.await_args_list[3].args[0])
    assert "resolved_at = NULL" in reopen_sql
    assert "status = 'failed'" in reopen_sql
