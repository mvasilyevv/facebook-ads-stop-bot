# -*- coding: utf-8 -*-
"""Роутер для управления офферами: CRUD + compare-агрегация + rules.

Endpoints (все с prefix /api, добавляемым auto-discovery):
    GET    /offers                   — список офферов
    GET    /offers/compare           — агрегация метрик per-offer
    POST   /offers                   — создать оффер
    PUT    /offers/{id}              — обновить оффер
    DELETE /offers/{id}              — soft delete (is_active=false)
    GET    /offers/{id}/rules        — правила оффера
    PUT    /offers/{id}/rules        — upsert правил оффера

Важные компромиссы:
    - Offer не содержит country_code / use_vision_creator / notes — возвращаются как null.
    - code при PUT-обновлении игнорируется (immutable, фронт не редактирует).
    - DELETE 404 для несуществующих и уже-inactive офферов (не идемпотентно).
    - /offers/compare требует партиционного WHERE по cycle_ts (без него — full scan).
"""

from __future__ import annotations

import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.offers import (
    OfferCompareRow,
    OfferCreateIn,
    OfferOut,
    OfferRuleOut,
    OfferRuleUpsertIn,
    OfferUpdateIn,
)
from apps.api.utils.partition import default_window
from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte
from core.models.catalog.fb_ad import FbAd
from core.models.catalog.fb_adset import FbAdset
from core.models.catalog.fb_campaign import FbCampaign
from core.models.catalog.offer import Offer
from core.models.catalog.offer_rule import OfferRule
from core.models.observer.alert_event import AlertEvent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["offers"])


# ─────────────────────── GET /offers ───────────────────────


@router.get("/offers", response_model=list[OfferOut])
async def list_offers(
    engine: DepEngine,
    include_inactive: bool = Query(default=False),
) -> list[OfferOut]:
    """Возвращает список офферов.

    По умолчанию только активные (is_active=true).
    При include_inactive=true — все, включая soft-deleted.
    """
    async with engine.connect() as conn:
        stmt = select(Offer).order_by(Offer.created_at.desc())
        if not include_inactive:
            stmt = stmt.where(Offer.is_active.is_(True))
        result = await conn.execute(stmt)
        rows = result.mappings().all()

    return [
        OfferOut(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            vertical=row["vertical"],
            is_active=row["is_active"],
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
            updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
        )
        for row in rows
    ]


# ─────────────────────── GET /offers/compare ───────────────────────


