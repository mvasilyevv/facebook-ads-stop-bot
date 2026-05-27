# -*- coding: utf-8 -*-
"""Outbox-обёртка над core/tasks/queue.py для task_type='meta_api_mutation'.

Контракты:
- create_mutation_task: статус 'pending' (executed сразу worker'ом)
- create_draft_task: статус 'draft' (ждёт подтверждения в TG/TMA)
- approve_draft: 'draft' → 'pending'
- cancel_task: любой не-финальный → 'cancelled'
- claim_pending → core.tasks.queue.claim_next_task с фильтром по task_type
- mark_*  → проксируем в core.tasks.queue
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.schemas import MetaMutationPayload
from core.tasks.queue import (
    Task,
    TaskClaim,
    claim_next_task,
    create_task,
    mark_failed,
    mark_succeeded,
    requeue_for_retry,
)

logger = logging.getLogger(__name__)

_TASK_TYPE = "meta_api_mutation"


def default_idempotency_key(
    payload: MetaMutationPayload,
    *,
    requested_by: str,
    salt: str | None = None,
) -> str:
    """Сгенерировать стабильный idempotency_key для mutation.

    Формат: 'meta:{mutation_kind}:{target_id}:{hash}'
    Хеш берёт mutation_kind+target_id+params+requested_by+salt — повторный вызов
    с теми же параметрами вернёт тот же ключ (UNIQUE conflict → no-op).

    salt используется для draft'ов: каждый draft уникален, salt=ISO-timestamp.
    """
    payload_str = json.dumps(
        {
            "kind": payload.mutation_kind,
            "target": payload.target_id,
            "params": payload.params,
            "by": requested_by,
            "salt": salt or "",
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()[:16]
    key = f"meta:{payload.mutation_kind}:{payload.target_id}:{digest}"
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
) -> int | None:
    """Создать meta_api_mutation задачу. None если дубликат по idempotency_key."""
    if status not in ("draft", "pending"):
        raise ValueError(f"create_mutation_task: status='{status}' не поддерживается")
    key = idempotency_key or default_idempotency_key(payload, requested_by=requested_by)
    return await create_task(
        engine,
        task_type=_TASK_TYPE,
        idempotency_key=key,
        payload=payload.to_dict(),
        requested_by=requested_by,
        status=status,
        max_attempts=max_attempts,
    )


async def create_draft_task(
    engine: AsyncEngine,
    *,
    payload: MetaMutationPayload,
    requested_by: str,
    max_attempts: int = 3,
) -> int | None:
    """Создать DRAFT-задачу (для AI tools).

    Каждый draft уникален: idempotency_key содержит timestamp salt.
    """
    salt = datetime.now(timezone.utc).isoformat()
    key = default_idempotency_key(payload, requested_by=requested_by, salt=salt)
    return await create_mutation_task(
        engine,
        payload=payload,
        requested_by=requested_by,
        status="draft",
        idempotency_key=key,
        max_attempts=max_attempts,
    )


# ====================== state transitions ======================


async def approve_draft_task(
    engine: AsyncEngine,
    *,
    task_id: int,
    approved_by: str,
) -> bool:
    """DRAFT → PENDING. Возвращает True если переход состоялся."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'pending',
                    requested_by = :rb,
                    updated_at = NOW()
                WHERE id = :id
                  AND task_type = :tt
                  AND status = 'draft'
                """
            ),
            {"id": int(task_id), "rb": approved_by[:64], "tt": _TASK_TYPE},
        )
    return (result.rowcount or 0) > 0


async def cancel_task(
    engine: AsyncEngine,
    *,
    task_id: int,
    reason: str,
) -> bool:
    """Отменить задачу (любой не-финальный статус → cancelled). True если применили."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'cancelled',
                    completed_at = NOW(),
                    last_error = :err,
                    updated_at = NOW()
                WHERE id = :id
                  AND task_type = :tt
                  AND status NOT IN ('succeeded', 'failed', 'cancelled')
                """
            ),
            {
                "id": int(task_id),
                "err": (reason or "cancelled")[:8000],
                "tt": _TASK_TYPE,
            },
        )
    return (result.rowcount or 0) > 0


# ====================== claim/finalize ======================


async def claim_pending_task(engine: AsyncEngine) -> TaskClaim:
    """Захват одной задачи task_type='meta_api_mutation' из очереди."""
    return await claim_next_task(engine, task_type=_TASK_TYPE)


async def mark_task_succeeded(
    engine: AsyncEngine,
    *,
    task_id: int,
    result: dict[str, Any] | None = None,
) -> None:
    await mark_succeeded(engine, task_id=task_id, result=result)


async def mark_task_failed(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
) -> None:
    await mark_failed(engine, task_id=task_id, error=error)


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
    )


# ====================== inspect ======================


@dataclass(slots=True, frozen=True)
class DraftView:
    """Снимок DRAFT-задачи для UI (TG/TMA Drafts page)."""

    id: int
    payload: MetaMutationPayload
    requested_by: str
    created_at: datetime | None


async def list_drafts(engine: AsyncEngine, *, limit: int = 50) -> list[DraftView]:
    """Список DRAFT meta-mutation задач (для TG /drafts и TMA)."""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, payload, requested_by, created_at
                    FROM task_queue
                    WHERE task_type = :tt AND status = 'draft'
                    ORDER BY created_at DESC
                    LIMIT :lim
                    """
                ),
                {"tt": _TASK_TYPE, "lim": int(limit)},
            )
        ).all()

    out: list[DraftView] = []
    for row in rows:
        raw_payload = row[1]
        if isinstance(raw_payload, str):
            raw_payload = json.loads(raw_payload)
        try:
            payload = MetaMutationPayload.from_dict(raw_payload or {})
        except (KeyError, ValueError):
            logger.warning("Кривой payload в draft task id=%s, пропускаю", row[0])
            continue
        out.append(
            DraftView(
                id=int(row[0]),
                payload=payload,
                requested_by=str(row[2] or ""),
                created_at=row[3],
            )
        )
    return out
