from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Response, WebSocketDisconnect
from pydantic import ValidationError

import apps.api.routers.v1.operator as operator_router
import apps.api.routers.ws as ws_router
import core.config
import core.db
from apps.api.main import create_app
from apps.api.routers.v1.operator import (
    _ads_section_state,
    _cabinet_risk,
    _currency_groups,
)
from apps.api.routers.v1.schemas.operator import (
    ApiProblem,
    DataState,
    OperatorAdCommandRequest,
    OperatorAttentionAction,
    OperatorAttentionData,
    OperatorCabinetLedgerRow,
    OperatorEconomyTotals,
    OperatorSection,
    OperatorSeverity,
)
from core.operator.queries import _task_item, task_action_kind, task_action_state
from core.public_identifiers import public_uuid
from core.worker_liveness import WORKER_POLL_INTERVAL_SECONDS


@pytest.fixture(autouse=True)
def _healthy_background_workers(monkeypatch):
    """Background workers default to healthy (issue #176).

    Every pre-existing test in this module asserts on the observer/scan half
    of ``_system_section`` and never intended to exercise background-worker
    liveness; without this fixture they would all start failing the moment
    ``_system_section`` gains a ``fetch_worker_heartbeats`` call, because an
    empty/unmocked heartbeat table would legitimately turn every one of them
    ``partial`` (unknown background workers). Scenario-specific tests below
    override this per test.
    """

    def _healthy_rows() -> list[dict]:
        now = datetime.now(UTC)
        return [
            {
                "worker_name": name,
                "last_heartbeat_at": now,
                "last_poll_success_at": now,
            }
            for name in WORKER_POLL_INTERVAL_SECONDS
        ]

    monkeypatch.setattr(
        operator_router,
        "fetch_worker_heartbeats",
        AsyncMock(side_effect=lambda *args, **kwargs: _healthy_rows()),
    )


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


# Причина берётся из записи о завершении задачи, а не из состояния (#206).
# Машинный код в ``result['reason']`` и ``last_error`` — внутренние поля: они
# не становятся текстом для оператора ни при каком состоянии.
@pytest.mark.parametrize(
    ("status", "result", "expected_reason"),
    [
        ("pending", {"reason": "raw pending detail"}, None),
        ("running", {"reason": "raw running detail"}, None),
        ("succeeded", {"outcome": "CONFIRMED", "reason": "raw success detail"}, None),
        ("failed", {"outcome": "REJECTED", "reason": "Traceback: secret-host"}, None),
        ("cancelled", {}, None),
        ("succeeded", {"outcome": "UNKNOWN", "reason": "internal_reconcile_code"}, None),
        (
            "failed",
            {
                "outcome": "REJECTED",
                "reason": "permanent_pre_external_failure",
                "operator_reason": "Шаг: создание объектов кампании. Meta отказала до создания объектов.",
            },
            "Шаг: создание объектов кампании. Meta отказала до создания объектов.",
        ),
    ],
)
def test_task_item_projects_only_the_recorded_operator_reason(
    status, result, expected_reason
) -> None:
    raw_last_error = "password=unsafe database.internal"
    item = _task_item(
        SimpleNamespace(
            id=42,
            task_type="meta_api_mutation",
            status=status,
            payload={"mutation_kind": "pause_ad", "target_id": "ad-1"},
            result=result,
            target_label="Ad",
            created_at=datetime(2026, 8, 8, 10, tzinfo=UTC),
            updated_at=datetime(2026, 8, 8, 10, 1, tzinfo=UTC),
            requested_by="operator:web",
            last_error=raw_last_error,
            correlation_id="00000000-0000-0000-0000-000000000042",
        )
    )

    assert item["reason"] == expected_reason
    if item["reason"] is None:
        return
    assert raw_last_error not in item["reason"]
    if raw_result_reason := result.get("reason"):
        assert str(raw_result_reason) not in item["reason"]


def test_task_item_exposes_target_id_only_from_command_payload() -> None:
    base_row = {
        "id": 42,
        "task_type": "meta_api_mutation",
        "status": "failed",
        "target_label": "Ad",
        "created_at": datetime(2026, 8, 8, 10, tzinfo=UTC),
        "updated_at": datetime(2026, 8, 8, 10, 1, tzinfo=UTC),
        "requested_by": "operator:web",
        "last_error": "target_id=error-secret",
        "correlation_id": "00000000-0000-0000-0000-000000000042",
    }

    with_payload_target = _task_item(
        SimpleNamespace(
            **base_row,
            payload={"mutation_kind": "pause_ad", "target_id": "ad-safe"},
            result={"outcome": "REJECTED", "target_id": "result-secret"},
        )
    )
    without_payload_target = _task_item(
        SimpleNamespace(
            **base_row,
            payload={"mutation_kind": "pause_ad"},
            result={"outcome": "REJECTED", "target_id": "result-secret"},
        )
    )

    assert with_payload_target["target_id"] == "ad-safe"
    assert without_payload_target["target_id"] is None


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


