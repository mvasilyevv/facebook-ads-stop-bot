# -*- coding: utf-8 -*-
"""Роутер ads_admin: hard-delete объявлений из каталога fb_ads.

Endpoint:
    POST /dashboard/ads/bulk-delete   — удалить выбранные объявления (по fb_ad_id)

Hard delete: DELETE FROM fb_ads. Связанные строки удаляются КАСКАДОМ на уровне Postgres
(FK ondelete=CASCADE: ad_metrics, alert_events, ad_alert_state, meta_api_observation,
enable_recommendations, ad_deposit_correction, ad_auto_enable_disabled, tracker_aggregate,
telegram message_ref). tracker_postback — ondelete=SET NULL (история постбэков сохраняется,
обнуляется только fb_ad_fk). task_queue не связан FK (outbox) — не трогается.

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
    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text("DELETE FROM fb_ads WHERE fb_ad_id = ANY(:ids) RETURNING fb_ad_id"),
                {"ids": list(body.fb_ad_ids)},
            )
        ).all()
    deleted = [str(r[0]) for r in rows]
    logger.info(
        "bulk_delete_ads: запрошено %d, удалено %d объявлений (каскад): %s",
        len(body.fb_ad_ids),
        len(deleted),
        deleted,
    )
    return BulkDeleteAdsResponse(deleted=deleted, count=len(deleted))
