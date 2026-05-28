# -*- coding: utf-8 -*-
"""Роутер enable-tasks: список задач на включение объявлений.

Endpoints (с prefix /api от auto-discovery):
    GET /dashboard/enable-tasks — список задач task_type='enable'

Аналогичен disable_tasks, но только read-only список (создание enable-задач
происходит через /dashboard/enable-recommendations/{id}/enable).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.tasks import EnableTaskRowOut
from apps.api.utils.status_mapper import from_frontend_task_status, to_frontend_task_status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_MAX_LIMIT = 500


def _task_row_to_out(row) -> dict:
    """Конвертирует строку task_queue → dict для EnableTaskRowOut."""
    return {
        "id": str(row.id),
        "fb_ad_id": row.fb_ad_id,
        "ad_name": row.ad_name,
        "task_type": row.task_type,
        "status": to_frontend_task_status(row.status),
        "attempt_count": row.attempt_count,
        "max_attempts": row.max_attempts,
        "requested_by": row.requested_by,
        "requested_by_chat_id": row.created_by_chat_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "next_attempt_at": row.next_retry_at,
        "last_error_message": row.last_error,
    }


# ─────────────────────── GET /dashboard/enable-tasks ─────────────────────────


@router.get("/dashboard/enable-tasks", response_model=list[EnableTaskRowOut])
async def list_enable_tasks(
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
    """Список enable-задач с фильтрацией.

    ?status=PENDING,FAILED — CSV uppercase frontend-статусов.
    PENDING разворачивается в ['draft','pending'].
    ?fb_ad_id=12345 — фильтр по payload->>'fb_ad_id'.
    Заголовок X-Total-Count — полный COUNT без LIMIT.
    """
    limit = min(limit, _MAX_LIMIT)

    # Разворачиваем CSV-статусы фронта → db-значения
    db_statuses: list[str] | None = None
    if status:
        raw_statuses = [s.strip() for s in status.split(",") if s.strip()]
        db_statuses = []
        for s in raw_statuses:
            if s.upper() == "PENDING":
                db_statuses.extend(["draft", "pending"])
            else:
                try:
                    db_statuses.append(from_frontend_task_status(s.upper()))
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc

    conditions = ["tq.task_type = 'enable'"]
    params: dict = {"limit": limit, "offset": offset}

    if db_statuses:
        placeholders = ", ".join(f":st{i}" for i in range(len(db_statuses)))
        conditions.append(f"tq.status IN ({placeholders})")
        for i, st in enumerate(db_statuses):
            params[f"st{i}"] = st

    if fb_ad_id:
        conditions.append("tq.payload->>'fb_ad_id' = :fb_ad_id")
        params["fb_ad_id"] = fb_ad_id

    where_clause = " AND ".join(conditions)

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
    return [_task_row_to_out(r) for r in rows]
