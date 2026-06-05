# -*- coding: utf-8 -*-
"""Роутер disable-tasks: CRUD + retry + cancel.

Endpoints (с prefix /api от auto-discovery):
    GET    /dashboard/disable-tasks            — список задач на отключение
    POST   /dashboard/disable-tasks            — создать задачу вручную
    POST   /dashboard/disable-tasks/{id}/retry — повторить failed/cancelled задачу
    DELETE /dashboard/disable-tasks/{id}       — отменить pending/retrying задачу
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.tasks import DisableTaskCreateIn, TaskQueueRowOut
from apps.api.utils.status_mapper import expand_frontend_statuses_csv
from apps.api.utils.task_serializer import task_row_to_out
from core.meta_api.queue import create_mutation_task
from core.meta_api.schemas import MetaMutationPayload

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

# Жёсткий лимит для пагинации
_MAX_LIMIT = 500

# Статусы в которых допустим retry
_RETRYABLE_STATUSES = {"failed", "cancelled"}

# Терминальные статусы (cancel запрещён)
_TERMINAL_STATUSES = {"succeeded", "cancelled"}

# Статусы которые нельзя retry (активные)
_ACTIVE_STATUSES = {"running", "succeeded", "pending"}


# ─────────────────────── GET /dashboard/disable-tasks ────────────────────────


@router.get("/dashboard/disable-tasks", response_model=list[TaskQueueRowOut])
async def list_disable_tasks(
    engine: DepEngine,
    response: Response,
    status: str | None = Query(
        default=None,
        description="CSV UPPERCASE статусов (PENDING,RUNNING,FAILED,...)",
    ),
    fb_ad_id: str | None = Query(default=None, description="Фильтр по Meta ad ID"),
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """Список disable-задач с фильтрацией.

    ?status=PENDING,FAILED — CSV uppercase frontend-статусов.
    PENDING разворачивается в ['draft','pending'] для покрытия обоих db-статусов.
    ?fb_ad_id=12345 — фильтр по payload->>'fb_ad_id'.
    Заголовок X-Total-Count — полный COUNT без LIMIT.
    """
    limit = min(limit, _MAX_LIMIT)

    # Разворачиваем CSV-статусы фронта → db-значения
    try:
        db_statuses = expand_frontend_statuses_csv(status)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Строим SQL динамически
    conditions = ["task_type = 'disable'"]
    params: dict = {"limit": limit, "offset": offset}

    if db_statuses:
        placeholders = ", ".join(f":st{i}" for i in range(len(db_statuses)))
        conditions.append(f"status IN ({placeholders})")
        for i, st in enumerate(db_statuses):
            params[f"st{i}"] = st

    if fb_ad_id:
        conditions.append("payload->>'fb_ad_id' = :fb_ad_id")
        params["fb_ad_id"] = fb_ad_id

    where_clause = " AND ".join(conditions)

    # JOIN fb_ads для ad_name
    query_sql = f"""
        SELECT
            tq.id,
            tq.task_type,
            tq.status,
            tq.payload->>'fb_ad_id' AS fb_ad_id,
            fa.ad_name,
            tq.attempt_count,
            tq.max_attempts,
            tq.requested_by,
            tq.created_by_chat_id,
            tq.created_at,
            tq.updated_at,
            tq.next_retry_at,
            tq.last_error
        FROM task_queue tq
        LEFT JOIN fb_ads fa ON fa.fb_ad_id = tq.payload->>'fb_ad_id'
        WHERE {where_clause}
        ORDER BY tq.created_at DESC
        LIMIT :limit OFFSET :offset
    """

    count_sql = f"""
        SELECT COUNT(*) FROM task_queue tq
        WHERE {where_clause}
    """

    async with engine.connect() as conn:
        rows = (await conn.execute(text(query_sql), params)).fetchall()
        total = (await conn.execute(text(count_sql), params)).scalar() or 0

    response.headers["X-Total-Count"] = str(total)
    return [task_row_to_out(r) for r in rows]


# ─────────────────────── POST /dashboard/disable-tasks ───────────────────────


@router.post("/dashboard/disable-tasks", response_model=TaskQueueRowOut, status_code=201)
async def create_disable_task(
    body: DisableTaskCreateIn,
    engine: DepEngine,
) -> dict:
    """Создать disable-задачу вручную.

    Резолвит fb_ad_id → fb_ads.id (404 если нет).
    Idempotency key уникален per-request через UUID (ручные задачи дублировать можно).
    """
    # Резолвим fb_ad_id → внутренний UUID
    async with engine.connect() as conn:
        ad_row = (
            await conn.execute(
                text("SELECT id, ad_name FROM fb_ads WHERE fb_ad_id = :fid LIMIT 1"),
                {"fid": body.fb_ad_id},
            )
        ).first()

    if ad_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Объявление fb_ad_id={body.fb_ad_id!r} не найдено в fb_ads",
        )

    ad_name = ad_row.ad_name

    # Ручное отключение — через Marketing API (pause_ad), как авто-стоп и кнопки.
    # DOM-канал (task_type='disable') удалён.
    ikey = f"manual:pause_ad:{body.fb_ad_id}:{uuid.uuid4().hex}"

    task_id = await create_mutation_task(
        engine,
        payload=MetaMutationPayload(
            mutation_kind="pause_ad",
            target_id=body.fb_ad_id,
            params={},
            ad_account_id=None,
        ),
        requested_by=body.requested_by,
        status="pending",
        idempotency_key=ikey,
        created_by_chat_id=body.requested_by_chat_id,
    )

    if task_id is None:
        # Коллизия idempotency_key — крайне маловероятно при UUID, но обработаем
        raise HTTPException(status_code=409, detail="Задача с таким idempotency_key уже существует")

    # Читаем свежую запись для ответа
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, task_type, status,
                           payload->>'fb_ad_id' AS fb_ad_id,
                           NULL::text AS ad_name,
                           attempt_count, max_attempts, requested_by,
                           created_by_chat_id, created_at, updated_at,
                           next_retry_at, last_error
                    FROM task_queue WHERE id = :tid
                    """
                ),
                {"tid": task_id},
            )
        ).first()

    if row is None:
        raise HTTPException(status_code=500, detail="Задача создана, но не найдена при чтении")

    result = task_row_to_out(row)
    result["ad_name"] = ad_name  # подставляем ad_name без лишнего JOIN
    return result