@router.get("/offers/compare", response_model=list[OfferCompareRow])
async def compare_offers(
    engine: DepEngine,
    days: int = Query(default=7, ge=1, le=90, description="Период агрегации (1-90 дней)"),
) -> list[OfferCompareRow]:
    """Агрегированные метрики per-offer за последние N дней.

    Требует партиционного WHERE по ad_metrics.cycle_ts и alert_events.created_at.
    Без этого фильтра запрос становится full-scan — не убирать.
    """
    # Вычисляем границы окна для партиционных таблиц
    period_start, period_end = default_window(hours=days * 24)

    async with engine.connect() as conn:
        # CRIT-1: ad_metrics — кумулятивные snapshot'ы, spend сбрасывается посуточно
        # (cabinet day reset). Наивный SUM по всем снимкам завышает spend в десятки
        # раз. /offers/compare — многодневное окно (до 90д), поэтому берём ПОСЛЕДНИЙ
        # snapshot на (ad × сутки) через DISTINCT ON (latest-per-ad-per-day), затем
        # SUM по офферу — это корректно складывает дневные итоги через reset'ы.
        # active_ads_count считаем по сырой ad_metrics (DISTINCT ad_id за окно, живые
        # за 7д) — это count объявлений, а не агрегация кумулятива, дублей не даёт.
        metrics_sql = text(
            f"""
            WITH {
                latest_per_ad_per_day_cte(
                    cte_alias="per_ad_day",
                    columns=("spend", "leads", "registrations", "deposits"),
                )
            }
            SELECT
                fc.offer_id                              AS offer_id,
                COALESCE(SUM(pad.spend), 0)              AS spend,
                COALESCE(SUM(pad.leads), 0)::bigint      AS leads,
                COALESCE(SUM(pad.registrations), 0)::bigint AS registrations,
                COALESCE(SUM(pad.deposits), 0)::bigint   AS deposits,
                COUNT(DISTINCT CASE
                    WHEN fa.last_seen_at >= NOW() - INTERVAL '7 days' THEN fa.id
                END)::bigint                             AS active_ads_count
            FROM per_ad_day pad
            JOIN fb_ads fa       ON fa.id = pad.ad_id
            JOIN fb_adsets fas   ON fas.id = fa.adset_id
            JOIN fb_campaigns fc ON fc.id = fas.campaign_id
            WHERE fc.offer_id IS NOT NULL
            GROUP BY fc.offer_id
            """
        )
        metrics_result = await conn.execute(
            metrics_sql, {"from_dt": period_start, "to_dt": period_end}
        )
        metrics_by_offer: dict[uuid.UUID, dict] = {
            row.offer_id: {
                "spend": row.spend or Decimal("0"),
                "leads": int(row.leads or 0),
                "registrations": int(row.registrations or 0),
                "deposits": int(row.deposits or 0),
                "active_ads_count": int(row.active_ads_count or 0),
            }
            for row in metrics_result.mappings()
        }

        # Алерты стадии stop: COUNT per offer
        alerts_q = (
            select(
                FbCampaign.offer_id,
                func.count(AlertEvent.id).label("stop_alerts_count"),
            )
            .select_from(AlertEvent)
            .join(FbAd, FbAd.id == AlertEvent.ad_id)
            .join(FbAdset, FbAdset.id == FbAd.adset_id)
            .join(FbCampaign, FbCampaign.id == FbAdset.campaign_id)
            .where(
                AlertEvent.created_at >= period_start,  # партиционный фильтр — обязателен
                AlertEvent.stage == "stop",
                FbCampaign.offer_id.isnot(None),
            )
            .group_by(FbCampaign.offer_id)
        )
        alerts_result = await conn.execute(alerts_q)
        alerts_by_offer: dict[uuid.UUID, int] = {
            row.offer_id: int(row.stop_alerts_count or 0) for row in alerts_result.mappings()
        }

        # Загружаем активные офферы для получения code/name
        offers_result = await conn.execute(
            select(Offer).where(Offer.is_active.is_(True)).order_by(Offer.code)
        )
        offers = offers_result.mappings().all()

    result: list[OfferCompareRow] = []
    for offer in offers:
        oid = offer["id"]
        m = metrics_by_offer.get(
            oid,
            {
                "spend": Decimal("0"),
                "leads": 0,
                "registrations": 0,
                "deposits": 0,
                "active_ads_count": 0,
            },
        )

        spend = Decimal(str(m["spend"]))
        leads = m["leads"]
        regs = m["registrations"]
        deps = m["deposits"]
        stop_cnt = alerts_by_offer.get(oid, 0)

        _two = Decimal("0.01")
        cpl = (spend / leads).quantize(_two, ROUND_HALF_UP) if leads > 0 else None
        cpr = (spend / regs).quantize(_two, ROUND_HALF_UP) if regs > 0 else None
        cpd = (spend / deps).quantize(_two, ROUND_HALF_UP) if deps > 0 else None

        result.append(
            OfferCompareRow(
                offer_id=oid,
                offer_code=offer["code"],
                offer_name=offer["name"],
                days=days,
                spend=spend.quantize(_two, ROUND_HALF_UP),
                leads=leads,
                registrations=regs,
                deposits=deps,
                active_ads_count=m["active_ads_count"],
                stop_alerts_count=stop_cnt,
                cost_per_lead=cpl,
                cost_per_registration=cpr,
                cost_per_deposit=cpd,
            )
        )

    return result


# ─────────────────────── POST /offers ───────────────────────


