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
    RulePreviewOut,
    RuleThresholdPreview,
    SpendRangePreview,
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
        # LEFT JOIN offer_rules → cpa_threshold (единый целевой CPA оффера; визард тянет бид).
        o = Offer.__table__
        r = OfferRule.__table__
        stmt = (
            select(o, r.c.cpa_threshold)
            .select_from(o.outerjoin(r, r.c.offer_id == o.c.id))
            .order_by(o.c.created_at.desc())
        )
        if not include_inactive:
            stmt = stmt.where(o.c.is_active.is_(True))
        result = await conn.execute(stmt)
        rows = result.mappings().all()

    return [
        OfferOut(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            vertical=row["vertical"],
            pixel_id=row["pixel_id"],
            is_active=row["is_active"],
            ad_account_ids=list(row["ad_account_ids"] or []),
            countries=list(row["countries"] or []),
            cpa_threshold=row["cpa_threshold"],
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
                name=body.code,  # name = code: поле «Название» убрано из UI
                vertical=body.vertical,
                pixel_id=(body.pixel_id or None),
                is_active=True,
                # Мульти-кабинет: валидация (min 1, числовые ID) — в OfferCreateIn.
                ad_account_ids=body.ad_account_ids,
                # Гео оффера (ISO-2 upper) — для дерайва визарда.
                countries=body.countries,
            )
            .returning(
                Offer.__table__.c.id,
                Offer.__table__.c.code,
                Offer.__table__.c.name,
                Offer.__table__.c.vertical,
                Offer.__table__.c.pixel_id,
                Offer.__table__.c.is_active,
                Offer.__table__.c.ad_account_ids,
                Offer.__table__.c.countries,
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
        pixel_id=row["pixel_id"],
        is_active=row["is_active"],
        ad_account_ids=list(row["ad_account_ids"] or []),
        countries=list(row["countries"] or []),
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
    # name не обновляется — всегда равно коду (поле убрано из UI).
    if body.vertical is not None:
        updates["vertical"] = body.vertical
    # pixel_id: None — не трогаем; строка (в т.ч. пустая → null) — заменяем.
    if body.pixel_id is not None:
        updates["pixel_id"] = body.pixel_id or None
    if body.is_active is not None:
        updates["is_active"] = body.is_active
    # Мульти-кабинет: None — не трогаем, список — замена (валидация в OfferUpdateIn).
    if body.ad_account_ids is not None:
        updates["ad_account_ids"] = body.ad_account_ids
    # Гео: None — не трогаем; список (в т.ч. пустой) — замена (нормализация в OfferUpdateIn).
    if body.countries is not None:
        updates["countries"] = body.countries
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
                    Offer.__table__.c.pixel_id,
                    Offer.__table__.c.is_active,
                    Offer.__table__.c.ad_account_ids,
                    Offer.__table__.c.countries,
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
        pixel_id=row["pixel_id"],
        is_active=row["is_active"],
        ad_account_ids=list(row["ad_account_ids"] or []),
        countries=list(row["countries"] or []),
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
        stop_percent_of_rule=row["stop_percent_of_rule"],
        warning_percent_of_stop=row["warning_percent_of_stop"],
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

        # Partial upsert: обновляем ТОЛЬКО переданные поля. Три формы (CPA в форме оффера /
        # частота / чувствительность-слайдеры) пишут в одну строку offer_rules — полный
        # upsert затирал бы чужие значения (напр. сохранение частоты обнуляло бы CPA →
        # сломанный автостоп). exclude_unset гарантирует, что форма меняет только своё.
        provided = body.model_dump(exclude_unset=True)

        # Upsert через INSERT ... ON CONFLICT (offer_id) DO UPDATE
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(OfferRule.__table__).values(offer_id=offer_id, **provided)
        conflict_set = {key: getattr(stmt.excluded, key) for key in provided}
        conflict_set["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(
            constraint="uq_offer_rules_offer_id",
            set_=conflict_set,
        ).returning(
            OfferRule.__table__.c.offer_id,
            OfferRule.__table__.c.spend_no_event_threshold,
            OfferRule.__table__.c.cpa_threshold,
            OfferRule.__table__.c.cpm_threshold,
            OfferRule.__table__.c.ctr_threshold,
            OfferRule.__table__.c.frequency_threshold,
            OfferRule.__table__.c.funnel_ratio_threshold,
            OfferRule.__table__.c.stop_percent_of_rule,
            OfferRule.__table__.c.warning_percent_of_stop,
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
        stop_percent_of_rule=row["stop_percent_of_rule"],
        warning_percent_of_stop=row["warning_percent_of_stop"],
    )


# ─────────────────────── GET /offers/rules/preview ───────────────────────


@router.get("/offers/rules/preview", response_model=RulePreviewOut)
async def preview_rule_thresholds(
    cpa: Decimal = Query(..., gt=0, description="CPA ($) для расчёта порогов"),
    stop_percent_of_rule: Decimal = Query(Decimal("80"), ge=1, le=100),
    warning_percent_of_stop: Decimal = Query(Decimal("80"), ge=1, le=100),
) -> RulePreviewOut:
    """При какой $-стоимости сработают правила и ворнинги для CPA + чувствительности.

    Использует RuleContext — единый расчёт с автостопом: значения в превью ТОЧНО совпадают
    с реальными порогами, по которым observer отключает объявления. Базовые проценты
    (CPC 2% / CPL 10% / CPR 20% / spend 50-70%/70-90%) фиксированы.
    """
    from core.rules.types import (
        REGS_NO_DEP_STOP_COUNT,
        SPEND_NO_DEP_FROM_PERCENT,
        SPEND_NO_DEP_TO_PERCENT,
        SPEND_WITH_DEP_FROM_PERCENT,
        SPEND_WITH_DEP_TO_PERCENT,
        RuleContext,
    )

    ctx = RuleContext(
        cpa_amount=cpa,
        warning_percent_of_stop=warning_percent_of_stop,
        stop_percent_of_base=stop_percent_of_rule,
    )
    cost_rules = [
        RuleThresholdPreview(
            rule="cpc_stop",
            label="Цена клика",
            base=ctx.cpc_base_stop_threshold,
            stop=ctx.cpc_stop_threshold,
            warning=ctx.cpc_warning_threshold,
        ),
        RuleThresholdPreview(
            rule="cpl_stop",
            label="Цена лида",
            base=ctx.cpl_base_stop_threshold,
            stop=ctx.cpl_stop_threshold,
            warning=ctx.cpl_warning_threshold,
        ),
        RuleThresholdPreview(
            rule="cpr_stop",
            label="Цена реги",
            base=ctx.cpr_base_stop_threshold,
            stop=ctx.cpr_stop_threshold,
            warning=ctx.cpr_warning_threshold,
        ),
    ]
    q = Decimal("0.01")

    def _spend(from_pct: Decimal, to_pct: Decimal, rule: str, label: str) -> SpendRangePreview:
        # Та же цепочка, что в evaluator: effective% = base% × stop%/100; $ = CPA × effective%/100.
        eff_from = from_pct * stop_percent_of_rule / Decimal("100")
        eff_to = to_pct * stop_percent_of_rule / Decimal("100")
        warn_from = eff_from * warning_percent_of_stop / Decimal("100")
        return SpendRangePreview(
            rule=rule,
            label=label,
            stop_from=(cpa * eff_from / Decimal("100")).quantize(q, ROUND_HALF_UP),
            stop_to=(cpa * eff_to / Decimal("100")).quantize(q, ROUND_HALF_UP),
            warning_from=(cpa * warn_from / Decimal("100")).quantize(q, ROUND_HALF_UP),
        )

    spend_ranges = [
        _spend(
            SPEND_NO_DEP_FROM_PERCENT,
            SPEND_NO_DEP_TO_PERCENT,
            "spend_no_dep_range",
            "Расход без депов",
        ),
        _spend(
            SPEND_WITH_DEP_FROM_PERCENT,
            SPEND_WITH_DEP_TO_PERCENT,
            "spend_with_dep_range",
            "Расход с депом",
        ),
    ]
    return RulePreviewOut(
        cpa=cpa,
        stop_percent_of_rule=stop_percent_of_rule,
        warning_percent_of_stop=warning_percent_of_stop,
        cost_rules=cost_rules,
        spend_ranges=spend_ranges,
        regs_no_dep_stop_count=REGS_NO_DEP_STOP_COUNT,
    )