def test_login_required_incident_exposes_only_typed_scan_recovery() -> None:
    now = datetime(2026, 8, 14, 10, tzinfo=UTC)
    base = {
        "id": "00000000-0000-0000-0000-000000000777",
        "severity": "critical",
        "status": "open",
        "title": "В Facebook нужно войти снова",
        "summary": "Кабинет: 777",
        "resource_type": "ad_account",
        "resource_id": "777",
        "resource_label": None,
        "opened_at": now,
    }

    login_item = operator_router._incident_attention_item(
        {**base, "incident_key": "observer:login_required:777"}
    )
    channel_item = operator_router._incident_attention_item(
        {**base, "incident_key": "autostop:channel_down"}
    )

    assert login_item.recovery_action == "retry_scan"
    assert login_item.target.kind == "account"
    assert channel_item.recovery_action is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "created", "expected_status"),
    [("queued", True, 202), ("running", False, 200)],
)
async def test_retry_scan_endpoint_preserves_command_lifecycle(
    monkeypatch,
    state: str,
    created: bool,
    expected_status: int,
) -> None:
    correlation_id = "00000000-0000-0000-0000-000000001842"
    enqueue = AsyncMock(
        return_value=SimpleNamespace(
            task_id=1842,
            state=state,
            created=created,
            correlation_id=correlation_id,
        )
    )
    monkeypatch.setattr(
        operator_router,
        "CommandService",
        lambda _engine: SimpleNamespace(enqueue_scan_retry=enqueue),
    )
    response = Response()

    result = await operator_router.retry_operator_scan(
        engine=object(),
        response=response,
        request=SimpleNamespace(
            state=SimpleNamespace(operator_principal="owner:42"),
        ),
        idempotency_key="scan-retry-key",
        requested_by="untrusted-header",
    )

    assert not isinstance(result, operator_router.JSONResponse)
    assert response.status_code == expected_status
    assert result.task_id == 1842
    assert result.state == state
    assert result.created is created
    assert enqueue.await_args.kwargs["requested_by"] == "owner:42"
    assert enqueue.await_args.kwargs["idempotency_key"].startswith("operator:v1:")


def _cabinet_row(
    *,
    cabinet_id: str,
    currency: str | None,
    spend: str | None,
    base: str | None,
    stop: str | None,
) -> OperatorCabinetLedgerRow:
    return OperatorCabinetLedgerRow(
        id=cabinet_id,
        name=f"act_{cabinet_id}",
        timezone="Europe/Kaliningrad",
        currency=currency,
        state=DataState.READY,
        severity="ok",
        as_of=datetime(2026, 7, 18, 10, 15, tzinfo=UTC),
        freshness_seconds=15,
        cabinet_day={
            "starts_at": datetime(2026, 7, 17, 22, tzinfo=UTC),
            "ends_at": datetime(2026, 7, 18, 22, tzinfo=UTC),
        },
        totals=OperatorEconomyTotals(
            spend=spend,
            base=base,
            stop=stop,
            base_delta=None,
        ),
        risk_label="В пределах порогов",
        risk_reason=None,
        issues=[],
        action=OperatorAttentionAction(
            label="Открыть кабинет",
            href=f"/cabinets/{cabinet_id}",
        ),
    )


def test_portfolio_groups_money_by_currency_and_preserves_known_zero() -> None:
    groups = _currency_groups(
        [
            _cabinet_row(cabinet_id="1", currency="USD", spend="0.00", base="10.00", stop="20.00"),
            _cabinet_row(cabinet_id="2", currency="USD", spend="5.00", base="10.00", stop="20.00"),
            _cabinet_row(cabinet_id="3", currency="EUR", spend="7.00", base="8.00", stop="16.00"),
        ]
    )

    assert [group.currency for group in groups] == ["EUR", "USD"]
    assert groups[0].state == DataState.PARTIAL
    assert groups[0].totals.spend is None
    assert groups[0].cabinets[0].totals.spend is None
    assert groups[1].totals.spend == "5.00"
    assert groups[1].totals.base == "20.00"
    assert groups[1].totals.stop == "40.00"


def test_portfolio_group_total_is_unknown_when_one_cabinet_is_unknown() -> None:
    [group] = _currency_groups(
        [
            _cabinet_row(cabinet_id="1", currency="USD", spend="5.00", base="10.00", stop="20.00"),
            _cabinet_row(cabinet_id="2", currency="USD", spend=None, base="10.00", stop="20.00"),
        ]
    )

    assert group.totals.spend is None
    assert group.totals.base == "20.00"
    assert group.totals.stop == "40.00"
    assert group.totals.base_delta is None


