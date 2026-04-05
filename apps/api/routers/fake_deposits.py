# -*- coding: utf-8 -*-
"""FastAPI роутер для управления ложными депозитами."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import AdDepositCorrectionSchema, AdDepositCorrectionUpdateSchema
from core.models import AdDepositCorrection, FbAd, FbAdset, FbCampaign

router = APIRouter(prefix="/api", tags=["fake-deposits"])


async def _resolve_ad_id(db: AsyncSession, fb_ad_id: str) -> FbAd | None:
    """Находит запись fb_ads по строковому fb_ad_id."""
    return await db.scalar(select(FbAd).where(FbAd.fb_ad_id == fb_ad_id))


async def _build_response(
    correction: AdDepositCorrection,
    db: AsyncSession,
) -> AdDepositCorrectionSchema:
    """Собирает ответ с данными объявления через JOIN FbAd → FbAdset → FbCampaign."""
    row = (
        await db.execute(
            select(FbAd.fb_ad_id, FbAd.ad_name, FbCampaign.campaign_name)
            .join(FbAdset, FbAd.adset_id == FbAdset.id)
            .join(FbCampaign, FbAdset.campaign_id == FbCampaign.id)
            .where(FbAd.id == correction.ad_id)
        )
    ).first()
    return AdDepositCorrectionSchema(
        id=str(correction.id),
        fb_ad_id=row.fb_ad_id if row else "",
        fake_count=correction.fake_count,
        note=correction.note,
        ad_name=row.ad_name if row else None,
        campaign_name=row.campaign_name if row else None,
        created_at=correction.created_at.isoformat(),
        updated_at=correction.updated_at.isoformat(),
    )


@router.get("/fake-deposits", response_model=list[AdDepositCorrectionSchema])
async def list_fake_deposits(
    db: AsyncSession = Depends(get_db),
) -> list[AdDepositCorrectionSchema]:
    """Список всех корректировок ложных депозитов."""
    corrections = (
        (
            await db.execute(
                select(AdDepositCorrection).order_by(AdDepositCorrection.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [await _build_response(c, db) for c in corrections]


@router.put("/fake-deposits/{fb_ad_id}", response_model=AdDepositCorrectionSchema)
async def set_fake_deposits(
    fb_ad_id: str,
    body: AdDepositCorrectionUpdateSchema,
    db: AsyncSession = Depends(get_db),
) -> AdDepositCorrectionSchema:
    """Установить или обновить количество ложных депозитов для объявления."""
    fb_ad = await _resolve_ad_id(db, fb_ad_id)
    if fb_ad is None:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    correction = await db.scalar(
        select(AdDepositCorrection).where(AdDepositCorrection.ad_id == fb_ad.id)
    )

    if correction is None:
        correction = AdDepositCorrection(
            ad_id=fb_ad.id,
            fake_count=body.fake_count,
            note=body.note,
        )
        db.add(correction)
    else:
        correction.fake_count = body.fake_count
        correction.note = body.note

    await db.commit()
    await db.refresh(correction)
    return await _build_response(correction, db)


@router.delete("/fake-deposits/{fb_ad_id}", status_code=204)
async def delete_fake_deposits(
    fb_ad_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Удалить корректировку ложных депозитов."""
    fb_ad = await _resolve_ad_id(db, fb_ad_id)
    if fb_ad is None:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    result = await db.execute(
        delete(AdDepositCorrection).where(AdDepositCorrection.ad_id == fb_ad.id)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Корректировка не найдена")
    await db.commit()
