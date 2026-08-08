from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect
from pydantic import ValidationError

import apps.api.routers.v1.operator as operator_router
import apps.api.routers.ws as ws_router
import core.config
import core.db
from apps.api.main import create_app
from apps.api.routers.v1.operator import _ads_section_state
from apps.api.routers.v1.schemas.operator import (
    ApiProblem,
    DataState,
    OperatorAttentionData,
    OperatorSection,
)
from core.operator.queries import task_action_kind, task_action_state


def test_campaign_run_notify_scope_preserves_only_a_valid_opaque_id() -> None:
    run_id = "9AF0A7AC-1E0D-4BDD-9F5A-EC2CBA8EC156"

    assert (
        ws_router._operator_event_scope({"scope": "campaign_run", "id": run_id})
        == "campaign_run:9af0a7ac-1e0d-4bdd-9f5a-ec2cba8ec156"
    )
    assert (
        ws_router._operator_event_scope({"scope": "campaign_run", "id": "not-a-uuid"})
        == "campaign_run"
    )
    assert ws_router._operator_event_scope({"scope": "task", "id": "1842"}) == "task"


@pytest.mark.parametrize(
    ("status", "result", "expected"),
    [
        ("pending", None, "queued"),
        ("retrying", {"outcome": "REJECTED"}, "queued"),
        ("retrying", {"outcome": "UNKNOWN", "reconcile_required": True}, "unknown"),
        ("running", {"reconcile_required": True}, "unknown"),
        ("running", None, "running"),
        ("succeeded", {"outcome": "CONFIRMED"}, "confirmed"),
        ("succeeded", {"outcome": "UNKNOWN"}, "unknown"),
        ("succeeded", {"outcome": "success"}, "unknown"),
        ("succeeded", None, "unknown"),
        ("failed", {"outcome": "UNKNOWN"}, "unknown"),
        ("failed", {"outcome": "REJECTED"}, "failed"),
        ("cancelled", None, "cancelled"),
    ],
)
def test_action_state_contract(status, result, expected) -> None:
    assert task_action_state(status, result) == expected


@pytest.mark.parametrize(
    ("task_type", "payload", "expected"),
    [
        ("meta_api_mutation", {"mutation_kind": "pause_ad"}, "pause"),
        ("meta_api_mutation", {"mutation_kind": "activate_ad"}, "activate"),
        ("observer_scan", {}, "scan"),
        ("campaign_create", {}, "create"),
    ],
)
def test_action_kind_contract(task_type, payload, expected) -> None:
    assert task_action_kind(task_type, payload) == expected


def test_operator_section_fields_are_required_even_when_nullable() -> None:
    schema = OperatorSection[OperatorAttentionData].model_json_schema()
    assert set(schema["required"]) == {
        "state",
        "as_of",
        "freshness_seconds",
        "sources",
        "issues",
        "data",
    }
    with pytest.raises(ValidationError):
        OperatorSection[OperatorAttentionData](state=DataState.UNAVAILABLE)


def test_api_problem_never_omits_nullable_field_errors() -> None:
    assert set(ApiProblem.model_json_schema()["required"]) == {
        "code",
        "message",
        "correlation_id",
        "field_errors",
    }