def test_cabinet_risk_keeps_stale_unknown_and_confirmed_stop_critical() -> None:
    totals = OperatorEconomyTotals(spend="30.00", base="15.00", stop="30.00", base_delta="15.00")

    stale = _cabinet_risk(
        state=DataState.STALE,
        totals=totals,
        issues=[],
        currency="USD",
    )
    confirmed = _cabinet_risk(
        state=DataState.READY,
        totals=totals,
        issues=[],
        currency="USD",
    )

    assert stale[0] == "unknown"
    assert confirmed[0] == "critical"
    assert "$30.00" in (confirmed[2] or "")


@pytest.mark.asyncio
async def test_system_section_flags_monitoring_that_covers_nothing(monkeypatch) -> None:
    """Включённый мониторинг с пустым allowlist при одном кабинете — CRITICAL.

    Скан в этом случае не выполняется вовсе (allowlist_blocks_scan), но раньше
    результат приходил как outcome="empty", неотличимый от «активных объявлений
    нет», и секция рисовалась зелёной. Оператор видел исправную систему, пока
    авто-стоп не покрывал ни одного объявления.
    """

    now = datetime.now(UTC)
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_scan_state",
        AsyncMock(
            return_value={
                "enabled": True,
                "last_scan_at": now,
                "last_scan_outcome": "empty",
                "next_scan_at": None,
                "campaign_ids": [],
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
        operator_router, "resolve_configured_ad_account_ids", AsyncMock(return_value=["123"])
    )

    section = await operator_router._system_section(engine=object(), now=now)

    assert section.data is not None
    assert section.data.severity == OperatorSeverity.CRITICAL
    codes = {issue.code for issue in section.issues}
    assert "scan_nothing_monitored" in codes
    assert section.state is not DataState.READY
    assert section.state is not DataState.EMPTY


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
                # Непустой allowlist: иначе секция справедливо уходит в CRITICAL
                # «мониторинг ничего не отслеживает», а этот тест не об этом.
                "campaign_ids": ["c1"],
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
        operator_router, "resolve_configured_ad_account_ids", AsyncMock(return_value=["123"])
    )

    section = await operator_router._system_section(engine=object(), now=now)

    assert section.state == DataState.READY
    assert section.sources == ["postgresql", "cabinet_runtime", "worker_heartbeats"]
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
                # Непустой allowlist: иначе секция справедливо уходит в CRITICAL
                # «мониторинг ничего не отслеживает», а этот тест не об этом.
                "campaign_ids": ["c1"],
                "actors": [],
            }
        ),
    )
    monkeypatch.setattr(
        operator_router, "resolve_configured_ad_account_ids", AsyncMock(return_value=["123"])
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
                # Непустой allowlist: иначе секция справедливо уходит в CRITICAL
                # «мониторинг ничего не отслеживает», а этот тест не об этом.
                "campaign_ids": ["c1"],
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
        "resolve_configured_ad_account_ids",
        AsyncMock(return_value=["123"]),
    )

    section = await operator_router._system_section(engine=object(), now=now)

    assert section.state == DataState.PARTIAL
    assert section.data is not None
    assert section.data.severity == "unknown"
    assert any(issue.code == "monitoring_state_unknown" for issue in section.issues)


def _healthy_scan_state(now: datetime) -> dict:
    """Baseline scan_state so the only source of degradation is a heartbeat row."""
    return {
        "enabled": True,
        "last_scan_at": now,
        "last_scan_outcome": "success",
        "next_scan_at": None,
        "campaign_ids": ["c1"],
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


def _worker_rows(now: datetime, **overrides: dict) -> list[dict]:
    """Healthy rows for every registered worker, overridden per test."""
    rows = {
        name: {"worker_name": name, "last_heartbeat_at": now, "last_poll_success_at": now}
        for name in WORKER_POLL_INTERVAL_SECONDS
    }
    for name, patch in overrides.items():
        rows[name] = {**rows[name], **patch}
    return list(rows.values())


async def _setup_system_section(monkeypatch, *, now: datetime, worker_rows: list[dict]) -> None:
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_scan_state",
        AsyncMock(return_value=_healthy_scan_state(now)),
    )
    monkeypatch.setattr(
        operator_router, "resolve_configured_ad_account_ids", AsyncMock(return_value=["123"])
    )
    monkeypatch.setattr(
        operator_router, "fetch_worker_heartbeats", AsyncMock(return_value=worker_rows)
    )


@pytest.mark.asyncio
async def test_system_section_background_worker_missing_heartbeat_is_unknown(
    monkeypatch,
) -> None:
    """A worker that never wrote a row (fresh deploy, typo'd name) is unknown,
    not silently healthy — ``null`` means unknown, not zero.
    """
    now = datetime.now(UTC)
    rows = [row for row in _worker_rows(now) if row["worker_name"] != "cleanup"]
    await _setup_system_section(monkeypatch, now=now, worker_rows=rows)

    section = await operator_router._system_section(engine=object(), now=now)

    background = {w.id: w for w in section.data.background_workers}
    cleanup = background["worker:cleanup"]
    assert cleanup.status == "unknown"
    assert cleanup.severity == OperatorSeverity.UNKNOWN
    assert cleanup.last_activity_at is None
    assert any(issue.code == "background_worker_missing" for issue in section.issues)
    assert section.data.severity == OperatorSeverity.UNKNOWN
    assert section.state == DataState.PARTIAL


