# -*- coding: utf-8 -*-
"""Роутер для управления офферами: CRUD + rules.

Endpoints (все с prefix /api из fail-fast registry):
    GET    /offers                   — список офферов
    POST   /offers                   — создать оффер
    PUT    /offers/{id}              — обновить оффер
    DELETE /offers/{id}              — идемпотентная деактивация
    GET    /offers/{id}/rules        — правила оффера
    PUT    /offers/{id}/rules        — upsert правил оффера

Важные семантики:
    - identity (code/name) immutable; PUT accepts mutable fields only.
    - DELETE означает только soft-deactivation и идемпотентен.
    - Метрики офферов отображаются через state-aware analytics, а не через
      отдельный COALESCE-агрегат каталога.
"""

from __future__ import annotations

import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from apps.api.deps import DepEngine
from apps.api.routers.v1.schemas.offers import (
    OfferCreateIn,
    OfferOut,
    OfferRuleOut,
    OfferRuleUpsertIn,
    OfferUpdateIn,
    RulePreviewOut,
    RuleThresholdPreview,
    SpendRangePreview,
)
from core.ad_account_catalog import ad_account_catalog
from core.models.catalog.offer import Offer
from core.models.catalog.offer_rule import OfferRule
from core.money import (
    InvalidCurrencyAmountError,
    UnsupportedCurrencyExponentError,
    currency_exponent,
    require_exact_currency_amount,
    validated_currency_code,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["offers"])

