# -*- coding: utf-8 -*-
"""Роутер enable-recommendations: просмотр и подтверждение рекомендаций на включение.

Endpoints (с prefix /api от auto-discovery):
    GET  /dashboard/enable-recommendations          — список рекомендаций
    POST /dashboard/enable-recommendations/{id}/enable — создать enable-задачу

Модель EnableRecommendation хранит:
  ad_id       — UUID FK на fb_ads.id (не fb_ad_id напрямую)
  snapshot_metrics — JSONB (в схеме называем metrics_payload)
  recommendation_level — ok/warning (в схеме дублируем как reason)
  live_batch_started_at — datetime

Создание enable-задачи: SELECT FOR UPDATE → INSERT task_queue →
UPDATE promoted_to_task_id в одной транзакции.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.tasks import (
    EnableRecommendationConfirmIn,
    EnableRecommendationRowOut,
    TaskQueueRowOut,
)
from apps.api.utils.status_mapper import to_frontend_task_status
from apps.api.utils.task_serializer import task_row_to_out

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_MAX_LIMIT = 500


def _rec_row_to_out(row) -> dict:
    """Конвертирует строку БД → dict для EnableRecommendationRowOut."""
    promoted_status = None
    if row.promoted_task_status is not None:
        try:
            promoted_status = to_frontend_task_status(row.promoted_task_status)
        except ValueError:
            promoted_status = row.promoted_task_status.upper()

    return {
        "id": str(row.id),
        "fb_ad_id": row.fb_ad_id,
        "ad_name": row.ad_name,
        "campaign_name": row.campaign_name,
        "reason": row.recommendation_level,
        "recommendation_level": row.recommendation_level,
        "metrics_payload": row.snapshot_metrics,
        "created_at": row.created_at,
        "live_batch_started_at": row.live_batch_started_at,
        "promoted_to_task_id": row.promoted_to_task_id,
        "promoted_task_status": promoted_status,
    }


# ───────────────── GET /dashboard/enable-recommendations ─────────────────────


@router.get(
    "/dashboard/enable-recommendations",
    response_model=list[EnableRecommendationRowOut],
)
async def list_enable_recommendations(
    engine: DepEngine,
    status: str | None = Query(
        default="PENDING",
        description="PENDING (без promoted_to_task_id) | PROMOTED (с promoted_to_task_id)",
    ),
    limit: int = Query(default=100, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    """Список рекомендаций на включение.

    ?status=PENDING  — только без promoted_to_task_id (ещё не подтверждены).
    ?status=PROMOTED — только с promoted_to_task_id (уже создана enable-задача).
    JOIN fb_ads для ad_name и fb_ad_id.
    JOIN fb_adsets → fb_campaigns для campaign_name.
    LEFT JOIN task_queue для promoted_task_status.
    """
    limit = min(limit, _MAX_LIMIT)

    status_upper = (status or "PENDING").upper()
    if status_upper == "PENDING":
        status_condition = "er.promoted_to_task_id IS NULL"
    elif status_upper == "PROMOTED":
        status_condition = "er.promoted_to_task_id IS NOT NULL"
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Неизвестный status={status!r}. Допустимые значения: PENDING, PROMOTED",
        )

    query_sql = f"""
        SELECT
            er.id,
            fa.fb_ad_id,
            fa.ad_name,
            fc.campaign_name,
            er.recommendation_level,
            er.snapshot_metrics,
            er.created_at,
            er.live_batch_started_at,
            er.promoted_to_task_id,
            tq.status AS promoted_task_status
        FROM enable_recommendations er
        JOIN fb_ads fa ON fa.id = er.ad_id
        JOIN fb_adsets fas ON fas.id = fa.adset_id
        JOIN fb_campaigns fc ON fc.id = fas.campaign_id
        LEFT JOIN task_queue tq ON tq.id = er.promoted_to_task_id
        WHERE {status_condition}
        ORDER BY er.created_at DESC
        LIMIT :limit OFFSET :offset
    """

    async with engine.connect() as conn:
        rows = (await conn.execute(text(query_sql), {"limit": limit, "offset": offset})).fetchall()

    return [_rec_row_to_out(r) for r in rows]


# ─────────── POST /dashboard/enable-recommendations/{id}/enable ──────────────


@router.post(
    "/dashboard/enable-recommendations/{rec_id}/enable",
    response_model=TaskQueueRowOut,
    status_code=201,
)
async def confirm_enable_recommendation(
    rec_id: uuid.UUID,
    engine: DepEngine,
    body: EnableRecommendationConfirmIn | None = None,
) -> dict:
    """Создать enable-задачу из рекомендации и привязать её.

    SELECT FOR UPDATE защищает от двойного подтверждения.
    409 если рекомендация уже promoted (promoted_to_task_id IS NOT NULL).
    404 если рекомендация не найдена.
    Атомарно: INSERT task_queue + UPDATE enable_recommendations.promoted_to_task_id.
    """
    if body is None:
        body = EnableRecommendationConfirmIn()

    async with engine.begin() as conn:
        # Блокируем строку рекомендации для защиты от race
        rec_row = (
            await conn.execute(
                text(
                    """
                    SELECT er.id, er.ad_id, er.promoted_to_task_id,
                           fa.fb_ad_id, fa.ad_name
                    FROM enable_recommendations er
                    JOIN fb_ads fa ON fa.id = er.ad_id
                    WHERE er.id = :rid
                    FOR UPDATE
                    """
                ),
                {"rid": rec_id},
            )
        ).first()

        if rec_row is None:
            raise HTTPException(
                status_code=404,
                detail=f"Рекомендация id={rec_id} не найдена",
            )

        if rec_row.promoted_to_task_id is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Рекомендация id={rec_id} уже подтверждена (task_id={rec_row.promoted_to_task_id})",
            )

        fb_ad_id = rec_row.fb_ad_id
        ad_id = str(rec_row.ad_id)
        ad_name = rec_row.ad_name

        # Включение — через Marketing API (activate_ad), как кнопка ereco: в TG.
        # Форма payload = MetaMutationPayload (mutation_kind/target_id/params/ad_account_id).
        # from_dict берёт только эти поля; метаданные рекомендации кладём в params
        # для трейсинга (activate_ad-хендлеру нужен лишь target_id). Канал disable/enable
        # воркеров удалён — задачу исполняет meta_api_worker.
        payload = {
            "mutation_kind": "activate_ad",
            "target_id": fb_ad_id,
            "params": {
                "source": "recommendation",
                "recommendation_id": str(rec_id),
                "ad_id": ad_id,
            },
            "ad_account_id": None,
        }

        ikey = f"reco:activate_ad:{fb_ad_id}:{rec_id}:{uuid.uuid4().hex}"

        import json as _json

        # INSERT напрямую внутри транзакции (не через create_task чтобы держать один conn)
        insert_result = await conn.execute(
            text(
                """
                INSERT INTO task_queue
                    (task_type, status, idempotency_key, payload,
                     attempt_count, max_attempts, requested_by, created_by_chat_id)
                VALUES
                    ('meta_api_mutation', 'pending', :ik, CAST(:pl AS JSONB), 0, 5, :rb, :ccid)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "ik": ikey,
                "pl": _json.dumps(payload),
                "rb": body.requested_by,
                "ccid": body.requested_by_chat_id,
            },
        )
        task_row = insert_result.first()

        if task_row is None:
            raise HTTPException(
                status_code=409, detail="Коллизия idempotency_key при создании задачи"
            )

        task_id = task_row[0]

        # Привязываем задачу к рекомендации
        await conn.execute(
            text(
                """
                UPDATE enable_recommendations
                SET promoted_to_task_id = :tid
                WHERE id = :rid
                """
            ),
            {"tid": task_id, "rid": rec_id},
        )

    # Читаем созданную задачу для ответа
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, task_type, status,
                           payload->>'target_id' AS fb_ad_id,
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
    result["ad_name"] = ad_name  # ad_name из рекомендации (в SELECT он NULL)
    return result