@router.post("/offers", response_model=OfferOut, status_code=201)
async def create_offer(
    body: OfferCreateIn,
    engine: DepEngine,
) -> OfferOut:
    """Создаёт новый оффер.

    При конфликте по UNIQUE code → 409 Conflict.
    """
    async with engine.begin() as conn:
        stmt = (
            Offer.__table__.insert()
            .values(
                code=body.code,
                name=body.name,
                vertical=body.vertical,
                is_active=True,
            )
            .returning(
                Offer.__table__.c.id,
                Offer.__table__.c.code,
                Offer.__table__.c.name,
                Offer.__table__.c.vertical,
                Offer.__table__.c.is_active,
                Offer.__table__.c.created_at,
                Offer.__table__.c.updated_at,
            )
        )
        try:
            result = await conn.execute(stmt)
        except IntegrityError as exc:
            err_str = str(exc).lower()
            if "unique" in err_str or "duplicate" in err_str:
                raise HTTPException(
                    status_code=409,
                    detail=f"Оффер с кодом '{body.code}' уже существует",
                ) from exc
            raise
        row = result.mappings().one()

    return OfferOut(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        vertical=row["vertical"],
        is_active=row["is_active"],
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
    )


# ─────────────────────── PUT /offers/{id} ───────────────────────


@router.put("/offers/{offer_id}", response_model=OfferOut)
async def update_offer(
    offer_id: uuid.UUID,
    body: OfferUpdateIn,
    engine: DepEngine,
) -> OfferOut:
    """Обновляет оффер.

    code — immutable: передача code в теле игнорируется, изменение не применяется.
    404 если оффер не найден.
    """
    updates: dict = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.vertical is not None:
        updates["vertical"] = body.vertical
    if body.is_active is not None:
        updates["is_active"] = body.is_active
    # body.code намеренно не добавляем в updates

    async with engine.begin() as conn:
        if not updates:
            # Нет что обновлять — просто возвращаем текущее состояние
            sel = select(Offer.__table__).where(Offer.__table__.c.id == offer_id)
            sel_result = await conn.execute(sel)
            row = sel_result.mappings().one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Оффер не найден")
        else:
            updates["updated_at"] = func.now()
            stmt = (
                Offer.__table__.update()
                .where(Offer.__table__.c.id == offer_id)
                .values(**updates)
                .returning(
                    Offer.__table__.c.id,
                    Offer.__table__.c.code,
                    Offer.__table__.c.name,
                    Offer.__table__.c.vertical,
                    Offer.__table__.c.is_active,
                    Offer.__table__.c.created_at,
                    Offer.__table__.c.updated_at,
                )
            )
            result = await conn.execute(stmt)
            row = result.mappings().one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Оффер не найден")

    return OfferOut(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        vertical=row["vertical"],
        is_active=row["is_active"],
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
    )


# ─────────────────────── DELETE /offers/{id} ───────────────────────


@router.delete("/offers/{offer_id}", status_code=204)
async def delete_offer(
    offer_id: uuid.UUID,
    engine: DepEngine,
) -> None:
    """Soft delete оффера: is_active=false.

    404 если оффер не найден или уже неактивный (не идемпотентно).
    Рационал: фронт ожидает ошибку при попытке удалить несуществующее.
    """
    async with engine.begin() as conn:
        stmt = (
            Offer.__table__.update()
            .where(
                Offer.__table__.c.id == offer_id,
                Offer.__table__.c.is_active.is_(True),
            )
            .values(is_active=False, updated_at=func.now())
            .returning(Offer.__table__.c.id)
        )
        result = await conn.execute(stmt)
        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Оффер не найден или уже неактивен",
            )


# ─────────────────────── GET /offers/{id}/rules ───────────────────────