RuleCpaQuery = Annotated[
    str,
    Query(
        min_length=1,
        max_length=32,
        pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
        description="CPA as an exact major-unit decimal string",
    ),
]


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
            select(o, r.c.cpa_threshold, r.c.currency)
            .select_from(o.outerjoin(r, r.c.offer_id == o.c.id))
            .order_by(o.c.created_at.desc())
        )
        if not include_inactive:
            stmt = stmt.where(o.c.is_active.is_(True))
        result = await conn.execute(stmt)
        rows = result.mappings().all()
        account_ids_by_offer = await ad_account_catalog.list_by_offer(
            conn,
            offer_ids=(row["id"] for row in rows),
        )

    return [
        OfferOut(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            pixel_id=row["pixel_id"],
            is_active=row["is_active"],
            ad_account_ids=account_ids_by_offer.get(row["id"], []),
            countries=list(row["countries"] or []),
            cpa_threshold=row["cpa_threshold"],
            currency=row["currency"],
            created_at=row["created_at"].isoformat() if row["created_at"] else None,
            updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
        )
        for row in rows
    ]


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
                pixel_id=(body.pixel_id or None),
                is_active=body.is_active,
                # Гео оффера (ISO-2 upper) — для дерайва визарда.
                countries=body.countries,
            )
            .returning(
                Offer.__table__.c.id,
                Offer.__table__.c.code,
                Offer.__table__.c.name,
                Offer.__table__.c.pixel_id,
                Offer.__table__.c.is_active,
                Offer.__table__.c.countries,
                Offer.__table__.c.created_at,
                Offer.__table__.c.updated_at,
            )
        )
        try:
            result = await conn.execute(stmt)
            row = result.mappings().one()
            account_ids = await ad_account_catalog.replace_offer_accounts(
                conn,
                offer_id=row["id"],
                account_ids=body.ad_account_ids,
            )
        except IntegrityError as exc:
            err_str = str(exc).lower()
            if "unique" in err_str or "duplicate" in err_str:
                raise HTTPException(
                    status_code=409,
                    detail=f"Оффер с кодом '{body.code}' уже существует",
                ) from exc
            raise

    return OfferOut(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        pixel_id=row["pixel_id"],
        is_active=row["is_active"],
        ad_account_ids=list(account_ids),
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

    404 если оффер не найден.
    """
    updates: dict = {}
    # pixel_id: None — не трогаем; строка (в т.ч. пустая → null) — заменяем.
    if body.pixel_id is not None:
        updates["pixel_id"] = body.pixel_id or None
    if body.is_active is not None:
        updates["is_active"] = body.is_active
    # Гео: None — не трогаем; список (в т.ч. пустой) — замена (нормализация в OfferUpdateIn).
    if body.countries is not None:
        updates["countries"] = body.countries

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
                    Offer.__table__.c.pixel_id,
                    Offer.__table__.c.is_active,
                    Offer.__table__.c.countries,
                    Offer.__table__.c.created_at,
                    Offer.__table__.c.updated_at,
                )
            )
            result = await conn.execute(stmt)
            row = result.mappings().one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail="Оффер не найден")

        if body.ad_account_ids is not None:
            account_ids = list(
                await ad_account_catalog.replace_offer_accounts(
                    conn,
                    offer_id=row["id"],
                    account_ids=body.ad_account_ids,
                )
            )
        else:
            account_ids = (await ad_account_catalog.list_by_offer(conn, offer_ids=[row["id"]])).get(
                row["id"], []
            )

    return OfferOut(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        pixel_id=row["pixel_id"],
        is_active=row["is_active"],
        ad_account_ids=account_ids,
        countries=list(row["countries"] or []),
        created_at=row["created_at"].isoformat() if row["created_at"] else None,
        updated_at=row["updated_at"].isoformat() if row["updated_at"] else None,
    )


# ─────────────────────── DEACTIVATE /offers/{id} ───────────────────────


@router.delete("/offers/{offer_id}", status_code=204)
async def deactivate_offer(
    offer_id: uuid.UUID,
    engine: DepEngine,
) -> None:
    """Idempotently deactivate an offer while retaining its history."""
    async with engine.begin() as conn:
        stmt = (
            Offer.__table__.update()
            .where(Offer.__table__.c.id == offer_id)
            .values(is_active=False, updated_at=func.now())
            .returning(Offer.__table__.c.id)
        )
        result = await conn.execute(stmt)
        if result.rowcount == 0:
            raise HTTPException(
                status_code=404,
                detail="Оффер не найден",
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
        cpa_threshold=row["cpa_threshold"],
        currency=row["currency"],
        frequency_threshold=row["frequency_threshold"],
        stop_percent_of_rule=row["stop_percent_of_rule"],
        warning_percent_of_stop=row["warning_percent_of_stop"],
        cpc_percent_of_cpa=row["cpc_percent_of_cpa"],
        cpl_percent_of_cpa=row["cpl_percent_of_cpa"],
        cpr_percent_of_cpa=row["cpr_percent_of_cpa"],
        regs_no_dep_stop_count=row["regs_no_dep_stop_count"],
        spend_no_dep_from_percent=row["spend_no_dep_from_percent"],
        spend_no_dep_to_percent=row["spend_no_dep_to_percent"],
        spend_with_dep_from_percent=row["spend_with_dep_from_percent"],
        spend_with_dep_to_percent=row["spend_with_dep_to_percent"],
        min_ratio_denominator=row["min_ratio_denominator"],
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
            OfferRule.__table__.c.cpa_threshold,
            OfferRule.__table__.c.currency,
            OfferRule.__table__.c.frequency_threshold,
            OfferRule.__table__.c.stop_percent_of_rule,
            OfferRule.__table__.c.warning_percent_of_stop,
            OfferRule.__table__.c.cpc_percent_of_cpa,
            OfferRule.__table__.c.cpl_percent_of_cpa,
            OfferRule.__table__.c.cpr_percent_of_cpa,
            OfferRule.__table__.c.regs_no_dep_stop_count,
            OfferRule.__table__.c.spend_no_dep_from_percent,
            OfferRule.__table__.c.spend_no_dep_to_percent,
            OfferRule.__table__.c.spend_with_dep_from_percent,
            OfferRule.__table__.c.spend_with_dep_to_percent,
            OfferRule.__table__.c.min_ratio_denominator,
        )

        result = await conn.execute(stmt)
        row = result.mappings().one()

    return OfferRuleOut(
        offer_id=row["offer_id"],
        cpa_threshold=row["cpa_threshold"],
        currency=row["currency"],
        frequency_threshold=row["frequency_threshold"],
        stop_percent_of_rule=row["stop_percent_of_rule"],
        warning_percent_of_stop=row["warning_percent_of_stop"],
        cpc_percent_of_cpa=row["cpc_percent_of_cpa"],
        cpl_percent_of_cpa=row["cpl_percent_of_cpa"],
        cpr_percent_of_cpa=row["cpr_percent_of_cpa"],
        regs_no_dep_stop_count=row["regs_no_dep_stop_count"],
        spend_no_dep_from_percent=row["spend_no_dep_from_percent"],
        spend_no_dep_to_percent=row["spend_no_dep_to_percent"],
        spend_with_dep_from_percent=row["spend_with_dep_from_percent"],
        spend_with_dep_to_percent=row["spend_with_dep_to_percent"],
        min_ratio_denominator=row["min_ratio_denominator"],
    )


# ─────────────────────── GET /offers/rules/preview ───────────────────────


@router.get("/offers/rules/preview", response_model=RulePreviewOut)
async def preview_rule_thresholds(
    cpa: RuleCpaQuery,
    currency: str = Query(..., min_length=3, max_length=3),
    stop_percent_of_rule: Decimal = Query(Decimal("80"), ge=1, le=100),
    warning_percent_of_stop: Decimal = Query(Decimal("80"), ge=1, le=100),
) -> RulePreviewOut:
    """При какой $-стоимости сработают правила и ворнинги для CPA + чувствительности.

    Использует RuleContext — единый расчёт с автостопом: значения в превью ТОЧНО совпадают
    с реальными порогами, по которым observer отключает объявления. Базовые проценты
    (CPC 2% / CPL 10% / CPR 20% / spend 50-70%/70-90%) фиксированы.
    """
    normalized_currency = validated_currency_code(currency)
    if normalized_currency is None:
        raise HTTPException(status_code=422, detail="Неверный трёхбуквенный код валюты")
    try:
        exponent = currency_exponent(normalized_currency)
        cpa_amount = require_exact_currency_amount(
            Decimal(cpa),
            currency=normalized_currency,
            exponent=exponent,
            field="cpa",
            allow_zero=False,
        )
    except (UnsupportedCurrencyExponentError, InvalidCurrencyAmountError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    from core.rules.types import (
        REGS_NO_DEP_STOP_COUNT,
        SPEND_NO_DEP_FROM_PERCENT,
        SPEND_NO_DEP_TO_PERCENT,
        SPEND_WITH_DEP_FROM_PERCENT,
        SPEND_WITH_DEP_TO_PERCENT,
        RuleContext,
    )

    ctx = RuleContext(
        currency=normalized_currency,
        currency_exponent=exponent,
        cpa_amount=cpa_amount,
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
    q = ctx.money_quantum

    def _spend(from_pct: Decimal, to_pct: Decimal, rule: str, label: str) -> SpendRangePreview:
        # Та же цепочка, что в evaluator: effective% = base% × stop%/100;
        # порог в major units = CPA × effective%/100.
        eff_from = from_pct * stop_percent_of_rule / Decimal("100")
        eff_to = to_pct * stop_percent_of_rule / Decimal("100")
        warn_from = eff_from * warning_percent_of_stop / Decimal("100")
        return SpendRangePreview(
            rule=rule,
            label=label,
            stop_from=(cpa_amount * eff_from / Decimal("100")).quantize(
                q,
                ROUND_HALF_UP,
            ),
            stop_to=(cpa_amount * eff_to / Decimal("100")).quantize(
                q,
                ROUND_HALF_UP,
            ),
            warning_from=(cpa_amount * warn_from / Decimal("100")).quantize(
                q,
                ROUND_HALF_UP,
            ),
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
        cpa=cpa_amount,
        currency=normalized_currency,
        stop_percent_of_rule=stop_percent_of_rule,
        warning_percent_of_stop=warning_percent_of_stop,
        cost_rules=cost_rules,
        spend_ranges=spend_ranges,
        regs_no_dep_stop_count=REGS_NO_DEP_STOP_COUNT,
    )
