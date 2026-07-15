# -*- coding: utf-8 -*-
"""Роутер ads_admin: hard-delete объявлений из каталога fb_ads.

Endpoint:
    POST /dashboard/ads/bulk-delete   — удалить выбранные объявления (по fb_ad_id)

Hard delete: DELETE FROM fb_ads. Связанные строки удаляются КАСКАДОМ на уровне Postgres
(FK ondelete=CASCADE: ad_metrics, alert_events, ad_alert_state, meta_api_observation,
enable_recommendations, ad_deposit_correction, ad_auto_enable_disabled, tracker_aggregate,
telegram message_ref). tracker_postback — ondelete=SET NULL (история постбэков сохраняется,
обнуляется только fb_ad_fk).

task_queue НЕ связан FK (outbox-паттерн) — каскад его не трогает. Поэтому в ТОЙ ЖЕ
транзакции, что и DELETE, отменяем (status='cancelled') все active-задачи
(draft/pending/running/retrying) по удаляемым ad_id. Иначе meta_api_worker исполнит
orphan pause_ad/activate_ad вслепую по target_id уже удалённого объявления (риск
ре-открута + бесконечный requeue без FSM-контекста). Отменяются два класса задач:
одиночные (payload->>'target_id' ∈ :ids) и bulk_status_change, где список ad_id в
params (ad_ids / object_ids) пересекается с удаляемыми (JSONB `?|` по массиву).

Необратимо. На фронте — confirm-with-typing. Лимит 500 за запрос.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import text

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.ads_admin import BulkDeleteAdsRequest, BulkDeleteAdsResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ads-admin"])


@router.post("/dashboard/ads/bulk-delete", response_model=BulkDeleteAdsResponse)
async def bulk_delete_ads(body: BulkDeleteAdsRequest, engine: DepEngine) -> BulkDeleteAdsResponse:
    """Hard-delete объявлений из fb_ads по списку fb_ad_id (каскад на уровне БД).

    Возвращает фактически удалённые fb_ad_id (которых не было — молча пропускаются).
    """
    ids = list(body.fb_ad_ids)
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text("DELETE FROM fb_ads WHERE fb_ad_id = ANY(:ids) RETURNING fb_ad_id"),
                {"ids": ids},
            )
        ).all()

        # Отменяем orphan-задачи в task_queue в ТОЙ ЖЕ транзакции (outbox без FK).
        # Используем полный запрошенный список ids (а не только реально удалённые) —
        # если объявления уже нет в fb_ads, но в очереди висит pause_ad по нему,
        # его всё равно нужно погасить.
        cancelled_rows = (
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'cancelled', updated_at = NOW()
                    WHERE status IN ('draft', 'pending', 'running', 'retrying')
                      AND (
                            payload->>'target_id' = ANY(:ids)
                            OR jsonb_exists_any(payload->'params'->'ad_ids', :ids)
                            OR jsonb_exists_any(payload->'params'->'object_ids', :ids)
                      )
                    RETURNING id
                    """
                ),
                {"ids": ids},
            )
        ).all()

    deleted = [str(r[0]) for r in rows]
    cancelled = [int(r[0]) for r in cancelled_rows]
    logger.info(
        "bulk_delete_ads: запрошено %d, удалено %d объявлений (каскад), "
        "отменено %d orphan-задач в task_queue: deleted=%s cancelled_tasks=%s",
        len(ids),
        len(deleted),
        len(cancelled),
        deleted,
        cancelled,
    )
    return BulkDeleteAdsResponse(deleted=deleted, count=len(deleted), cancelled_task_ids=cancelled)