@pytest.mark.asyncio
async def test_system_section_background_worker_dead_process_is_offline_critical(
    monkeypatch,
) -> None:
    """18.08.2026: eleven hours of silence never reached the operator screen.

    A worker whose *heartbeat itself* has gone stale — the process is not
    responding at all — must be CRITICAL and visibly distinct from a healthy
    idle worker.
    """
    now = datetime.now(UTC)
    dead_since = now - timedelta(hours=11)
    rows = _worker_rows(
        now,
        campaign_creator={
            "last_heartbeat_at": dead_since,
            "last_poll_success_at": dead_since,
        },
    )
    await _setup_system_section(monkeypatch, now=now, worker_rows=rows)

    section = await operator_router._system_section(engine=object(), now=now)

    background = {w.id: w for w in section.data.background_workers}
    creator = background["worker:campaign_creator"]
    assert creator.status == "offline"
    assert creator.severity == OperatorSeverity.CRITICAL
    assert section.data.severity == OperatorSeverity.CRITICAL
    assert any(issue.code == "background_worker_offline" for issue in section.issues)


@pytest.mark.asyncio
async def test_system_section_background_worker_stuck_queue_loop_is_critical_and_distinct_from_offline(
    monkeypatch,
) -> None:
    """A process that is alive but stopped polling its own queue is not the
    same failure as a dead process, and neither may be confused with a
    healthy idle worker: this is the exact bug class that hid the incident —
    a decoupled heartbeat coroutine can keep ticking while the real task loop
    is stuck.
    """
    now = datetime.now(UTC)
    rows = _worker_rows(
        now,
        campaign_creator={
            "last_heartbeat_at": now,  # process itself is fine
            "last_poll_success_at": now - timedelta(hours=1),  # queue loop is not
        },
    )
    await _setup_system_section(monkeypatch, now=now, worker_rows=rows)

    section = await operator_router._system_section(engine=object(), now=now)

    background = {w.id: w for w in section.data.background_workers}
    creator = background["worker:campaign_creator"]
    assert creator.status == "stalled"
    assert creator.status != "offline"
    assert creator.severity == OperatorSeverity.CRITICAL
    assert section.data.severity == OperatorSeverity.CRITICAL


@pytest.mark.asyncio
async def test_system_section_background_worker_idle_with_fresh_poll_is_healthy(
    monkeypatch,
) -> None:
    """An idle worker at an empty queue keeps advancing both signals and must
    look healthy, not merely "not yet flagged".
    """
    now = datetime.now(UTC)
    rows = _worker_rows(now)  # everyone healthy, including an "idle" campaign_creator
    await _setup_system_section(monkeypatch, now=now, worker_rows=rows)

    section = await operator_router._system_section(engine=object(), now=now)

    background = {w.id: w for w in section.data.background_workers}
    for worker in background.values():
        assert worker.status == "online"
        assert worker.severity == OperatorSeverity.OK
    assert section.data.severity == OperatorSeverity.OK
    assert section.state == DataState.READY
    assert not any(issue.code.startswith("background_worker_") for issue in section.issues)


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

    cabinet_operation = openapi["paths"]["/api/operator/cabinets/{cabinet_id}/snapshot"]["get"]
    assert cabinet_operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OperatorSnapshot"
    }

    actions_operation = openapi["paths"]["/api/operator/actions"]["get"]
    account_parameter = next(
        parameter
        for parameter in actions_operation["parameters"]
        if parameter["name"] == "account_id"
    )
    assert account_parameter["in"] == "query"
    assert account_parameter["required"] is False
    action_schema = openapi["components"]["schemas"]["OperatorActionItem"]
    assert "target_id" in action_schema["properties"]
    assert "target_id" not in action_schema["required"]

    incidents_operation = openapi["paths"]["/api/operator/incidents"]["get"]
    assert {parameter["name"] for parameter in incidents_operation["parameters"]} >= {
        "account_id",
        "severity",
        "status",
        "page",
        "page_size",
    }
    assert incidents_operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/OperatorIncidentsResponse"
    }


