from __future__ import annotations

import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status

from apps.api.deps import DbSessionDep
from apps.api.schemas.common import ControlFlagTarget
from apps.api.schemas.offers import (
    OfferActionResponse,
    OfferBindingActionResponse,
    OfferBindingCreateRequest,
    OfferBindingItem,
    OfferCreateRequest,
    OfferItem,
    OfferRateActionResponse,
    OfferRateCreateRequest,
)
from core.domain import EntityType
from core.repositories import OffersRepository

router = APIRouter(tags=["offers"])


async def _build_offer_code(repo: OffersRepository, name: str, explicit_code: str | None) -> str:
    """Собирает уникальный код оффера из имени или явного значения."""

    normalized_explicit_code = explicit_code.strip() if explicit_code is not None else None
    if normalized_explicit_code:
        return normalized_explicit_code

    base_code = re.sub(r"[^\w]+", "-", name.casefold(), flags=re.UNICODE).strip("-_")
    base_code = base_code.replace("_", "-")[:80]
    if not base_code:
        base_code = "offer"

    candidate = base_code
    suffix = 2
    while await repo.get_offer_by_code(candidate) is not None:
        candidate = f"{base_code}-{suffix}"
        suffix += 1
    return candidate


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
            "entity_id": binding.entity_id,
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
    offer_code = await _build_offer_code(repo, payload.name, payload.code)
    offer = await repo.create_offer(
        code=offer_code,
        name=payload.name,
        is_active=payload.is_active,
    )
    current_cpa_usd = None
    if payload.cpa_usd is not None:
        await repo.add_rate_version(
            offer_id=offer.id,
            cpa_usd=payload.cpa_usd,
            effective_from=datetime.now(tz=UTC),
            note="Создано из упрощенной формы оффера",
        )
        current_cpa_usd = payload.cpa_usd
    await session.commit()
    return OfferActionResponse(
        message="Оффер создан",
        offer=_map_offer_item(offer, current_cpa_usd=current_cpa_usd),
    )


@router.delete("/offers/{offer_id}", response_model=OfferActionResponse)
async def delete_offer(offer_id: str, session: DbSessionDep) -> OfferActionResponse:
    repo = OffersRepository(session)
    offer = await repo.get_offer(offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Оффер не найден")

    rate = await repo.resolve_rate_version(offer.id, datetime.now(tz=UTC))
    offer_item = _map_offer_item(offer, current_cpa_usd=rate.cpa_usd if rate is not None else None)
    await repo.delete_offer(offer_id)
    await session.commit()
    return OfferActionResponse(message="Оффер удален", offer=offer_item)


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


@router.get("/offer-bindings", response_model=list[OfferBindingItem])
async def list_offer_bindings(session: DbSessionDep) -> list[OfferBindingItem]:
    """Получить все привязки офферов к сущностям."""
    repo = OffersRepository(session)
    bindings = await repo.list_bindings()
    offer_ids = {str(b.offer_id) for b in bindings}
    offers_map: dict[str, str] = {}
    for oid in offer_ids:
        offer = await repo.get_offer(oid)
        if offer is not None:
            offers_map[oid] = offer.code
    result: list[OfferBindingItem] = []
    for binding in bindings:
        result.append(
            OfferBindingItem(
                id=str(binding.id),
                entity_type=binding.entity_type.value,
                entity_id=binding.entity_id,
                offer_id=str(binding.offer_id),
                offer_code=offers_map.get(str(binding.offer_id), "unknown"),
                priority=binding.priority,
                is_active=binding.is_active,
                created_at=binding.created_at,
                updated_at=binding.updated_at,
            )
        )
    return result


@router.post(
    "/adsets/{adset_scope_key}/offer-binding",
    response_model=OfferBindingActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def bind_offer_to_adset(
    adset_scope_key: str,
    payload: OfferBindingCreateRequest,
    session: DbSessionDep,
) -> OfferBindingActionResponse:
    repo = OffersRepository(session)
    offer = await repo.get_offer(payload.offer_id)
    if offer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Оффер не найден")
    binding = await repo.upsert_binding(
        entity_type=EntityType(ControlFlagTarget.ADSET.value),
        entity_id=adset_scope_key,
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
