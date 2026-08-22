# -*- coding: utf-8 -*-
"""Outbox helpers for ``task_type='meta_api_mutation'``."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import (
    Task,
    TaskClaim,
    checkpoint_duplicate_adset_structure,
    claim_browser_ready_task,
    create_task,
    mark_failed,
    mark_succeeded,
    release_after_browser_readiness_rejection,
    request_task_cancel,
    requeue_duplicate_recovery,
    requeue_for_retry,
    requeue_proven_not_committed,
)
from core.tasks.queue import (
    mark_task_failed_or_cancelled as _mark_task_failed_or_cancelled,
)

_TASK_TYPE = "meta_api_mutation"


def default_idempotency_key(
    payload: MetaMutationPayload,
    *,
    requested_by: str,
) -> str:
    """Сгенерировать стабильный idempotency_key для mutation.

    Формат: 'meta:{mutation_kind}:{ad_account_id}:{target_id}:{hash}'
    Хеш берёт account+mutation_kind+target_id+params+requested_by — повторный вызов
    с теми же параметрами вернёт тот же ключ (UNIQUE conflict → no-op).

    """
    payload_str = json.dumps(
        {
            "kind": payload.mutation_kind,
            "target": payload.target_id,
            "account": payload.ad_account_id,
            "params": payload.params,
            "by": requested_by,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()[:16]
    key = f"meta:{payload.mutation_kind}:{payload.ad_account_id}:{payload.target_id}:{digest}"
    return key[:128]  # match VARCHAR(128) ограничение в БД


# ====================== create ======================


async def create_mutation_task(
    engine: AsyncEngine,
    *,
    payload: MetaMutationPayload,
    requested_by: str,
    status: str = "pending",
    idempotency_key: str | None = None,
    max_attempts: int = 5,
    created_by_chat_id: int | None = None,
    priority: int | None = None,
    correlation_id: uuid.UUID | None = None,
    connection: AsyncConnection | None = None,
) -> int | None:
    """Создать meta_api_mutation задачу. None если дубликат по idempotency_key."""
    if status != "pending":
        raise ValueError(f"create_mutation_task: status='{status}' не поддерживается")
    key = idempotency_key or default_idempotency_key(payload, requested_by=requested_by)
    bulk_target_lock_keys: tuple[str, ...] = ()
    if payload.mutation_kind == "bulk_status_change":
        raw_ad_ids = payload.params.get("ad_ids") or []
        if isinstance(raw_ad_ids, list):
            bulk_target_lock_keys = tuple(
                sorted({str(ad_id).strip() for ad_id in raw_ad_ids if str(ad_id).strip()})
            )
    return await create_task(
        engine,
        task_type=_TASK_TYPE,
        idempotency_key=key,
        payload=payload.to_dict(),
        requested_by=requested_by,
        status=status,
        max_attempts=max_attempts,
        created_by_chat_id=created_by_chat_id,
        priority=priority,
        correlation_id=correlation_id,
        connection=connection,
        target_lock_key=(
            str(payload.target_id) if payload.mutation_kind in {"pause_ad", "activate_ad"} else None
        ),
        target_lock_keys=bulk_target_lock_keys,
    )


async def cancel_task(
    engine: AsyncEngine,
    *,
    task_id: int,
    reason: str,
) -> bool:
    """Request canonical cooperative cancellation for a Meta mutation.

    Pending work closes immediately; a running mutation receives a durable
    cancellation request so its owner can abort/reconcile the external call.
    """
    async with engine.connect() as conn:
        is_meta_mutation = bool(
            await conn.scalar(
                text("SELECT EXISTS (SELECT 1 FROM task_queue WHERE id = :id AND task_type = :tt)"),
                {"id": int(task_id), "tt": _TASK_TYPE},
            )
        )
    if not is_meta_mutation:
        return False
    return await request_task_cancel(
        engine,
        task_id=task_id,
        reason=(reason or "cancelled")[:8000],
    )


# ====================== claim/finalize ======================


async def claim_browser_ready_mutation_task(
    engine: AsyncEngine,
    *,
    lanes: tuple[str, ...],
    worker_id: uuid.UUID | None = None,
    lease_seconds: int = 30 * 60,
) -> TaskClaim:
    """Claim through the fresh durable v5/profile scheduling gate."""
    return await claim_browser_ready_task(
        engine,
        task_type=_TASK_TYPE,
        lanes=lanes,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
    )


async def mark_task_succeeded(
    engine: AsyncEngine,
    *,
    task_id: int,
    result: dict[str, Any] | None = None,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
    transactional_effect: Callable[[AsyncConnection], Awaitable[None]] | None = None,
) -> bool:
    """Прокси к core.tasks.queue.mark_succeeded. См. там про bool-семантику."""
    return await mark_succeeded(
        engine,
        task_id=task_id,
        result=result,
        lease_owner=lease_owner,
        lease_token=lease_token,
        transactional_effect=transactional_effect,
    )


async def mark_task_failed(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
    result: dict[str, Any] | None = None,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
    transactional_effect: Callable[[AsyncConnection], Awaitable[None]] | None = None,
) -> bool:
    """Прокси к core.tasks.queue.mark_failed. См. там про bool-семантику."""
    return await mark_failed(
        engine,
        task_id=task_id,
        error=error,
        result=result,
        lease_owner=lease_owner,
        lease_token=lease_token,
        transactional_effect=transactional_effect,
    )


async def mark_task_failed_or_cancelled(
    engine: AsyncEngine,
    *,
    task_id: int,
    target_lock_key: str,
    error: str,
    result: dict[str, Any] | None = None,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> str | None:
    """Прокси к core.tasks.queue.mark_task_failed_or_cancelled.

    Возвращает итоговый статус (``'failed'`` или ``'cancelled'``) или ``None``.
    """
    return await _mark_task_failed_or_cancelled(
        engine,
        task_id=task_id,
        target_lock_key=target_lock_key,
        error=error,
        result=result,
        lease_owner=lease_owner,
        lease_token=lease_token,
    )


async def checkpoint_duplicate_progress(
    engine: AsyncEngine,
    *,
    task_id: int,
    checkpoint: dict[str, Any],
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    """Persist progress only for a running duplicate_adset_structure task."""
    return await checkpoint_duplicate_adset_structure(
        engine,
        task_id=task_id,
        checkpoint=checkpoint,
        lease_owner=lease_owner,
        lease_token=lease_token,
    )


async def defer_duplicate_recovery(
    engine: AsyncEngine,
    *,
    task_id: int,
    checkpoint: dict[str, Any],
    error: str,
    delay_seconds: int = 60,
    lease_owner: uuid.UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    """Requeue PAUSE-only crash recovery independently of create retry limits."""
    return await requeue_duplicate_recovery(
        engine,
        task_id=task_id,
        checkpoint=checkpoint,
        error=error,
        delay_seconds=delay_seconds,
        lease_owner=lease_owner,
        lease_token=lease_token,
    )


async def requeue_task(
    engine: AsyncEngine,
    *,
    task: Task,
    error: str,
) -> bool:
    """Решить retry vs final fail и обновить запись.

    True — retry поставлен, False — финальный fail.
    """
    return await requeue_for_retry(
        engine,
        task_id=task.id,
        error=error,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
        lane=task.lane,
    )


async def requeue_task_proven_not_committed(
    engine: AsyncEngine,
    *,
    task: Task,
    target_lock_key: str,
    error: str,
) -> str | None:
    """Retry/cancel a browser rejection that provably occurred before Meta I/O."""
    return await requeue_proven_not_committed(
        engine,
        task_id=task.id,
        target_lock_key=target_lock_key,
        error=error,
        attempt_count=task.attempt_count,
        max_attempts=task.max_attempts,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
        lane=task.lane,
    )


async def release_task_after_browser_readiness_rejection(
    engine: AsyncEngine,
    *,
    task: Task,
    target_lock_key: str,
    error: str,
) -> str | None:
    """Return a proven exact-live rejection; money consumes its attempt budget."""
    return await release_after_browser_readiness_rejection(
        engine,
        task=task,
        error=error,
        target_lock_key=target_lock_key,
    )