@pytest.mark.asyncio
async def test_operator_actions_scopes_rows_and_evidence_to_requested_cabinet(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    cabinet_days = operator_router.CabinetDayResolution(
        account_ids=("111",),
        timezone_names={"111": "Europe/Kaliningrad"},
        query_boundaries={"111": now.replace(hour=0)},
        missing_account_ids=(),
    )
    currencies = operator_router.AccountCurrencyResolution(
        account_ids=("111",),
        currencies={"111": "USD"},
        observed_at_by_account={"111": now},
        missing_account_ids=(),
    )
    actions_mock = AsyncMock(return_value=([], None, now))
    cabinet_days_mock = AsyncMock(return_value=cabinet_days)
    currencies_mock = AsyncMock(return_value=currencies)
    monkeypatch.setattr(operator_router, "fetch_operator_actions", actions_mock)
    monkeypatch.setattr(operator_router, "resolve_cabinet_days", cabinet_days_mock)
    monkeypatch.setattr(operator_router, "resolve_account_currencies", currencies_mock)

    response = await operator_router.get_operator_actions(
        engine=object(),
        settings=SimpleNamespace(app_timezone="Europe/Kaliningrad"),
        account_id="act_111",
        limit=30,
        before_id=None,
        state=[],
    )

    assert not isinstance(response, operator_router.JSONResponse)
    assert response.scope.account_ids == ["111"]
    assert response.scope.currency == "USD"
    assert response.scope.cabinet_timezone == "Europe/Kaliningrad"
    assert actions_mock.await_args.kwargs["account_id"] == "111"
    assert cabinet_days_mock.await_args.kwargs["account_ids"] == ["111"]
    assert currencies_mock.await_args.kwargs["account_ids"] == ["111"]


@pytest.mark.asyncio
async def test_operator_actions_rejects_empty_canonical_scope_without_unscoped_reads(
    monkeypatch,
) -> None:
    actions_mock = AsyncMock()
    cabinet_days_mock = AsyncMock()
    currencies_mock = AsyncMock()
    monkeypatch.setattr(operator_router, "fetch_operator_actions", actions_mock)
    monkeypatch.setattr(operator_router, "resolve_cabinet_days", cabinet_days_mock)
    monkeypatch.setattr(operator_router, "resolve_account_currencies", currencies_mock)

    response = await operator_router.get_operator_actions(
        engine=object(),
        settings=SimpleNamespace(app_timezone="Europe/Kaliningrad"),
        account_id=" act_ ",
        limit=30,
        before_id=None,
        state=[],
    )

    assert isinstance(response, operator_router.JSONResponse)
    assert response.status_code == 422
    actions_mock.assert_not_awaited()
    cabinet_days_mock.assert_not_awaited()
    currencies_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_operator_incident_list_hides_money_copy_without_confirmed_usd(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    incident_id = "00000000-0000-0000-0000-000000000051"
    cabinet_days = operator_router.CabinetDayResolution(
        account_ids=("111",),
        timezone_names={"111": "Europe/Kaliningrad"},
        query_boundaries={"111": now.replace(hour=0)},
        missing_account_ids=(),
    )
    currencies = operator_router.AccountCurrencyResolution(
        account_ids=("111",),
        currencies={},
        observed_at_by_account={},
        missing_account_ids=("111",),
    )
    incident_row = {
        "id": incident_id,
        "severity": "critical",
        "status": "open",
        "title": "CPL $9.56 > $3.00",
        "summary": "Spend $18.40 · 0 FTD",
        "resource_type": "ad",
        "resource_id": "120001",
        "resource_label": "GH_CR2",
        "ad_account_id": "111",
        "opened_at": now,
        "correlation_id": "00000000-0000-0000-0000-000000000099",
        "facts": {"currency": "USD", "metrics": {"spend": "18.40"}},
    }
    page_mock = AsyncMock(return_value=([incident_row], 1))
    monkeypatch.setattr(operator_router, "fetch_operator_incident_page", page_mock)
    monkeypatch.setattr(
        operator_router,
        "resolve_cabinet_days",
        AsyncMock(return_value=cabinet_days),
    )
    monkeypatch.setattr(
        operator_router,
        "resolve_account_currencies",
        AsyncMock(return_value=currencies),
    )

    response = await operator_router.get_operator_incidents(
        engine=object(),
        settings=SimpleNamespace(app_timezone="Europe/Kaliningrad"),
        account_id="act_111",
        severity=["critical"],
        incident_status=["open"],
        page=1,
        page_size=30,
    )

    assert not isinstance(response, operator_router.JSONResponse)
    assert response.state == DataState.PARTIAL
    assert response.scope.currency_state == "unknown"
    assert response.items[0].title == "Денежный сигнал требует проверки"
    assert response.items[0].summary is not None
    assert "$" not in response.items[0].summary
    assert response.items[0].requires_usd_evidence is True
    assert response.items[0].status == "open"
    assert "00000000-0000-0000-0000-000000000099" not in response.model_dump_json()
    assert page_mock.await_args.kwargs == {
        "account_id": "111",
        "severities": ("critical",),
        "statuses": ("open",),
        "page": 1,
        "page_size": 30,
    }


@pytest.mark.asyncio
async def test_operator_incident_detail_hides_business_copy_without_usd_evidence(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    incident_id = "00000000-0000-0000-0000-000000000052"
    cabinet_days = operator_router.CabinetDayResolution(
        account_ids=("222",),
        timezone_names={"222": "Europe/Kaliningrad"},
        query_boundaries={"222": now.replace(hour=0)},
        missing_account_ids=(),
    )
    currencies = operator_router.AccountCurrencyResolution(
        account_ids=("222",),
        currencies={"222": "EUR"},
        observed_at_by_account={"222": now},
        missing_account_ids=(),
    )
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_incident",
        AsyncMock(
            return_value={
                "id": incident_id,
                "severity": "warning",
                "status": "open",
                "title": "Spend $44.00 выше stop",
                "summary": "CPL $8.80",
                "resource_type": "ad",
                "resource_id": "120002",
                "resource_label": "PL_VIP",
                "ad_account_id": "222",
                "opened_at": now,
                "facts": {"metrics": {"spend": "44.00"}},
            }
        ),
    )
    monkeypatch.setattr(
        operator_router,
        "resolve_cabinet_days",
        AsyncMock(return_value=cabinet_days),
    )
    monkeypatch.setattr(
        operator_router,
        "resolve_account_currencies",
        AsyncMock(return_value=currencies),
    )

    response = await operator_router.get_operator_incident(
        incident_id=operator_router.uuid.UUID(incident_id),
        engine=object(),
        settings=SimpleNamespace(app_timezone="Europe/Kaliningrad"),
    )

    assert not isinstance(response, operator_router.JSONResponse)
    assert response.state == DataState.PARTIAL
    assert response.scope.currency == "EUR"
    assert response.incident.title == "Денежный сигнал требует проверки"
    assert response.incident.summary is not None
    assert "$" not in response.incident.summary
    assert response.incident.status == "open"
    assert any(issue.code == "currency_not_usd" for issue in response.issues)


@pytest.mark.asyncio
async def test_cabinet_snapshot_isolates_actions_and_attention_to_one_cabinet(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    cabinet_days = operator_router.CabinetDayResolution(
        account_ids=("111",),
        timezone_names={"111": "Europe/Kaliningrad"},
        query_boundaries={"111": now.replace(hour=0)},
        missing_account_ids=(),
    )
    currencies = operator_router.AccountCurrencyResolution(
        account_ids=("111",),
        currencies={"111": "USD"},
        observed_at_by_account={"111": now},
        missing_account_ids=(),
    )
    economy = OperatorSection(
        state=DataState.READY,
        as_of=now,
        freshness_seconds=0,
        sources=["meta"],
        issues=[],
        data=operator_router.OperatorEconomyData(
            totals=OperatorEconomyTotals(
                spend="10.00",
                base="20.00",
                stop="30.00",
                base_delta="-10.00",
            ),
            series=[],
        ),
    )
    funnel = OperatorSection(
        state=DataState.READY,
        as_of=now,
        freshness_seconds=0,
        sources=["tracker"],
        issues=[],
        data=operator_router.OperatorFunnelData(stages=[]),
    )
    portfolio = OperatorSection(
        state=DataState.READY,
        as_of=now,
        freshness_seconds=0,
        sources=["meta"],
        issues=[],
        data=operator_router.OperatorPortfolioData(currency_groups=[]),
    )
    system = OperatorSection(
        state=DataState.PARTIAL,
        as_of=now,
        freshness_seconds=0,
        sources=["cabinet_runtime"],
        issues=[
            operator_router.OperatorIssue(
                code="foreign_cabinet_actor_failed",
                title="Cabinet 222: actor завершился с ошибкой",
                detail=None,
                severity="critical",
                correlation_id=None,
            )
        ],
        data=operator_router.OperatorSystemData(
            severity="critical",
            monitoring_enabled=True,
            last_scan_at=now,
            next_scan_at=None,
            workers=[],
            background_workers=[],
        ),
    )
    action_rows = [
        {
            "id": id_,
            "public_id": f"#{id_}",
            "kind": "pause",
            "state": "running",
            "title": "Отключение рекламы",
            "target_label": f"ad-{account_id}",
            "requested_at": now,
            "updated_at": now,
            "requested_by": "owner:test",
            "reason": None,
            "correlation_id": f"correlation-{id_}",
            "account_id": account_id,
            "currency": "USD",
            "cabinet_timezone": "Europe/Kaliningrad",
            "account_context_observed_at": now,
            "account_context_issues": [],
        }
        for id_, account_id in (("1", "111"), ("2", "222"))
    ]
    # operator._incident_attention_item теперь кодирует "id" как непрозрачный
    # публичный идентификатор (core.public_identifiers.public_uuid), поэтому
    # фикстуре нужен настоящий UUID, а не человекочитаемый "incident-111".
    incident_ids = {
        account_id: f"00000000-0000-4000-8000-{account_id:0>12}" for account_id in ("111", "222")
    }
    incidents = [
        {
            "id": incident_ids[account_id],
            "severity": "warning",
            "status": "open",
            "title": f"Incident cabinet {account_id}",
            "summary": f"cabinet {account_id}",
            "resource_type": "account",
            "resource_id": account_id,
            "resource_label": f"act_{account_id}",
            "opened_at": now,
        }
        for account_id in ("111", "222")
    ]

    async def scoped_actions(_engine, *, account_id=None, **_kwargs):
        normalized = account_id.removeprefix("act_") if account_id else None
        visible = [
            row for row in action_rows if normalized is None or row["account_id"] == normalized
        ]
        return visible, None, now

    async def scoped_incidents(_engine, *, account_id, **_kwargs):
        normalized = account_id.removeprefix("act_") if account_id else None
        return [
            incident
            for incident in incidents
            if normalized is None or incident["resource_id"] == normalized
        ]

    monkeypatch.setattr(
        operator_router,
        "_analytics_sections",
        AsyncMock(
            return_value=(
                economy,
                funnel,
                True,
                now.replace(hour=0),
                now,
                cabinet_days,
            )
        ),
    )
    monkeypatch.setattr(operator_router, "_portfolio_section", AsyncMock(return_value=portfolio))
    system_mock = AsyncMock(return_value=system)
    monkeypatch.setattr(operator_router, "_system_section", system_mock)
    actions_mock = AsyncMock(side_effect=scoped_actions)
    monkeypatch.setattr(operator_router, "fetch_operator_actions", actions_mock)
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_incidents",
        AsyncMock(side_effect=scoped_incidents),
    )
    approaching_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(operator_router, "_fetch_approaching_stop_rows", approaching_mock)
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_revision",
        AsyncMock(return_value=(7, "revision-7")),
    )
    monkeypatch.setattr(
        operator_router,
        "_account_meta",
        AsyncMock(return_value={"id": "111", "name": "act_111"}),
    )
    monkeypatch.setattr(
        operator_router,
        "resolve_account_currencies",
        AsyncMock(return_value=currencies),
    )

    snapshot = await operator_router.get_operator_cabinet_snapshot(
        engine=object(),
        settings=SimpleNamespace(app_timezone="Europe/Kaliningrad"),
        cabinet_id="111",
        window="today",
        timezone=None,
    )

    assert not isinstance(snapshot, operator_router.JSONResponse)
    assert snapshot.actions.data is not None
    assert [item.account_id for item in snapshot.actions.data.items] == ["111"]
    assert snapshot.attention.data is not None
    assert {item.id for item in snapshot.attention.data.items} == {
        public_uuid(incident_ids["111"], prefix="inc"),
        "task:1",
    }
    assert snapshot.attention.state == DataState.READY
    assert snapshot.approaching_stop.state == DataState.EMPTY
    assert actions_mock.await_args.kwargs["account_id"] == "111"
    assert system_mock.await_args.kwargs["account_id"] == "111"
    assert approaching_mock.await_args.kwargs["account_id"] == "111"


@pytest.mark.asyncio
async def test_system_section_isolates_cabinet_runtime_evidence(monkeypatch) -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    scan = {
        "enabled": True,
        "last_scan_at": now,
        "next_scan_at": now + timedelta(seconds=30),
        "last_scan_outcome": "success",
        "actors": [
            {
                "ad_account_id": "111",
                "stage": "idle",
                "last_progress_at": now,
                "last_snapshot_at": now,
                "owner_instance": "observer-a",
                "lease_expires_at": now + timedelta(seconds=30),
                "error": None,
            },
            {
                "ad_account_id": "222",
                "stage": "error",
                "last_progress_at": now,
                "last_snapshot_at": now,
                "owner_instance": "observer-b",
                "lease_expires_at": now + timedelta(seconds=30),
                "error": "foreign cabinet failure",
            },
        ],
    }
    scan_state = AsyncMock(return_value=scan)
    configured_accounts = AsyncMock(return_value=["111", "222"])
    monkeypatch.setattr(operator_router, "fetch_operator_scan_state", scan_state)
    monkeypatch.setattr(operator_router, "resolve_configured_ad_account_ids", configured_accounts)

    section = await operator_router._system_section(
        engine=object(),
        now=now,
        account_id="act_111",
    )

    assert section.data is not None
    assert [worker.id for worker in section.data.workers] == ["observer:111"]
    assert all("222" not in issue.title for issue in section.issues)
    assert scan_state.await_args.kwargs == {"account_id": "111"}
    configured_accounts.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_section_never_exposes_raw_cabinet_actor_error(monkeypatch) -> None:
    now = datetime(2026, 8, 8, 12, tzinfo=UTC)
    raw_error = "secret-host.internal:5432 connection refused token=unsafe"
    monkeypatch.setattr(
        operator_router,
        "fetch_operator_scan_state",
        AsyncMock(
            return_value={
                "enabled": True,
                "last_scan_at": now,
                "next_scan_at": None,
                # Непустой allowlist: иначе секция справедливо уходит в CRITICAL
                # «мониторинг ничего не отслеживает», а этот тест не об этом.
                "campaign_ids": ["c1"],
                "last_scan_outcome": "error",
                "actors": [
                    {
                        "ad_account_id": "111",
                        "stage": "error",
                        "last_progress_at": now,
                        "last_snapshot_at": now,
                        "owner_instance": "observer-a",
                        "lease_expires_at": now + timedelta(seconds=30),
                        "error": raw_error,
                    }
                ],
            }
        ),
    )
    monkeypatch.setattr(
        operator_router,
        "resolve_configured_ad_account_ids",
        AsyncMock(return_value=["111"]),
    )

    section = await operator_router._system_section(engine=object(), now=now)

    issue = next(item for item in section.issues if item.code == "cabinet_actor_error")
    assert raw_error not in (issue.detail or "")
    assert "secret-host" not in section.model_dump_json()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["pause_ad", "activate_ad"])
