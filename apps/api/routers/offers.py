from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.common import ControlFlagTarget
from apps.api.schemas.offers import (
    OfferActionResponse,
    OfferBindingActionResponse,
    OfferBindingCreateRequest,
    OfferCreateRequest,
    OfferItem,
    OfferRateActionResponse,
    OfferRateCreateRequest,
)
from core.domain import EntityType
from core.repositories import OffersRepository

router = APIRouter(tags=["offers"])


def _map_offer_item(offer, current_cpa_usd) -> OfferItem:
    return OfferItem(
        id=str(offer.id),
        code=offer.code,
        name=offer.name,
        is_active=offer.is_active,
        current_cpa_usd=current_cpa_usd,
        created_at=offer.created_at,
        updated_at=offer.updated_at,
    )


def _map_rate_action_response(rate) -> OfferRateActionResponse:
    return OfferRateActionResponse(
        message="Ставка оффера сохранена",
        rate={
            "id": str(rate.id),
            "offer_id": str(rate.offer_id),
            "cpa_usd": rate.cpa_usd,
            "effective_from": rate.effective_from,
            "effective_to": rate.effective_to,
            "note": rate.note,
            "created_at": rate.created_at,
        },
    )


def _map_binding_action_response(
    binding, offer_code: str, message: str
) -> OfferBindingActionResponse:
    return OfferBindingActionResponse(
        message=message,
        binding={
            "id": str(binding.id),
            "entity_type": binding.entity_type.value,
            "entity_external_id": binding.entity_id,
            "offer_id": str(binding.offer_id),
            "offer_code": offer_code,
            "priority": binding.priority,
            "is_active": binding.is_active,
            "created_at": binding.created_at,
            "updated_at": binding.updated_at,
        },
    )


@router.get("/offers", response_model=list[OfferItem])
async def list_offers(session: DbSessionDep) -> list[OfferItem]:
    repo = OffersRepository(session)
    offers = await repo.list_offers()
    result: list[OfferItem] = []
    for offer in offers:
        rate = await repo.resolve_rate_version(offer.id, datetime.now(tz=UTC))
        result.append(_map_offer_item(offer, rate.cpa_usd if rate is not None else None))
    return result


@router.post(
    "/offers",
    response_model=OfferActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_offer(
    payload: OfferCreateRequest,
    session: DbSessionDep,
) -> OfferActionResponse:
    repo = OffersRepository(session)
    offer = await repo.create_offer(
        code=payload.code,
        name=payload.name,
        is_active=payload.is_active,
    )
    await session.commit()
    return OfferActionResponse(
        message="Оффер создан",
        offer=_map_offer_item(offer, current_cpa_usd=None),
    )


@router.post(
    "/offers/{offer_id}/rates",
    response_model=OfferRateActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_offer_rate(
    offer_id: str,
    payload: OfferRateCreateRequest,
    session: DbSessionDep,
) -> OfferRateActionResponse:
    repo = OffersRepository(session)
    offer = await repo.get_offer(offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Оффер не найден")
    rate = await repo.add_rate_version(
        offer_id=offer_id,
        cpa_usd=payload.cpa_usd,
        effective_from=payload.effective_from,
        effective_to=payload.effective_to,
        note=payload.note,
    )
    await session.commit()
    return _map_rate_action_response(rate)


@router.post(
    "/adsets/{fb_adset_id}/offer-binding",
    response_model=OfferBindingActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bind_offer_to_adset(
    fb_adset_id: str,
    payload: OfferBindingCreateRequest,
    session: DbSessionDep,
) -> OfferBindingActionResponse:
    repo = OffersRepository(session)
    offer = await repo.get_offer(payload.offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Оффер не найден")
    binding = await repo.upsert_binding(
        entity_type=EntityType(ControlFlagTarget.ADSET.value),
        entity_id=fb_adset_id,
        offer_id=payload.offer_id,
        priority=payload.priority,
        is_active=payload.is_active,
    )
    await session.commit()
    return _map_binding_action_response(binding, offer.code, "Оффер привязан к адсету")


@router.post(
    "/ads/{fb_ad_id}/offer-binding",
    response_model=OfferBindingActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bind_offer_to_ad(
    fb_ad_id: str,
    payload: OfferBindingCreateRequest,
    session: DbSessionDep,
) -> OfferBindingActionResponse:
    repo = OffersRepository(session)
    offer = await repo.get_offer(payload.offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Оффер не найден")
    binding = await repo.upsert_binding(
        entity_type=EntityType(ControlFlagTarget.AD.value),
        entity_id=fb_ad_id,
        offer_id=payload.offer_id,
        priority=payload.priority,
        is_active=payload.is_active,
    )
    await session.commit()
    return _map_binding_action_response(binding, offer.code, "Оффер привязан к объявлению")
