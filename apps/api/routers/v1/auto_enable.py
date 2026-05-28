# -*- coding: utf-8 -*-
"""Роутер auto_enable: CRUD флагов «не включать автоматически».

Endpoints:
    GET    /dashboard/auto-enable-disabled               — список всех флагов
    POST   /dashboard/auto-enable-disabled/{fb_ad_id}   — установить флаг
    DELETE /dashboard/auto-enable-disabled/{fb_ad_id}   — снять флаг
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.auto_enable import AutoEnableDisabledIn, AutoEnableDisabledOut
from core.models.catalog.fb_ad import FbAd
from core.models.observer.ad_auto_enable_disabled import AdAutoEnableDisabled

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auto-enable"])


# ─────────────────────── GET /dashboard/auto-enable-disabled ───────────────────────


@router.get(
    "/dashboard/auto-enable-disabled",
    response_model=list[AutoEnableDisabledOut],
)
async def list_auto_enable_disabled(engine: DepEngine) -> list[AutoEnableDisabledOut]:
    """Возвращает список объявлений с отключённым авто-включением.

    JOIN на fb_ads для получения fb_ad_id и ad_name.
    """
    stmt = (
        select(
            AdAutoEnableDisabled.id,
            AdAutoEnableDisabled.ad_id,
            AdAutoEnableDisabled.created_at,
            AdAutoEnableDisabled.reason,
            FbAd.fb_ad_id,
            FbAd.ad_name,
        )
        .join(FbAd, AdAutoEnableDisabled.ad_id == FbAd.id, isouter=True)
        .order_by(AdAutoEnableDisabled.created_at.desc())
    )

    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).fetchall()

    return [
        AutoEnableDisabledOut(
            fb_ad_id=r.fb_ad_id or "",
            internal_id=r.ad_id,
            ad_name=r.ad_name,
            disabled_at=r.created_at,
            reason=r.reason,
        )
        for r in rows
    ]


# ─────────────────────── POST /dashboard/auto-enable-disabled/{fb_ad_id} ───────────────────────


@router.post(
    "/dashboard/auto-enable-disabled/{fb_ad_id}",
    response_model=AutoEnableDisabledOut,
    status_code=201,
)
async def disable_auto_enable(
    fb_ad_id: str,
    engine: DepEngine,
    body: AutoEnableDisabledIn | None = None,
) -> AutoEnableDisabledOut:
    """Устанавливает флаг «не включать автоматически» для объявления.

    Фронт (disableAutoEnable) не передаёт тело — body опционален.
    404 если объявление не найдено.
    409 если флаг уже установлен.
    """
    # Нормализуем body (None если фронт не передал)
    reason: str | None = body.reason if body else None

    async with engine.begin() as conn:
        # Ресолв fb_ad_id → ad_id
        ad_row = (
            await conn.execute(select(FbAd.id, FbAd.ad_name).where(FbAd.fb_ad_id == fb_ad_id))
        ).one_or_none()
        if ad_row is None:
            raise HTTPException(status_code=404, detail=f"Объявление {fb_ad_id!r} не найдено")

        ad_id = ad_row.id
        ad_name = ad_row.ad_name

        # Проверяем дубликат
        exists = (
            await conn.execute(
                select(AdAutoEnableDisabled.id).where(AdAutoEnableDisabled.ad_id == ad_id)
            )
        ).one_or_none()
        if exists is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Авто-включение уже отключено для объявления {fb_ad_id!r}",
            )

        now = datetime.now(UTC)
        try:
            result = (
                await conn.execute(
                    AdAutoEnableDisabled.__table__.insert()
                    .values(
                        ad_id=ad_id,
                        cabinet_day_started_at=now,
                        reason=reason,
                        created_at=now,
                    )
                    .returning(
                        AdAutoEnableDisabled.id,
                        AdAutoEnableDisabled.ad_id,
                        AdAutoEnableDisabled.created_at,
                        AdAutoEnableDisabled.reason,
                    )
                )
            ).one()
        except IntegrityError as exc:
            # Гонка: другой процесс успел вставить между SELECT и INSERT
            raise HTTPException(
                status_code=409,
                detail=f"Авто-включение уже отключено для объявления {fb_ad_id!r}",
            ) from exc

    return AutoEnableDisabledOut(
        fb_ad_id=fb_ad_id,
        internal_id=result.ad_id,
        ad_name=ad_name,
        disabled_at=result.created_at,
        reason=result.reason,
    )


# ─────────────────────── DELETE /dashboard/auto-enable-disabled/{fb_ad_id} ───────────────────────


@router.delete("/dashboard/auto-enable-disabled/{fb_ad_id}", status_code=204)
async def enable_auto_enable(fb_ad_id: str, engine: DepEngine) -> Response:
    """Снимает флаг «не включать автоматически» (включает авто-включение).

    404 если флаг не был установлен. 204 при успешном удалении.
    """
    async with engine.begin() as conn:
        # Ресолв fb_ad_id → ad_id
        ad_row = (
            await conn.execute(select(FbAd.id).where(FbAd.fb_ad_id == fb_ad_id))
        ).one_or_none()
        if ad_row is None:
            raise HTTPException(status_code=404, detail=f"Объявление {fb_ad_id!r} не найдено")

        ad_id = ad_row.id

        del_stmt = (
            delete(AdAutoEnableDisabled)
            .where(AdAutoEnableDisabled.ad_id == ad_id)
            .returning(AdAutoEnableDisabled.id)
        )
        deleted = (await conn.execute(del_stmt)).one_or_none()

    if deleted is None:
        raise HTTPException(
            status_code=404,
            detail=f"Флаг авто-включения не установлен для объявления {fb_ad_id!r}",
        )

    return Response(status_code=204)
