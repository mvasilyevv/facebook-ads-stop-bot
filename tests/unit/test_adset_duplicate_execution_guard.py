"""Execution authority tests for irreversible ad-set duplication plans."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta_worker
from core.adset_duplicates.execution_guard import (
    DuplicateExecutionReceiptError,
    _verify_receipt_rows,
    authorize_duplicate_execution_boundary,
)
from core.adset_duplicates.plan_integrity import duplicate_execution_plan_digest
from core.tasks.queue import Task

_PRINCIPAL = "operator-1"


def _signed_payload(**overrides: Any) -> tuple[dict[str, Any], bytes]:
    params: dict[str, Any] = {
        "source_campaign_id": "101",
        "source_adset_id": "201",
        "selected_ad_ids": ["301"],
        "campaign_count": 1,
        "adsets_per_campaign": 1,
        "budget_level": "ABO",
        "daily_budget": "50.00",
        "currency": "USD",
        "currency_exponent": 2,
        "start_time": "2099-07-16T08:00:00Z",
        "campaign_names": ["signed campaign"],
        "adset_names": ["signed adset"],
    }
    params.update(overrides)
    payload = {
        "mutation_kind": "duplicate_adset_structure",
        "target_id": "201",
        "params": params,
        "ad_account_id": "999",
    }
    digest = duplicate_execution_plan_digest(**payload)
    params["plan_digest"] = digest.hex()
    return payload, digest


def _receipt(
    payload: dict[str, Any],
    digest: bytes,
    *,
    principal: str = _PRINCIPAL,
) -> dict[str, Any]:
    return {
        "principal": principal,
        "task_payload": deepcopy(payload),
        "plan_digest": digest,
        "consumed_at": datetime.now(UTC),
    }


def _task(payload: dict[str, Any], *, requested_by: str = _PRINCIPAL) -> Task:
    now = datetime.now(UTC)
    return Task(
        id=91,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key="meta:duplicate-adset:test",
        payload=payload,
        attempt_count=0,
        max_attempts=1,
        requested_by=requested_by,
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="interactive",
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(minutes=2),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000111"),
        lease_token=7,
        lease_expires_at=now + timedelta(minutes=2),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


def _self_sign(payload: dict[str, Any]) -> None:
    payload["params"]["plan_digest"] = duplicate_execution_plan_digest(
        mutation_kind=payload["mutation_kind"],
        target_id=payload["target_id"],
        params=payload["params"],
        ad_account_id=payload["ad_account_id"],
    ).hex()


def _tampered_task(case: str) -> tuple[Task, list[dict[str, Any]]]:
    anchored_payload, anchored_digest = _signed_payload()
    live_payload = deepcopy(anchored_payload)
    requested_by = _PRINCIPAL
    rows = [_receipt(anchored_payload, anchored_digest)]

    if case == "money":
        live_payload["params"]["daily_budget"] = "75.00"
        _self_sign(live_payload)
    elif case == "start":
        live_payload["params"]["start_time"] = "2099-07-17T08:00:00Z"
        _self_sign(live_payload)
    elif case == "account":
        live_payload["ad_account_id"] = "998"
        _self_sign(live_payload)
    elif case == "name":
        live_payload["params"]["campaign_names"] = ["tampered campaign"]
        _self_sign(live_payload)
    elif case == "selection":
        live_payload["params"]["selected_ad_ids"] = ["302"]
        _self_sign(live_payload)
    elif case == "target":
        live_payload["target_id"] = "202"
        _self_sign(live_payload)
    elif case == "digest_missing":
        live_payload["params"].pop("plan_digest")
    elif case == "digest_malformed":
        live_payload["params"]["plan_digest"] = "not-a-sha256"
    elif case == "digest_mismatch":
        live_payload["params"]["plan_digest"] = "0" * 64
    elif case == "principal":
        requested_by = "other-operator"
    elif case == "missing_receipt":
        rows = []
    elif case == "conflicting_receipts":
        conflicting_payload, conflicting_digest = _signed_payload(daily_budget="99.00")
        rows.append(_receipt(conflicting_payload, conflicting_digest))
    else:  # pragma: no cover - test helper contract
        raise AssertionError(f"unknown tamper case: {case}")

    return _task(live_payload, requested_by=requested_by), rows


def test_coherent_duplicate_receipts_authorize_exact_task() -> None:
    payload, digest = _signed_payload()
    rows = [
        _receipt(payload, digest),
        _receipt(payload, digest),
    ]

    _verify_receipt_rows(
        rows,
        task_payload=payload,
        requested_by=_PRINCIPAL,
        require_consumed=True,
    )


@pytest.mark.parametrize(
    "case",
    [
        "money",
        "start",
        "account",
        "name",
        "selection",
        "target",
        "digest_missing",
        "digest_malformed",
        "digest_mismatch",
        "principal",
        "missing_receipt",
        "conflicting_receipts",
    ],
)
def test_receipt_validator_rejects_missing_conflicting_or_tampered_authority(
    case: str,
) -> None:
    task, rows = _tampered_task(case)

    with pytest.raises(DuplicateExecutionReceiptError):
        _verify_receipt_rows(
            rows,
            task_payload=task.payload,
            requested_by=task.requested_by,
            require_consumed=True,
        )


@pytest.mark.asyncio
async def test_boundary_rejects_mismatched_advisory_target_before_database() -> None:
    payload, _digest = _signed_payload()
    task = _task(payload)

    with pytest.raises(
        DuplicateExecutionReceiptError,
        match="advisory lock target does not match claimed task target",
    ):
        await authorize_duplicate_execution_boundary(
            object(),  # type: ignore[arg-type] -- mismatch must fail before DB access
            task_id=task.id,
            task_payload=task.payload,
            requested_by=task.requested_by,
            target_lock_key="different-target",
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )


@pytest.mark.asyncio
async def test_receipt_rejection_is_terminal_before_external_boundary(monkeypatch) -> None:
    payload, _digest = _signed_payload()
    task = _task(payload)
    mark_failed = AsyncMock(return_value=True)
    mark_external_started = AsyncMock(return_value=True)
    atomic_boundary = AsyncMock(
        side_effect=DuplicateExecutionReceiptError("duplicate task receipts conflict")
    )
    client = AsyncMock()
    monkeypatch.setattr(meta_worker, "mark_task_failed", mark_failed)
    monkeypatch.setattr(meta_worker, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta_worker, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta_worker, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(
        meta_worker,
        "authorize_duplicate_execution_boundary",
        atomic_boundary,
    )
    monkeypatch.setattr(
        meta_worker,
        "mark_external_call_started",
        mark_external_started,
    )

    await meta_worker.process_one_task(object(), task, client=client)

    mark_failed.assert_awaited_once()
    assert mark_failed.await_args.kwargs["result"] == {
        "outcome": "REJECTED",
        "reason": "duplicate_plan_integrity",
    }
    atomic_boundary.assert_awaited_once()
    mark_external_started.assert_not_awaited()
    client.execute_graph_call.assert_not_awaited()
    assert task.external_started_at is None


@pytest.mark.asyncio
async def test_recovery_integrity_rejection_preserves_checkpoint_for_manual_incident(
    monkeypatch,
) -> None:
    payload, _digest = _signed_payload()
    task = _task(payload)
    checkpoint = {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "phase": "recovery_retrying",
        "created_ids": {"campaigns": ["501"], "adsets": ["502"], "ads": []},
        "cleanup_failures": [{"id": "502", "error": "timeout"}],
        "recovery_requested": True,
    }
    task.result = checkpoint
    task.external_started_at = datetime.now(UTC)
    mark_failed = AsyncMock(return_value=True)
    atomic_boundary = AsyncMock(
        side_effect=DuplicateExecutionReceiptError("duplicate task has no durable receipt")
    )
    client = AsyncMock()
    monkeypatch.setattr(meta_worker, "mark_task_failed", mark_failed)
    monkeypatch.setattr(meta_worker, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(
        meta_worker,
        "authorize_duplicate_execution_boundary",
        atomic_boundary,
    )

    await meta_worker.process_one_task(object(), task, client=client)

    mark_failed.assert_awaited_once()
    failure = mark_failed.await_args.kwargs["result"]
    assert failure["checkpoint_type"] == "duplicate_adset_structure"
    assert failure["created_ids"] == checkpoint["created_ids"]
    assert failure["outcome"] == "UNKNOWN"
    assert failure["manual_review_required"] is True
    assert failure["phase"] == "recovery_checkpoint_invalid"
    assert failure["recovery_integrity_error"] == "duplicate task has no durable receipt"
    assert task.external_started_at is not None
    client.execute_graph_call.assert_not_awaited()