@pytest.mark.asyncio
async def test_system_section_uses_durable_cabinet_activity(monkeypatch) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_scan_state",
        AsyncMock(
            return_value={
                "enabled": True,
                "last_scan_at": now,
                "last_scan_outcome": "success",
                "next_scan_at": None,
                "actors": [
                    {
                        "ad_account_id": "123",
                        "owner_instance": None,
                        "lease_expires_at": None,
                        "stage": "idle",
                        "last_progress_at": now,
                        "last_snapshot_at": now,
                        "error": None,
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        operator_router, "resolve_scan_account_ids", AsyncMock(return_value=["123"])
    )

    section = await operator_router._system_section(engine=object(), now=now)

    assert section.state == DataState.READY
    assert section.sources == ["postgresql", "cabinet_runtime"]
    assert section.data is not None
    assert section.data.workers[0].last_activity_at == now
    assert section.data.workers[0].status == "online"


@pytest.mark.asyncio
async def test_system_section_never_hides_a_missing_expected_actor(monkeypatch) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_scan_state",
        AsyncMock(
            return_value={
                "enabled": True,
                "last_scan_at": now,
                "last_scan_outcome": "success",
                "next_scan_at": None,
                "actors": [],
            }
        ),
    )
    monkeypatch.setattr(
        operator_router, "resolve_scan_account_ids", AsyncMock(return_value=["123"])
    )

    section = await operator_router._system_section(engine=object(), now=now)

    assert section.state == DataState.PARTIAL
    assert section.data is not None
    assert section.data.severity == "unknown"
    assert section.data.workers[0].status == "unknown"
    assert any(issue.code == "cabinet_runtime_missing" for issue in section.issues)


@pytest.mark.asyncio
async def test_system_section_never_false_greens_unknown_monitoring_state(
    monkeypatch,
) -> None:
    now = datetime.now(UTC)
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_scan_state",
        AsyncMock(
            return_value={
                "enabled": None,
                "last_scan_at": now,
                "last_scan_outcome": "success",
                "next_scan_at": None,
                "actors": [
                    {
                        "ad_account_id": "123",
                        "owner_instance": None,
                        "lease_expires_at": None,
                        "stage": "idle",
                        "last_progress_at": now,
                        "last_snapshot_at": now,
                        "error": None,
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        operator_router,
        "resolve_scan_account_ids",
        AsyncMock(return_value=["123"]),
    )

    section = await operator_router._system_section(engine=object(), now=now)

    assert section.state == DataState.PARTIAL
    assert section.data is not None
    assert section.data.severity == "unknown"
    assert any(issue.code == "monitoring_state_unknown" for issue in section.issues)


@pytest.mark.parametrize(
    (
        "meta_as_of",
        "meta_freshness",
        "meta_status",
        "row_state",
        "total",
        "timezone_known",
        "tracker_available",
        "expected",
    ),
    [
        (datetime.now(UTC), 5, "good", "ready", 2, True, True, DataState.READY),
        (datetime.now(UTC), 5, "good", "ready", 0, True, True, DataState.EMPTY),
        (datetime.now(UTC), 5, "good", "partial", 2, True, True, DataState.PARTIAL),
        (datetime.now(UTC), 120, "degraded", "stale", 2, True, True, DataState.STALE),
        (
            datetime.now(UTC),
            5,
            "good",
            "unavailable",
            2,
            True,
            True,
            DataState.UNAVAILABLE,
        ),
        (datetime.now(UTC), 120, "degraded", "ready", 0, True, True, DataState.STALE),
        (datetime.now(UTC), 5, "good", "ready", 2, True, False, DataState.PARTIAL),
        (None, None, "missing", "ready", 0, True, True, DataState.UNAVAILABLE),
    ],
)
def test_ads_section_state_never_hides_degraded_rows_or_sources(
    meta_as_of,
    meta_freshness,
    meta_status,
    row_state,
    total,
    timezone_known,
    tracker_available,
    expected,
) -> None:
    assert (
        _ads_section_state(
            meta_as_of=meta_as_of,
            meta_freshness=meta_freshness,
            meta_status=meta_status,
            row_state=row_state,
            total=total,
            timezone_known=timezone_known,
            tracker_available=tracker_available,
        )
        == expected
    )


def test_operator_openapi_declares_typed_problem_responses() -> None:
    openapi = create_app().openapi()
    operation = openapi["paths"]["/api/operator/snapshot"]["get"]
    assert set(operation["responses"]) >= {"200", "401", "403", "422", "503"}
    schema = operation["responses"]["422"]["content"]["application/json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/ApiProblem"}
    meta_schema = openapi["components"]["schemas"]["OperatorSnapshotMeta"]
    assert {
        "cabinet_timezone",
        "cabinet_timezone_known",
        "missing_timezone_account_ids",
    } <= set(meta_schema["required"])


def test_operator_money_commands_distinguish_queued_from_existing_lifecycle() -> None:
    schema = create_app().openapi()
    for path in (
        "/api/operator/ads/{ad_id}/pause",
        "/api/operator/ads/{ad_id}/activate",
    ):
        responses = schema["paths"][path]["post"]["responses"]
        assert set(responses) >= {"200", "202", "401", "403", "409", "422", "503"}
        assert responses["200"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/OperatorCommandResponse"
        }
        assert responses["202"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/OperatorCommandResponse"
        }

    action_states = schema["components"]["schemas"]["OperatorActionState"]["enum"]
    assert set(action_states) == {
        "queued",
        "running",
        "confirmed",
        "failed",
        "cancelled",
        "unknown",
    }


class _ListenerConnection:
    def __init__(self) -> None:
        self.added = False
        self.removed = False

    async def add_listener(self, channel, callback) -> None:
        assert channel == "fb_operator_events"
        self.added = True

    async def remove_listener(self, channel, callback) -> None:
        assert channel == "fb_operator_events"
        self.removed = True


class _TerminatingListenerConnection(_ListenerConnection):
    def __init__(self) -> None:
        super().__init__()
        self.termination_callback = None
        self.termination_removed = False

    def add_termination_listener(self, callback) -> None:
        self.termination_callback = callback

    def remove_termination_listener(self, callback) -> None:
        assert callback is self.termination_callback
        self.termination_removed = True


class _WebSocket:
    def __init__(self, connection: _ListenerConnection) -> None:
        self.headers = {}
        self.query_params = {}
        self.app = SimpleNamespace(state=SimpleNamespace(operator_pg_connection=connection))
        self.accepted = False
        self.messages: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int) -> None:
        raise AssertionError(f"unexpected close {code}")

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)
        if len(self.messages) > 1:
            raise WebSocketDisconnect()


class _TerminationWebSocket(_WebSocket):
    def __init__(self, connection: _TerminatingListenerConnection) -> None:
        super().__init__(connection)
        self.connection = connection
        self.closed_with: int | None = None

    async def close(self, code: int) -> None:
        self.closed_with = code

    async def send_json(self, payload: dict) -> None:
        self.messages.append(payload)
        if len(self.messages) == 1:
            assert self.connection.termination_callback is not None
            self.connection.termination_callback(self.connection)


@pytest.mark.asyncio
async def test_operator_ws_starts_with_db_revision_and_contiguous_sequence(
    monkeypatch,
) -> None:
    connection = _ListenerConnection()
    websocket = _WebSocket(connection)
    monkeypatch.setattr(ws_router, "_authorize_websocket", AsyncMock(return_value=True))
    monkeypatch.setattr(
        ws_router,
        "fetch_operator_revision",
        AsyncMock(return_value=(1_000_000, "revision-1")),
    )
    monkeypatch.setattr(ws_router, "_HEARTBEAT_SECONDS", 0.001)
    monkeypatch.setattr(core.db, "get_engine", lambda: object())
    monkeypatch.setattr(
        core.config,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+asyncpg://unused"),
    )

    await ws_router.ws_operator(websocket)  # type: ignore[arg-type]

    assert connection.added is True
    assert connection.removed is True
    assert websocket.accepted is True
    first = websocket.messages[0]
    assert first["type"] == "snapshot_required"
    assert first["sequence"] == 1
    assert first["snapshot_revision"] == "revision-1"
    assert websocket.messages[1]["sequence"] == 2


@pytest.mark.asyncio
async def test_operator_ws_closes_for_reconciliation_when_pg_listener_terminates(
    monkeypatch,
) -> None:
    connection = _TerminatingListenerConnection()
    websocket = _TerminationWebSocket(connection)
    monkeypatch.setattr(ws_router, "_authorize_websocket", AsyncMock(return_value=True))
    monkeypatch.setattr(
        ws_router,
        "fetch_operator_revision",
        AsyncMock(return_value=(1_000_000, "revision-1")),
    )
    monkeypatch.setattr(ws_router, "_HEARTBEAT_SECONDS", 10)
    monkeypatch.setattr(core.db, "get_engine", lambda: object())
    monkeypatch.setattr(
        core.config,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+asyncpg://unused"),
    )

    await ws_router.ws_operator(websocket)  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert websocket.closed_with == 1013
    assert [message["type"] for message in websocket.messages] == ["snapshot_required"]
    assert connection.removed is True
    assert connection.termination_removed is True
