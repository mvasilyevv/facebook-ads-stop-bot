# -*- coding: utf-8 -*-
"""API наблюдателя: ручная смена суток кабинета и observer-статус."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import get_db
from core.cabinet_day import build_cabinet_day_archive_payload, has_any_metric_value
from core.models import AdSnapshot, CabinetDayArchive, FbAd, FbAdset
from core.settings_queries import get_or_create_observer_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/observer", tags=["observer"])


@router.post("/start-new-cabinet-day")
async def start_new_cabinet_day(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Закрывает текущие сутки кабинета и открывает новые.

    Архивирует живые снэпшоты текущего дня в CabinetDayArchive и сдвигает
    observer_settings.cabinet_day_started_at на now(). Не блокирует observer:
    следующий цикл просто начнёт писать снэпшоты, относящиеся уже к новому дню.
    """
    settings = await get_or_create_observer_settings(db)
    now = datetime.now(UTC)

    stmt = select(AdSnapshot).options(
        selectinload(AdSnapshot.fb_ad).selectinload(FbAd.adset).selectinload(FbAdset.campaign),
    )
    if settings.cabinet_day_started_at is not None:
        stmt = stmt.where(AdSnapshot.last_observed_at >= settings.cabinet_day_started_at)

    current_snapshots = (await db.execute(stmt)).scalars().all()
    has_data = bool(current_snapshots) and any(
        has_any_metric_value(snapshot) for snapshot in current_snapshots
    )

    archived = 0
    if has_data:
        summary_json, campaigns_json, ads_json = build_cabinet_day_archive_payload(
            current_snapshots
        )
        db.add(
            CabinetDayArchive(
                started_at=settings.cabinet_day_started_at or now,
                ended_at=now,
                reset_detected_at=now,
                ads_count=len(current_snapshots),
                summary_json=summary_json,
                campaigns_json=campaigns_json,
                ads_json=ads_json,
            )
        )
        archived = len(current_snapshots)

    settings.cabinet_day_started_at = now
    await db.commit()

    logger.info(
        "Observer: новые сутки кабинета открыты вручную, архивировано %s объявлений",
        archived,
    )
    return {
        "ok": True,
        "archived_ads": archived,
        "new_day_started_at": now.isoformat(),
    }


@router.get("/status")
async def get_observer_status(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Возвращает компактный observer-статус для UI-плитки на дашборде."""
    settings = await get_or_create_observer_settings(db)

    last_batch_size = 0
    if settings.current_scan_id and settings.current_scan_id > 0:
        last_batch_size = (
            await db.scalar(
                select(func.count(AdSnapshot.id)).where(
                    AdSnapshot.last_scan_id == settings.current_scan_id
                )
            )
            or 0
        )

    active_stmt = select(func.count(AdSnapshot.id))
    if settings.cabinet_day_started_at is not None:
        active_stmt = active_stmt.where(
            AdSnapshot.last_observed_at >= settings.cabinet_day_started_at
        )
    active_total = await db.scalar(active_stmt) or 0

    return {
        "is_scanning_enabled": bool(settings.is_scanning_enabled),
        "worker_status": settings.worker_status,
        "worker_message": settings.worker_message,
        "worker_heartbeat_at": (
            settings.worker_heartbeat_at.isoformat() if settings.worker_heartbeat_at else None
        ),
        "worker_last_error": settings.worker_last_error,
        "worker_last_error_at": (
            settings.worker_last_error_at.isoformat() if settings.worker_last_error_at else None
        ),
        "current_scan_id": int(settings.current_scan_id or 0),
        "last_batch_size": int(last_batch_size),
        "active_total": int(active_total),
        "next_scan_at": settings.next_scan_at.isoformat() if settings.next_scan_at else None,
        "cabinet_day_started_at": (
            settings.cabinet_day_started_at.isoformat() if settings.cabinet_day_started_at else None
        ),
    }