@pytest.mark.parametrize(
    ("state", "created", "expected_status"),
    [
        ("queued", True, 202),
        ("queued", False, 202),
        ("running", False, 200),
        ("confirmed", False, 200),
    ],
)
async def test_operator_money_uses_202_only_for_unfinished_queued_action(
    monkeypatch,
    action: str,
    state: str,
    created: bool,
    expected_status: int,
) -> None:
    correlation_id = "00000000-0000-0000-0000-000000001842"
    enqueue = AsyncMock(
        return_value=SimpleNamespace(
            task_id=1842,
            state=state,
            created=created,
            correlation_id=correlation_id,
        )
    )
    monkeypatch.setattr(
        operator_router,
        "CommandService",
        lambda _engine: SimpleNamespace(enqueue_ad_action=enqueue),
    )
    response = Response()

    result = await operator_router._enqueue_operator_command(
        action=action,
        ad_id="230011223344",
        engine=object(),
        idempotency_key=f"{action}-request",
        requested_by="owner:42",
        response=response,
        precondition=OperatorAdCommandRequest(
            expected_delivery_status="ACTIVE",
            expected_as_of=datetime(2026, 8, 15, 10, tzinfo=UTC),
        ),
    )

    assert not isinstance(result, operator_router.JSONResponse)
    assert response.status_code == expected_status
    assert result.state == state
    # Принятую команду нельзя показать как успех, пока Meta её не подтвердила.
    if response.status_code == 202:
        assert result.state == "queued"


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