# ──────────────── POST /dashboard/disable-tasks/{id}/retry ───────────────────


@router.post("/dashboard/disable-tasks/{task_id}/retry", response_model=TaskQueueRowOut)
async def retry_disable_task(
    task_id: int,
    engine: DepEngine,
) -> dict:
    """Повторить failed/cancelled disable-задачу.

    Переводит статус в 'retrying' и сбрасывает next_retry_at=NOW().
    Attempt_count НЕ инкрементируется — worker сам это делает при claim.
    409 если задача в активном статусе (running/pending/succeeded).
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, task_type, status FROM task_queue
                    WHERE id = :tid LIMIT 1
                    """
                ),
                {"tid": task_id},
            )
        ).first()

    if row is None or row.task_type != "disable":
        raise HTTPException(status_code=404, detail=f"disable-задача id={task_id} не найдена")

    current_status = row.status

    if current_status in _ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя повторить задачу в статусе '{current_status}' — только failed/cancelled",
        )

    if current_status not in _RETRYABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Статус '{current_status}' не поддерживает retry",
        )

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    next_retry_at = NOW(),
                    updated_at = NOW()
                WHERE id = :tid AND status = ANY(:allowed)
                """
            ),
            {"tid": task_id, "allowed": list(_RETRYABLE_STATUSES)},
        )

    # rowcount=0 → статус изменился между SELECT и UPDATE (гонка с воркером)
    if result.rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail="Состояние задачи изменилось — повторите запрос",
        )

    # Читаем обновлённую строку
    async with engine.connect() as conn:
        updated = (
            await conn.execute(
                text(
                    """
                    SELECT tq.id, tq.task_type, tq.status,
                           tq.payload->>'fb_ad_id' AS fb_ad_id,
                           fa.ad_name,
                           tq.attempt_count, tq.max_attempts, tq.requested_by,
                           tq.created_by_chat_id, tq.created_at, tq.updated_at,
                           tq.next_retry_at, tq.last_error
                    FROM task_queue tq
                    LEFT JOIN fb_ads fa ON fa.fb_ad_id = tq.payload->>'fb_ad_id'
                    WHERE tq.id = :tid
                    """
                ),
                {"tid": task_id},
            )
        ).first()

    if updated is None:
        raise HTTPException(status_code=500, detail="Задача не найдена после обновления")

    return task_row_to_out(updated)


# ─────────────────── DELETE /dashboard/disable-tasks/{id} ────────────────────


@router.delete("/dashboard/disable-tasks/{task_id}", status_code=204)
async def cancel_disable_task(
    task_id: int,
    engine: DepEngine,
) -> None:
    """Отменить disable-задачу (soft cancel).

    Переводит status в 'cancelled'.
    404 если задача не найдена или не disable.
    409 если задача уже в терминальном статусе (succeeded/cancelled).
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT id, task_type, status FROM task_queue WHERE id = :tid LIMIT 1"),
                {"tid": task_id},
            )
        ).first()

    if row is None or row.task_type != "disable":
        raise HTTPException(status_code=404, detail=f"disable-задача id={task_id} не найдена")

    if row.status in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя отменить задачу в терминальном статусе '{row.status}'",
        )

    async with engine.begin() as conn:
        cancel_result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'cancelled',
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE id = :tid AND status NOT IN ('succeeded', 'cancelled')
                """
            ),
            {"tid": task_id},
        )

    # rowcount=0 → статус изменился между SELECT и UPDATE (гонка с воркером)
    if cancel_result.rowcount == 0:
        raise HTTPException(
            status_code=409,
            detail="Состояние задачи изменилось — повторите запрос",
        )
