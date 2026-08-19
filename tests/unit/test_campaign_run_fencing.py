from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.campaign_creator_worker import (
    finalize_run_cancelled,
    finalize_run_failed,
    finalize_run_succeeded,
    set_run_status,
)
from core.tasks.queue import Task


class _Result:
    def __init__(self, rowcount: int, *, row=None) -> None:
        self.rowcount = rowcount
        self._row = row

    def first(self):
        return self._row


class _Connection:
    def __init__(self, results: list[int | _Result]) -> None:
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        result = self._results.pop(0)
        if isinstance(result, _Result):
            return result
        return _Result(
            result,
            row=(
                SimpleNamespace(
                    correlation_id=None,
                    payload={"run_id": "run-91"},
                    result={"outcome": "CONFIRMED"},
                )
                if result > 0
                else None
            ),
        )


class _Transaction:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection
        self.exit_exception = None

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, *_args):
        self.exit_exception = exc_type
        return False


class _Engine:
    def __init__(self, results: list[int | _Result]) -> None:
        self.connection = _Connection(results)
        self.transaction = _Transaction(self.connection)

    def begin(self):
        return self.transaction


def _task() -> Task:
    now = datetime.now(UTC)
    return Task(
        id=91,
        task_type="campaign_create",
        status="running",
        idempotency_key="campaign-91",
        payload={"run_id": "run-91"},
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="bulk",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(minutes=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000091"),
        lease_token=17,
        lease_expires_at=now + timedelta(minutes=30),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_campaign_progress_requires_exact_live_task_fence() -> None:
    engine = _Engine([1])

    assert (
        await set_run_status(
            engine,
            "run-91",
            "creating",
            task=_task(),
            progress={"stage": "creating"},
        )
        is True
    )

    sql, params = engine.connection.calls[0]
    assert "EXISTS" in sql
    assert "tq.status = 'running'" in sql
    assert "tq.lease_owner = :lease_owner" in sql
    assert "tq.lease_token = :lease_token" in sql
    assert "tq.lease_expires_at > clock_timestamp()" in sql
    assert "tq.cancel_requested_at IS NULL" in sql
    assert "tq.payload->>'run_id'" in sql
    assert params["lease_token"] == 17


@pytest.mark.asyncio
async def test_campaign_success_finalizes_run_and_task_in_one_transaction() -> None:
    engine = _Engine([1, 1])

    applied = await finalize_run_succeeded(
        engine,
        "run-91",
        task=_task(),
        created_meta_ids={"campaigns": ["c1"]},
        progress={"stage": "succeeded"},
    )

    assert applied is True
    assert len(engine.connection.calls) == 2
    run_sql, _ = engine.connection.calls[0]
    task_sql, task_params = engine.connection.calls[1]
    assert "UPDATE campaign_run" in run_sql
    assert "UPDATE task_queue" in task_sql
    assert "tq.lease_expires_at > clock_timestamp()" in run_sql
    assert "lease_expires_at > clock_timestamp()" in task_sql
    assert "lease_owner = :lease_owner" in task_sql
    assert "RETURNING correlation_id, payload, result" in task_sql
    assert json.loads(task_params["result"])["outcome"] == "CONFIRMED"
    assert engine.transaction.exit_exception is None


@pytest.mark.asyncio
async def test_stale_success_fence_rolls_back_both_rows() -> None:
    engine = _Engine([1, 0])

    applied = await finalize_run_succeeded(
        engine,
        "run-91",
        task=_task(),
        created_meta_ids={"campaigns": ["c1"]},
        progress={"stage": "succeeded"},
    )

    assert applied is False
    assert engine.transaction.exit_exception is not None


@pytest.mark.asyncio
async def test_campaign_unknown_failure_is_atomic_and_fenced(monkeypatch) -> None:
    import apps.campaign_creator_worker as persistence

    projection = AsyncMock()
    monkeypatch.setattr(
        persistence,
        "transition_terminal_task_in_transaction",
        projection,
    )
    engine = _Engine(
        [
            1,
            _Result(
                1,
                row=SimpleNamespace(
                    correlation_id=None,
                    payload={"run_id": "run-91"},
                    result={
                        "outcome": "UNKNOWN",
                        "manual_review_required": True,
                        "reconcile_required": True,
                    },
                ),
            ),
        ]
    )

    applied = await finalize_run_failed(
        engine,
        "run-91",
        task=_task(),
        error="response lost",
        task_result={
            "outcome": "UNKNOWN",
            "manual_review_required": True,
            "reconcile_required": True,
        },
    )

    assert applied is True
    task_sql, task_params = engine.connection.calls[1]
    assert "lease_owner = :lease_owner" in task_sql
    assert "lease_token = :lease_token" in task_sql
    assert "lease_expires_at > clock_timestamp()" in task_sql
    assert json.loads(task_params["task_result"])["outcome"] == "UNKNOWN"
    projection.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_create_opens_reconciliation_incident_in_same_transaction(
    monkeypatch,
) -> None:
    import apps.campaign_creator_worker as persistence

    engine = _Engine(
        [
            1,
            _Result(
                1,
                row=SimpleNamespace(
                    correlation_id=None,
                    payload={"run_id": "run-91"},
                    result={
                        "outcome": "UNKNOWN",
                        "reconcile_required": True,
                        "created_ids": {"campaigns": ["c1"], "adsets": ["s1", "s2"]},
                    },
                ),
            ),
        ]
    )
    projection = AsyncMock()
    monkeypatch.setattr(
        persistence,
        "transition_terminal_task_in_transaction",
        projection,
    )

    applied = await finalize_run_failed(
        engine,
        "run-91",
        task=_task(),
        error="partial",
        created_meta_ids={"campaigns": ["c1"], "adsets": ["s1", "s2"]},
        task_result={"outcome": "UNKNOWN", "reconcile_required": True},
    )

    assert applied is True
    projection.assert_awaited_once_with(
        engine.connection,
        task_id=91,
        correlation_id=None,
        phase="unknown",
        payload={"run_id": "run-91"},
        result={
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "created_ids": {"campaigns": ["c1"], "adsets": ["s1", "s2"]},
        },
        requested_by="test",
        lane="bulk",
        task_type="campaign_create",
    )
    assert engine.transaction.exit_exception is None


@pytest.mark.asyncio
async def test_uncorrelated_campaign_unknown_opens_durable_incident(monkeypatch) -> None:
    import core.tasks.queue as queue
    import core.telegram.worker_notify as worker_notify

    correlation_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    incident = AsyncMock(return_value=True)
    monkeypatch.setattr(
        queue,
        "_transition_correlated_incident",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        worker_notify,
        "notify_recurring_incident_in_transaction",
        incident,
    )
    connection = object()

    await queue.transition_terminal_task_in_transaction(
        connection,  # type: ignore[arg-type]
        task_id=91,
        correlation_id=correlation_id,
        phase="unknown",
        payload={"run_id": "run-91"},
        result={"outcome": "UNKNOWN", "reconcile_required": True},
        requested_by="test",
        lane="bulk",
        task_type="campaign_create",
    )

    incident.assert_awaited_once()
    call = incident.await_args
    assert call.args == (connection,)
    kwargs = call.kwargs
    # Ключи идемпотентности фиксированы: от них зависит склейка карточек.
    assert kwargs["incident_key"] == "campaign-create:run-91:unknown"
    assert kwargs["audience"] == "owners"
    assert kwargs["event_type"] == "campaign_create_reconciliation_required"
    assert kwargs["severity"] == "critical"
    assert kwargs["resource_type"] == "campaign_run"
    assert kwargs["resource_id"] == "run-91"
    assert kwargs["correlation_id"] == correlation_id
    # Текст: что случилось, номер задачи и конкретное действие оператора.
    assert "сверк" in kwargs["title"].lower()
    assert "подтверждение потеряно" in kwargs["summary"]
    assert any("#91" in line for line in kwargs["lines"])
    assert any("Ads Manager" in line for line in kwargs["lines"])
    assert "повтор" in kwargs["risk"].lower()


async def _campaign_unknown_incident(monkeypatch, result: dict) -> dict:
    """Открывает инцидент кампании с заданным result и отдаёт карточку."""
    import core.tasks.queue as queue
    import core.telegram.worker_notify as worker_notify

    incident = AsyncMock(return_value=True)
    monkeypatch.setattr(queue, "_transition_correlated_incident", AsyncMock(return_value=False))
    monkeypatch.setattr(worker_notify, "notify_recurring_incident_in_transaction", incident)

    await queue.transition_terminal_task_in_transaction(
        object(),  # type: ignore[arg-type]
        task_id=91,
        correlation_id=None,
        phase="unknown",
        payload={"run_id": "run-91"},
        result=result,
        requested_by="test",
        lane="bulk",
        task_type="campaign_create",
    )
    incident.assert_awaited_once()
    return incident.await_args.kwargs


# Отказ до отправки в Facebook читается в карточке: оператор видит, что сверх
# перечисленного ничего не создавалось. Сверка и риск дубля при этом остаются.
@pytest.mark.asyncio
async def test_campaign_incident_shows_failure_happened_before_meta_dispatch(monkeypatch) -> None:
    kwargs = await _campaign_unknown_incident(
        monkeypatch,
        {
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "created_ids": {"campaigns": ["c1"], "adsets": ["s1"]},
            "pre_dispatch": True,
        },
    )

    text = " ".join([kwargs["summary"], *kwargs["lines"]]).lower()
    assert "до отправки" in text
    assert "подтверждение потеряно" not in text
    assert any("Ads Manager" in line for line in kwargs["lines"])
    assert "повтор" in kwargs["risk"].lower()
    assert kwargs["severity"] == "critical"


# Без доказательства (признака нет) карточка ничего не утверждает про отправку.
@pytest.mark.asyncio
async def test_campaign_incident_without_proof_claims_nothing_about_dispatch(monkeypatch) -> None:
    kwargs = await _campaign_unknown_incident(
        monkeypatch,
        {
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "created_ids": {"campaigns": ["c1"], "adsets": ["s1"]},
        },
    )

    text = " ".join([kwargs["summary"], *kwargs["lines"]]).lower()
    assert "до отправки" not in text
    assert any("Ads Manager" in line for line in kwargs["lines"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finalizer", "result_payload", "expected_phase"),
    [
        ("succeeded", {"outcome": "CONFIRMED"}, "confirmed"),
        (
            "failed",
            {"outcome": "UNKNOWN", "reconcile_required": True},
            "unknown",
        ),
        ("cancelled", {"outcome": "REJECTED"}, "cancelled"),
    ],
)
async def test_campaign_terminal_transition_calls_incident_hook_in_same_transaction(
    monkeypatch,
    finalizer: str,
    result_payload: dict,
    expected_phase: str,
) -> None:
    import apps.campaign_creator_worker as persistence

    correlation_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
    task_row = SimpleNamespace(
        correlation_id=correlation_id,
        payload={"run_id": "run-91", "mutation_kind": "campaign_create"},
        result=result_payload,
    )
    engine = _Engine([1, _Result(1, row=task_row)])
    correlated_hook = AsyncMock()
    terminal_hook = AsyncMock()
    monkeypatch.setattr(
        persistence,
        "transition_correlated_incident_in_transaction",
        correlated_hook,
    )
    monkeypatch.setattr(
        persistence,
        "transition_terminal_task_in_transaction",
        terminal_hook,
    )

    if finalizer == "succeeded":
        applied = await finalize_run_succeeded(
            engine,
            "run-91",
            task=_task(),
            created_meta_ids={"campaigns": ["c1"]},
            progress={"stage": "succeeded"},
        )
    elif finalizer == "failed":
        applied = await finalize_run_failed(
            engine,
            "run-91",
            task=_task(),
            error="ambiguous",
            task_result=result_payload,
        )
    else:
        applied = await finalize_run_cancelled(
            engine,
            "run-91",
            task=_task(),
            reason="operator cancelled",
        )

    assert applied is True
    if finalizer == "failed":
        terminal_hook.assert_awaited_once_with(
            engine.connection,
            task_id=91,
            correlation_id=correlation_id,
            phase=expected_phase,
            payload={"run_id": "run-91", "mutation_kind": "campaign_create"},
            result=result_payload,
            requested_by="test",
            lane="bulk",
            task_type="campaign_create",
        )
        correlated_hook.assert_not_awaited()
    else:
        correlated_hook.assert_awaited_once_with(
            engine.connection,
            task_id=91,
            correlation_id=correlation_id,
            phase=expected_phase,
            payload={"run_id": "run-91", "mutation_kind": "campaign_create"},
        )
        terminal_hook.assert_not_awaited()
    assert engine.transaction.exit_exception is None