def _campaign_create_row(result: dict | None, payload: dict | None = None) -> SimpleNamespace:
    """Строка задачи залива — единственный источник связи действия с запуском."""
    return SimpleNamespace(
        id=20,
        task_type="campaign_create",
        status="failed",
        payload={"account_id": "111", **(payload or {})},
        result=result,
        target_label=None,
        created_at=datetime(2026, 8, 20, 10, tzinfo=UTC),
        updated_at=datetime(2026, 8, 20, 10, 5, tzinfo=UTC),
        requested_by="api_launch",
        last_error="techtext",
        correlation_id="00000000-0000-0000-0000-000000000020",
    )


def test_campaign_action_carries_the_run_it_belongs_to() -> None:
    """Без ссылки на запуск экран действия не может показать сам залив.

    Живая находка 20.08.2026: карточка «создание кампании» показывала
    собственный конвейер, потому что состав залива, созданные объекты и
    управление лежат в запуске, а действие о нём ничего не знало.
    """
    item = _task_item(
        _campaign_create_row(
            {"run_id": "5f1b25c9-1593-4cd5-b39e-068e877d32fa", "outcome": "UNKNOWN"}
        )
    )

    assert item["run_id"] == "5f1b25c9-1593-4cd5-b39e-068e877d32fa"


