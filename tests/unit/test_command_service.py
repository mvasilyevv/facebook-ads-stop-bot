from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
                        delivery_status="ACTIVE",
                        metrics_as_of=None,
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
            reason="autostart_reconciliation",
            source_task_id=91,
            observed_delivery_status="PAUSED",
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