@router.get("/offers/{offer_id}/rules", response_model=OfferRuleOut)
async def get_offer_rules(
    offer_id: uuid.UUID,
    engine: DepEngine,
) -> OfferRuleOut:
    """Возвращает правила оффера.

    Если OfferRule для оффера не существует — возвращает дефолтную структуру
    с offer_id и всеми порогами null (правило не настроено).
    404 если сам оффер не найден.
    """
    async with engine.connect() as conn:
        # Проверяем существование оффера
        offer_check = await conn.execute(
            select(Offer.__table__.c.id).where(Offer.__table__.c.id == offer_id)
        )
        if offer_check.first() is None:
            raise HTTPException(status_code=404, detail="Оффер не найден")

        # Загружаем правила
        rules_result = await conn.execute(
            select(OfferRule.__table__).where(OfferRule.__table__.c.offer_id == offer_id)
        )
        row = rules_result.mappings().one_or_none()

    if row is None:
        # Дефолтная структура — все пороги null
        return OfferRuleOut(offer_id=offer_id)

    return OfferRuleOut(
        offer_id=row["offer_id"],
        spend_no_event_threshold=row["spend_no_event_threshold"],
        cpa_threshold=row["cpa_threshold"],
        cpm_threshold=row["cpm_threshold"],
        ctr_threshold=row["ctr_threshold"],
        frequency_threshold=row["frequency_threshold"],
        funnel_ratio_threshold=row["funnel_ratio_threshold"],
    )


# ─────────────────────── PUT /offers/{id}/rules ───────────────────────


@router.put("/offers/{offer_id}/rules", response_model=OfferRuleOut)
async def upsert_offer_rules(
    offer_id: uuid.UUID,
    body: OfferRuleUpsertIn,
    engine: DepEngine,
) -> OfferRuleOut:
    """Upsert правил оффера.

    Если OfferRule не существует — создаёт. Если существует — обновляет.
    404 если оффер не найден.
    Невалидные пороги (отрицательные) → 422.
    """
    async with engine.begin() as conn:
        # Проверяем существование оффера
        offer_check = await conn.execute(
            select(Offer.__table__.c.id).where(Offer.__table__.c.id == offer_id)
        )
        if offer_check.first() is None:
            raise HTTPException(status_code=404, detail="Оффер не найден")

        values = {
            "offer_id": offer_id,
            "spend_no_event_threshold": body.spend_no_event_threshold,
            "cpa_threshold": body.cpa_threshold,
            "cpm_threshold": body.cpm_threshold,
            "ctr_threshold": body.ctr_threshold,
            "frequency_threshold": body.frequency_threshold,
            "funnel_ratio_threshold": body.funnel_ratio_threshold,
        }

        # Upsert через INSERT ... ON CONFLICT (offer_id) DO UPDATE
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(OfferRule.__table__).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_offer_rules_offer_id",
            set_={
                "spend_no_event_threshold": stmt.excluded.spend_no_event_threshold,
                "cpa_threshold": stmt.excluded.cpa_threshold,
                "cpm_threshold": stmt.excluded.cpm_threshold,
                "ctr_threshold": stmt.excluded.ctr_threshold,
                "frequency_threshold": stmt.excluded.frequency_threshold,
                "funnel_ratio_threshold": stmt.excluded.funnel_ratio_threshold,
                "updated_at": func.now(),
            },
        ).returning(
            OfferRule.__table__.c.offer_id,
            OfferRule.__table__.c.spend_no_event_threshold,
            OfferRule.__table__.c.cpa_threshold,
            OfferRule.__table__.c.cpm_threshold,
            OfferRule.__table__.c.ctr_threshold,
            OfferRule.__table__.c.frequency_threshold,
            OfferRule.__table__.c.funnel_ratio_threshold,
        )

        result = await conn.execute(stmt)
        row = result.mappings().one()

    return OfferRuleOut(
        offer_id=row["offer_id"],
        spend_no_event_threshold=row["spend_no_event_threshold"],
        cpa_threshold=row["cpa_threshold"],
        cpm_threshold=row["cpm_threshold"],
        ctr_threshold=row["ctr_threshold"],
        frequency_threshold=row["frequency_threshold"],
        funnel_ratio_threshold=row["funnel_ratio_threshold"],
    )
