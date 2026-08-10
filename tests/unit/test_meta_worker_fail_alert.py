# -*- coding: utf-8 -*-
"""Money-failure notifications are owned by the transactional task finalizer."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as mw
from core.meta_api.errors import TokenInvalidError
from core.tasks.queue import Task


def _task(*, attempt_count: int = 10, max_attempts: int = 10) -> Task:
    now = datetime.now(UTC)
    return Task(
        id=99,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key="meta:pause_ad:99",
        payload={"mutation_kind": "pause_ad", "target_id": "777", "ad_account_id": "123"},
        attempt_count=attempt_count,
        max_attempts=max_attempts,
        requested_by="bot_auto_stop",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="money",
        priority=100,
        available_at=now,
        deadline_at=now + timedelta(seconds=30),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000103"),
        lease_token=3,
        lease_expires_at=now + timedelta(minutes=1),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


def _ownership() -> SimpleNamespace:
    return SimpleNamespace(allowed=True, not_found=False, reason="", foreign_ids=[])


@pytest.fixture(autouse=True)
def _fenced_external_boundary(monkeypatch) -> None:
    monkeypatch.setattr(mw, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(mw, "mark_external_call_started", AsyncMock(return_value=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("bad parse"), RuntimeError("unexpected")])
async def test_exhausted_money_failure_has_no_post_commit_send(monkeypatch, error) -> None:
    """The worker finalizes only; mark_failed owns the atomic durable alert."""
    monkeypatch.setattr(mw, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(mw, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        mw,
        "load_meta_snapshot_freshness",
        AsyncMock(return_value=SimpleNamespace(fresh=True)),
    )
    monkeypatch.setattr(mw, "check_mutation_ownership", AsyncMock(return_value=_ownership()))
    monkeypatch.setattr(mw, "execute_mutation", AsyncMock(side_effect=error))
    monkeypatch.setattr(mw, "requeue_task", AsyncMock(return_value=False))
    monkeypatch.setattr(mw, "requeue_unknown_for_reconciliation", AsyncMock(return_value=True))

    await mw.process_one_task(
        object(),
        _task(),
        client=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_token_invalid_marks_terminal_result_for_atomic_incident_projection(
    monkeypatch,
) -> None:
    terminal = AsyncMock(return_value=True)
    monkeypatch.setattr(mw, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(mw, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        mw,
        "load_meta_snapshot_freshness",
        AsyncMock(return_value=SimpleNamespace(fresh=True)),
    )
    monkeypatch.setattr(mw, "check_mutation_ownership", AsyncMock(return_value=_ownership()))
    monkeypatch.setattr(
        mw,
        "execute_mutation",
        AsyncMock(side_effect=TokenInvalidError("session expired", code=190)),
    )
    monkeypatch.setattr(mw, "mark_task_failed", terminal)

    await mw.process_one_task(object(), _task(), client=AsyncMock())

    assert terminal.await_args.kwargs["result"] == {
        "outcome": "REJECTED",
        "reason": "TokenInvalidError",
        "requires_meta_reauth": True,
    }