def test_action_without_a_run_reports_absence_not_a_fabricated_id() -> None:
    """Нет запуска — это ``None``. Пустая строка увела бы экран на несуществующий запуск."""
    assert _task_item(_campaign_create_row(None))["run_id"] is None
    assert _task_item(_campaign_create_row({"outcome": "UNKNOWN"}))["run_id"] is None
    assert _task_item(_campaign_create_row({"run_id": "   "}))["run_id"] is None


def test_run_id_is_rejected_when_it_is_not_an_identifier() -> None:
    """Значение из ``result`` не доверенное: экран строит по нему адрес запуска."""
    assert _task_item(_campaign_create_row({"run_id": "../../etc"}))["run_id"] is None
    assert _task_item(_campaign_create_row({"run_id": 12345}))["run_id"] is None


def test_running_campaign_action_links_to_its_run_before_it_finishes() -> None:
    """Ссылка на залив нужна раньше всего, пока он ещё идёт.

    В ``result`` идентификатор появляется только при финализации, поэтому
    источник связи — ``payload``: там он есть с момента постановки в очередь.
    """
    item = _task_item(
        _campaign_create_row(None, {"run_id": "e02fbf4c-53e3-4451-8d43-2accfb65fbc7"})
    )

    assert item["run_id"] == "e02fbf4c-53e3-4451-8d43-2accfb65fbc7"
