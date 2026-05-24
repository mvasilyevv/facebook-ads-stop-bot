# -*- coding: utf-8 -*-
"""FastAPI роутер для управления офферами."""

import uuid as _uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Date as SqlDate
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import get_db
from apps.api.schemas import OfferRuleConfigSchema, OfferSchema
from core.models import AdMetricHistory, FbAd, FbAdset, FbCampaign, Offer, OfferRuleConfig

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
            cpa_amount=o.cpa_amount,
            payout_per_deposit=o.payout_per_deposit,
            country_name=o.country_name,
            is_active=o.is_active,
            landing_url=o.landing_url,
            cabinet_id=o.cabinet_id,
            pixel_id=o.pixel_id,
            geo_code=o.geo_code,
            geo_slot_name=o.geo_slot_name,
        )
        for o in offers
    ]


@router.post("/offers", response_model=OfferSchema, status_code=201)
async def create_offer(body: OfferSchema, db: AsyncSession = Depends(get_db)):
    """Создать оффер."""
    offer = Offer(
        code=body.code,
        cpa_amount=body.cpa_amount,
        payout_per_deposit=body.payout_per_deposit if body.payout_per_deposit is not None else 0.0,
        country_name=body.country_name,
        is_active=body.is_active,
        landing_url=body.landing_url,
        cabinet_id=body.cabinet_id,
        pixel_id=body.pixel_id,
        geo_code=body.geo_code,
        geo_slot_name=body.geo_slot_name,
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
    offer.cpa_amount = body.cpa_amount
    # payout_per_deposit имеет NOT NULL в БД, поэтому не затираем его None из тела запроса
    if body.payout_per_deposit is not None:
        offer.payout_per_deposit = body.payout_per_deposit
    offer.country_name = body.country_name
    offer.is_active = body.is_active
    offer.landing_url = body.landing_url
    offer.cabinet_id = body.cabinet_id
    offer.pixel_id = body.pixel_id
    offer.geo_code = body.geo_code
    offer.geo_slot_name = body.geo_slot_name
    await db.commit()
    body.id = offer_id
    return body


@router.get("/offers/compare")
async def compare_offers(
    days: int = Query(default=7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Сравнение офферов по spend/leads/deps за N дней.

    Возвращает список офферов с суммарными метриками и динамикой по дням.
    Сортировка по spend desc.
    """
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    # Строим day-series: max-per-ad-per-day, потом SUM по офферу
    day_col = cast(AdMetricHistory.cycle_ts, SqlDate).label("day")
    per_ad_day = (
        select(
            AdMetricHistory.ad_id,
            day_col,
            func.max(AdMetricHistory.spend).label("spend"),
            func.max(AdMetricHistory.leads).label("leads"),
            func.max(AdMetricHistory.deposits).label("deps"),
        )
        .where(AdMetricHistory.cycle_ts >= period_start)
        .group_by(AdMetricHistory.ad_id, day_col)
        .subquery()
    )

    q = (
        select(
            FbCampaign.offer_code,
            per_ad_day.c.day,
            func.sum(per_ad_day.c.spend).label("spend"),
            func.sum(per_ad_day.c.leads).label("leads"),
            func.sum(per_ad_day.c.deps).label("deps"),
        )
        .join(FbAd, FbAd.id == per_ad_day.c.ad_id)
        .join(FbAdset, FbAd.adset_id == FbAdset.id)
        .join(FbCampaign, FbAdset.campaign_id == FbCampaign.id)
        .where(FbCampaign.offer_code.isnot(None))
        .group_by(FbCampaign.offer_code, per_ad_day.c.day)
        .order_by(FbCampaign.offer_code, per_ad_day.c.day)
    )
    rows = (await db.execute(q)).all()

    # Группируем по офферу, строим массив spend_by_day
    from collections import defaultdict

    offer_days: dict[str, dict] = defaultdict(
        lambda: {"spend_total": Decimal("0"), "leads": 0, "deps": 0, "by_day": {}}
    )
    for row in rows:
        code = row.offer_code
        day_str = str(row.day)
        offer_days[code]["spend_total"] += Decimal(str(row.spend or 0))
        offer_days[code]["leads"] += int(row.leads or 0)
        offer_days[code]["deps"] += int(row.deps or 0)
        offer_days[code]["by_day"][day_str] = float(row.spend or 0)

    # Строим список дат за период
    date_labels = [(period_start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]

    result = []
    for code, data in offer_days.items():
        spend_total = float(data["spend_total"])
        leads = data["leads"]
        deps = data["deps"]
        cr = round(deps / leads * 100, 1) if leads > 0 else 0.0
        spend_by_day = [data["by_day"].get(d, 0.0) for d in date_labels]
        result.append(
            {
                "code": code,
                "spend_total": spend_total,
                "leads": leads,
                "deps": deps,
                "cr_pct": cr,
                "spend_by_day": spend_by_day,
                "date_labels": date_labels,
            }
        )

    # Сортировка по spend_total desc
    result.sort(key=lambda x: x["spend_total"], reverse=True)
    return result


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
        warning_percent_of_stop=rc.warning_percent_of_stop,
        stop_percent_of_base=rc.stop_percent_of_base,
        cpc_warning_percent_of_stop=rc.cpc_warning_percent_of_stop,
        cpc_stop_percent_of_base=rc.cpc_stop_percent_of_base,
        cpl_warning_percent_of_stop=rc.cpl_warning_percent_of_stop,
        cpl_stop_percent_of_base=rc.cpl_stop_percent_of_base,
        cpr_warning_percent_of_stop=rc.cpr_warning_percent_of_stop,
        cpr_stop_percent_of_base=rc.cpr_stop_percent_of_base,
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
