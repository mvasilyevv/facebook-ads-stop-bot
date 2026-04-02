# -*- coding: utf-8 -*-
"""FastAPI роутер для управления офферами."""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import OfferRuleConfigSchema, OfferSchema
from core.models import Offer, OfferRuleConfig

router = APIRouter(prefix="/api", tags=["offers"])


@router.get("/offers", response_model=list[OfferSchema])
async def list_offers(db: AsyncSession = Depends(get_db)):
    """Список всех офферов."""
    result = await db.execute(select(Offer).order_by(Offer.created_at.desc()))
    offers = result.scalars().all()
    return [
        OfferSchema(
            id=str(o.id),
            code=o.code,
            name=o.name,
            cpa_amount=o.cpa_amount,
            is_active=o.is_active,
        )
        for o in offers
    ]


@router.post("/offers", response_model=OfferSchema, status_code=201)
async def create_offer(body: OfferSchema, db: AsyncSession = Depends(get_db)):
    """Создать оффер."""
    offer = Offer(
        code=body.code,
        name=body.name,
        cpa_amount=body.cpa_amount,
        payout_per_deposit=body.payout_per_deposit,
        is_active=body.is_active,
    )
    db.add(offer)
    await db.flush()
    # Создаём дефолтную конфигурацию правил
    rule_config = OfferRuleConfig(offer_id=offer.id)
    db.add(rule_config)
    await db.commit()
    await db.refresh(offer)
    body.id = str(offer.id)
    return body


@router.put("/offers/{offer_id}", response_model=OfferSchema)
async def update_offer(offer_id: str, body: OfferSchema, db: AsyncSession = Depends(get_db)):
    """Обновить оффер."""
    result = await db.execute(select(Offer).where(Offer.id == _uuid.UUID(offer_id)))
    offer = result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="Оффер не найден")
    offer.code = body.code
    offer.name = body.name
    offer.cpa_amount = body.cpa_amount
    offer.payout_per_deposit = body.payout_per_deposit
    offer.is_active = body.is_active
    await db.commit()
    body.id = offer_id
    return body


@router.delete("/offers/{offer_id}")
async def delete_offer(offer_id: str, db: AsyncSession = Depends(get_db)):
    """Удалить оффер."""
    result = await db.execute(select(Offer).where(Offer.id == _uuid.UUID(offer_id)))
    offer = result.scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="Оффер не найден")
    await db.delete(offer)
    await db.commit()
    return {"ok": True}


@router.get("/offers/{offer_id}/rules", response_model=OfferRuleConfigSchema)
async def get_offer_rules(offer_id: str, db: AsyncSession = Depends(get_db)):
    """Получить правила оффера."""
    result = await db.execute(
        select(OfferRuleConfig).where(OfferRuleConfig.offer_id == _uuid.UUID(offer_id))
    )
    rc = result.scalar_one_or_none()
    if rc is None:
        return OfferRuleConfigSchema()
    return OfferRuleConfigSchema(
        cpc_percent_enabled=rc.cpc_percent_enabled,
        cpc_percent_stop=rc.cpc_percent_stop,
        cpl_percent_enabled=rc.cpl_percent_enabled,
        cpl_percent_stop=rc.cpl_percent_stop,
        cpr_percent_enabled=rc.cpr_percent_enabled,
        cpr_percent_stop=rc.cpr_percent_stop,
        regs_no_dep_enabled=rc.regs_no_dep_enabled,
        regs_no_dep_stop_count=rc.regs_no_dep_stop_count,
        spend_no_dep_enabled=rc.spend_no_dep_enabled,
        spend_no_dep_from_percent=rc.spend_no_dep_from_percent,
        spend_no_dep_to_percent=rc.spend_no_dep_to_percent,
        spend_with_dep_enabled=rc.spend_with_dep_enabled,
        spend_with_dep_from_percent=rc.spend_with_dep_from_percent,
        spend_with_dep_to_percent=rc.spend_with_dep_to_percent,
        frequency_elevated_threshold=rc.frequency_elevated_threshold,
        frequency_critical_threshold=rc.frequency_critical_threshold,
    )


@router.put("/offers/{offer_id}/rules", response_model=OfferRuleConfigSchema)
async def update_offer_rules(
    offer_id: str, body: OfferRuleConfigSchema, db: AsyncSession = Depends(get_db)
):
    """Обновить правила оффера."""
    uid = _uuid.UUID(offer_id)
    result = await db.execute(select(OfferRuleConfig).where(OfferRuleConfig.offer_id == uid))
    rc = result.scalar_one_or_none()
    if rc is None:
        rc = OfferRuleConfig(offer_id=uid)
        db.add(rc)
    for field, value in body.model_dump().items():
        setattr(rc, field, value)
    await db.commit()
    return body
