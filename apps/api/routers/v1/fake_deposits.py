# -*- coding: utf-8 -*-
"""Роутер fake_deposits: CRUD для корректировок фейковых депозитов.

Endpoints:
    GET    /fake-deposits               — список всех корректировок с JOIN на fb_ads
    PUT    /fake-deposits/{fb_ad_id}    — upsert (INSERT или UPDATE)
    DELETE /fake-deposits/{fb_ad_id}    — hard delete
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.fake_deposits import FakeDepositOut, FakeDepositUpsertIn
from core.models.catalog.fb_ad import FbAd
from core.models.observer.ad_deposit_correction import AdDepositCorrection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["fake-deposits"])


# ─────────────────────── GET /fake-deposits ───────────────────────


@router.get("/fake-deposits", response_model=list[FakeDepositOut])
async def list_fake_deposits(engine: DepEngine) -> list[FakeDepositOut]:
    """Возвращает все корректировки фейковых депозитов с именем объявления через LEFT JOIN."""
    stmt = (
        select(
            AdDepositCorrection.id,
            AdDepositCorrection.ad_id,
            AdDepositCorrection.corrected_deposits,
            AdDepositCorrection.note,
            AdDepositCorrection.created_at,
            AdDepositCorrection.updated_at,
            FbAd.fb_ad_id,
            FbAd.ad_name,
        )
        .join(FbAd, AdDepositCorrection.ad_id == FbAd.id, isouter=True)
        .order_by(AdDepositCorrection.created_at.desc())
    )

    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).fetchall()

    return [
        FakeDepositOut(
            fb_ad_id=r.fb_ad_id or "",
            internal_id=r.ad_id,
            ad_name=r.ad_name,
            fake_count=r.corrected_deposits,
            note=r.note,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


# ─────────────────────── PUT /fake-deposits/{fb_ad_id} ───────────────────────


@router.put("/fake-deposits/{fb_ad_id}", response_model=FakeDepositOut)
async def upsert_fake_deposit(
    fb_ad_id: str,
    body: FakeDepositUpsertIn,
    engine: DepEngine,
) -> FakeDepositOut:
    """Upsert корректировки фейковых депозитов для объявления.

    Ресолв fb_ad_id → FbAd.id. 404 если объявление не существует.
    ON CONFLICT (ad_id) DO UPDATE — обновляет corrected_deposits и note.
    Возвращает обновлённую запись (200).
    """
    async with engine.begin() as conn:
        # Ресолв fb_ad_id → ad_id
        ad_row = (
            await conn.execute(select(FbAd.id, FbAd.ad_name).where(FbAd.fb_ad_id == fb_ad_id))
        ).one_or_none()
        if ad_row is None:
            raise HTTPException(status_code=404, detail=f"Объявление {fb_ad_id!r} не найдено")

        ad_id = ad_row.id
        ad_name = ad_row.ad_name

        # Upsert через PostgreSQL INSERT ... ON CONFLICT DO UPDATE
        now = datetime.now(UTC)
        stmt = (
            pg_insert(AdDepositCorrection)
            .values(
                ad_id=ad_id,
                corrected_deposits=body.fake_count,
                note=body.note,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["ad_id"],
                set_={
                    "corrected_deposits": body.fake_count,
                    "note": body.note,
                    "updated_at": now,
                },
            )
            .returning(
                AdDepositCorrection.id,
                AdDepositCorrection.ad_id,
                AdDepositCorrection.corrected_deposits,
                AdDepositCorrection.note,
                AdDepositCorrection.created_at,
                AdDepositCorrection.updated_at,
            )
        )
        result = (await conn.execute(stmt)).one()

    return FakeDepositOut(
        fb_ad_id=fb_ad_id,
        internal_id=result.ad_id,
        ad_name=ad_name,
        fake_count=result.corrected_deposits,
        note=result.note,
        created_at=result.created_at,
        updated_at=result.updated_at,
    )


# ─────────────────────── DELETE /fake-deposits/{fb_ad_id} ───────────────────────


@router.delete("/fake-deposits/{fb_ad_id}", status_code=204)
async def delete_fake_deposit(fb_ad_id: str, engine: DepEngine) -> Response:
    """Удаляет корректировку фейковых депозитов для объявления.

    404 если записи не было. 204 при успешном удалении.
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
            delete(AdDepositCorrection)
            .where(AdDepositCorrection.ad_id == ad_id)
            .returning(AdDepositCorrection.id)
        )
        deleted = (await conn.execute(del_stmt)).one_or_none()

    if deleted is None:
        raise HTTPException(
            status_code=404,
            detail=f"Корректировка для объявления {fb_ad_id!r} не найдена",
        )

    return Response(status_code=204)
