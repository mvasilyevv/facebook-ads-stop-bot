# -*- coding: utf-8 -*-
"""Unit (#227): cancel_requested_at доезжает до статуса ``cancelled`` на всех терминальных путях.

Три пути meta_api_worker, которые финализируют задачу без per-target advisory lock:
- _PERMANENT_EXCEPTIONS
- _fail_irreversible (через ValueError на необратимой мутации)
- unretryable browser rejection (#211)

Каждый из них обязан вызывать ``mark_task_failed_or_cancelled``, а не ``mark_task_failed``
напрямую. Именно она берёт advisory lock и проверяет ``cancel_requested_at`` в SQL.

Наблюдения:
1. Задача с ``cancel_requested_at`` → ``mark_task_failed_or_cancelled`` вызвана,
   ``mark_task_failed`` не вызвана.
2. Задача без ``cancel_requested_at`` → то же самое: функция вызвана и возвращает
   ``'failed'``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import apps.meta_api_worker.main as meta
from core.meta_api.errors import (
    BROWSER_OPERATION_REJECTION_REASONS,
    BrowserOperationRejectedError,
    TokenInvalidError,
)
from core.tasks.queue import Task


def _task(kind: str, *, cancel_requested_at=None) -> Task:
    now = datetime.now(UTC)
    lane = "bulk" if kind == "duplicate_adset_structure" else "interactive"
    return Task(
        id=227,
        task_type="meta_api_mutation",
        status="running",
        idempotency_key=f"meta:{kind}:227",
        payload={"mutation_kind": kind, "target_id": "999", "ad_account_id": "456"},
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane=lane,
        priority=0,
        available_at=now,
        deadline_at=now + timedelta(seconds=300),
        lease_owner=uuid.UUID("00000000-0000-0000-0000-000000000227"),
        lease_token=11,
        lease_expires_at=now + timedelta(minutes=2),
        cancel_requested_at=cancel_requested_at,
        cancel_reason="оператор нажал Отмену" if cancel_requested_at else None,
        correlation_id=uuid.uuid4(),
    )


def _unretryable_rejection() -> BrowserOperationRejectedError:
    reason = "capability_signature_invalid"
    return BrowserOperationRejectedError(
        BROWSER_OPERATION_REJECTION_REASONS[reason],
        reason_code=reason,
        endpoint="/act_456/ads",
    )


@pytest.fixture
def _base(monkeypatch):
    """Общая часть патчинга: scanning ON, owner-фильтр OFF, все финализаторы под наблюдением."""
    monkeypatch.setattr(meta, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta, "load_owner_tag", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "_preflight_task_control", AsyncMock(return_value=None))
    monkeypatch.setattr(meta, "mark_external_call_started", AsyncMock(return_value=True))
    monkeypatch.setattr(
        meta,
        "authorize_duplicate_execution_boundary",
        AsyncMock(return_value=True),
    )

    spies = SimpleNamespace(
        failed=AsyncMock(return_value=True),
        failed_or_cancelled=AsyncMock(return_value="failed"),
        requeue=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(meta, "mark_task_failed", spies.failed)
    monkeypatch.setattr(meta, "mark_task_failed_or_cancelled", spies.failed_or_cancelled)
    monkeypatch.setattr(meta, "requeue_task", spies.requeue)
    return spies


# ====================== _PERMANENT_EXCEPTIONS ======================


@pytest.mark.asyncio
async def test_permanent_exception_with_cancel_calls_mark_task_failed_or_cancelled(
    monkeypatch, _base
) -> None:
    """_PERMANENT_EXCEPTIONS: задача с cancel_requested_at → mark_task_failed_or_cancelled."""
    monkeypatch.setattr(
        meta, "execute_mutation", AsyncMock(side_effect=TokenInvalidError("token gone"))
    )
    _base.failed_or_cancelled.return_value = "cancelled"

    await meta.process_one_task(
        object(),
        _task("pause_ad", cancel_requested_at=datetime.now(UTC)),
        client=AsyncMock(),
    )

    _base.failed_or_cancelled.assert_awaited_once()
    _base.failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_permanent_exception_without_cancel_calls_mark_task_failed_or_cancelled(
    monkeypatch, _base
) -> None:
    """_PERMANENT_EXCEPTIONS: задача без cancel → mark_task_failed_or_cancelled, вернула 'failed'."""
    monkeypatch.setattr(
        meta, "execute_mutation", AsyncMock(side_effect=TokenInvalidError("token gone"))
    )
    _base.failed_or_cancelled.return_value = "failed"

    await meta.process_one_task(
        object(),
        _task("pause_ad"),
        client=AsyncMock(),
    )

    _base.failed_or_cancelled.assert_awaited_once()
    _base.failed.assert_not_awaited()


# ====================== _fail_irreversible ======================


@pytest.mark.asyncio
async def test_fail_irreversible_with_cancel_calls_mark_task_failed_or_cancelled(
    monkeypatch, _base
) -> None:
    """_fail_irreversible: задача с cancel_requested_at → mark_task_failed_or_cancelled."""
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=ValueError("post-response id malformed")),
    )
    _base.failed_or_cancelled.return_value = "cancelled"

    await meta.process_one_task(
        object(),
        _task("duplicate_adset_structure", cancel_requested_at=datetime.now(UTC)),
        client=AsyncMock(),
    )

    _base.failed_or_cancelled.assert_awaited_once()
    _base.failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_irreversible_without_cancel_calls_mark_task_failed_or_cancelled(
    monkeypatch, _base
) -> None:
    """_fail_irreversible: задача без cancel → mark_task_failed_or_cancelled, вернула 'failed'."""
    monkeypatch.setattr(
        meta,
        "execute_mutation",
        AsyncMock(side_effect=ValueError("post-response id malformed")),
    )
    _base.failed_or_cancelled.return_value = "failed"

    await meta.process_one_task(
        object(),
        _task("duplicate_adset_structure"),
        client=AsyncMock(),
    )

    _base.failed_or_cancelled.assert_awaited_once()
    _base.failed.assert_not_awaited()


# ====================== unretryable browser rejection (#211) ======================


@pytest.mark.asyncio
async def test_unretryable_rejection_with_cancel_calls_mark_task_failed_or_cancelled(
    monkeypatch, _base
) -> None:
    """Unretryable rejection: задача с cancel_requested_at → mark_task_failed_or_cancelled."""
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=_unretryable_rejection()))
    _base.failed_or_cancelled.return_value = "cancelled"

    await meta.process_one_task(
        object(),
        _task("pause_ad", cancel_requested_at=datetime.now(UTC)),
        client=AsyncMock(),
    )

    _base.failed_or_cancelled.assert_awaited_once()
    _base.failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_unretryable_rejection_without_cancel_calls_mark_task_failed_or_cancelled(
    monkeypatch, _base
) -> None:
    """Unretryable rejection: задача без cancel → mark_task_failed_or_cancelled, вернула 'failed'."""
    monkeypatch.setattr(meta, "execute_mutation", AsyncMock(side_effect=_unretryable_rejection()))
    _base.failed_or_cancelled.return_value = "failed"

    await meta.process_one_task(
        object(),
        _task("pause_ad"),
        client=AsyncMock(),
    )

    _base.failed_or_cancelled.assert_awaited_once()
    _base.failed.assert_not_awaited()
