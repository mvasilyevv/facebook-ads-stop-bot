from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

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
    OfferRateItem,
)
from apps.api.services.state import ApiStore


class OffersService:
    """Управляет офферами, ставками и привязками к adset/ad."""

    def __init__(self, store: ApiStore) -> None:
        self._store = store

    def list_offers(self) -> list[OfferItem]:
        return list(self._store.offers.values())

    def create_offer(self, payload: OfferCreateRequest) -> OfferActionResponse:
        now = datetime.now(tz=UTC)
        offer = OfferItem(
            id=str(uuid4()),
            code=payload.code,
            name=payload.name,
            is_active=payload.is_active,
            current_cpa_usd=None,
            created_at=now,
            updated_at=now,
        )
        self._store.offers[offer.id] = offer
        self._store.offer_rates.setdefault(offer.id, [])
        return OfferActionResponse(message="Оффер создан", offer=offer)

    def create_rate(
        self,
        offer_id: str,
        payload: OfferRateCreateRequest,
    ) -> OfferRateActionResponse | None:
        offer = self._store.offers.get(offer_id)
        if offer is None:
            return None

        now = datetime.now(tz=UTC)
        rate = OfferRateItem(
            id=str(uuid4()),
            offer_id=offer_id,
            cpa_usd=payload.cpa_usd,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            note=payload.note,
            created_at=now,
        )
        self._store.offer_rates.setdefault(offer_id, []).append(rate)

        updated_offer = offer.model_copy(
            update={
                "current_cpa_usd": self._resolve_current_rate_value(
                    offer_id=offer_id,
                    current_time=now,
                ),
                "updated_at": now,
            }
        )
        self._store.offers[offer_id] = updated_offer
        self._recalculate_resolved_cpa_for_ads(current_time=now)
        return OfferRateActionResponse(message="Ставка оффера сохранена", rate=rate)

    def bind_offer(
        self,
        entity_type: ControlFlagTarget,
        entity_external_id: str,
        payload: OfferBindingCreateRequest,
    ) -> OfferBindingActionResponse | None:
        offer = self._store.offers.get(payload.offer_id)
        if offer is None:
            return None

        now = datetime.now(tz=UTC)
        key = self._binding_key(entity_type=entity_type, entity_external_id=entity_external_id)
        binding = OfferBindingItem(
            id=str(uuid4()),
            entity_type=entity_type,
            entity_external_id=entity_external_id,
            offer_id=payload.offer_id,
            offer_code=offer.code,
            priority=payload.priority,
            is_active=payload.is_active,
            created_at=now,
            updated_at=now,
        )
        self._store.offer_bindings[key] = binding
        self._recalculate_resolved_cpa_for_ads(current_time=now)

        target_label = "объявлению" if entity_type == ControlFlagTarget.AD else "адсету"
        return OfferBindingActionResponse(
            message=f"Оффер привязан к {target_label}",
            binding=binding,
        )

    def _recalculate_resolved_cpa_for_ads(self, current_time: datetime) -> None:
        for fb_ad_id, ad in self._store.ads.items():
            resolved_cpa = self._resolve_cpa_for_ad(
                fb_ad_id=fb_ad_id,
                adset_id=ad.adset_id,
                current_time=current_time,
            )
            self._store.ads[fb_ad_id] = ad.model_copy(
                update={
                    "resolved_cpa_usd": resolved_cpa,
                    "updated_at": current_time,
                }
            )

    def _resolve_cpa_for_ad(
        self,
        fb_ad_id: str,
        adset_id: str,
        current_time: datetime,
    ) -> Decimal | None:
        binding = self._resolve_binding(
            entity_type=ControlFlagTarget.AD,
            entity_external_id=fb_ad_id,
        )
        if binding is None:
            binding = self._resolve_binding(
                entity_type=ControlFlagTarget.ADSET,
                entity_external_id=adset_id,
            )
        if binding is None:
            return None

        return self._resolve_current_rate_value(
            offer_id=binding.offer_id,
            current_time=current_time,
        )

    def _resolve_binding(
        self,
        entity_type: ControlFlagTarget,
        entity_external_id: str,
    ) -> OfferBindingItem | None:
        matches = [
            binding
            for binding in self._store.offer_bindings.values()
            if binding.entity_type == entity_type
            and binding.entity_external_id == entity_external_id
            and binding.is_active
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.priority, reverse=True)[0]

    def _resolve_current_rate_value(
        self,
        offer_id: str,
        current_time: datetime,
    ) -> Decimal | None:
        matches = [
            rate
            for rate in self._store.offer_rates.get(offer_id, [])
            if rate.effective_from <= current_time
            and (rate.effective_to is None or current_time < rate.effective_to)
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda item: item.effective_from, reverse=True)[0].cpa_usd

    @staticmethod
    def _binding_key(entity_type: ControlFlagTarget, entity_external_id: str) -> str:
        return f"{entity_type.value}:{entity_external_id}"
