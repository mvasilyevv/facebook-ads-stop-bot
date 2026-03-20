from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from apps.api.config import ApiSettings
from apps.api.schemas.common import ControlFlagTarget
from apps.api.schemas.offers import (
    OfferBindingCreateRequest,
    OfferCreateRequest,
    OfferRateCreateRequest,
)
from apps.api.services.offers import OffersService
from apps.api.services.state import build_api_state


# Проверяет, что ставка оффера, привязанного к адсету, автоматически попадает в resolved CPA объявления.
def test_offer_rate_binding_updates_ad_cpa() -> None:
    state = build_api_state(ApiSettings())
    service = OffersService(store=state.store)
    offer_response = service.create_offer(OfferCreateRequest(code="offer-1", name="Оффер 1"))
    service.create_rate(
        offer_id=offer_response.offer.id,
        payload=OfferRateCreateRequest(
            cpa_usd=Decimal("5.00"),
            effective_from=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
        ),
    )

    service.bind_offer(
        entity_type=ControlFlagTarget.ADSET,
        entity_external_id="demo-adset-1",
        payload=OfferBindingCreateRequest(offer_id=offer_response.offer.id),
    )

    updated_ad = state.store.ads["demo-ad-1"]
    assert updated_ad.resolved_cpa_usd == Decimal("5.00")


# Проверяет, что привязка оффера к объявлению переопределяет ставку, пришедшую с уровня адсета.
def test_offer_binding_for_ad_overrides_adset_binding() -> None:
    state = build_api_state(ApiSettings())
    service = OffersService(store=state.store)
    adset_offer = service.create_offer(
        OfferCreateRequest(code="offer-adset", name="Оффер адсета")
    ).offer
    ad_offer = service.create_offer(
        OfferCreateRequest(code="offer-ad", name="Оффер объявления")
    ).offer

    service.create_rate(
        offer_id=adset_offer.id,
        payload=OfferRateCreateRequest(
            cpa_usd=Decimal("5.00"),
            effective_from=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
        ),
    )
    service.create_rate(
        offer_id=ad_offer.id,
        payload=OfferRateCreateRequest(
            cpa_usd=Decimal("8.00"),
            effective_from=datetime(2026, 3, 20, 10, 0, tzinfo=UTC),
        ),
    )
    service.bind_offer(
        entity_type=ControlFlagTarget.ADSET,
        entity_external_id="demo-adset-1",
        payload=OfferBindingCreateRequest(offer_id=adset_offer.id),
    )

    service.bind_offer(
        entity_type=ControlFlagTarget.AD,
        entity_external_id="demo-ad-1",
        payload=OfferBindingCreateRequest(offer_id=ad_offer.id, priority=10),
    )

    updated_ad = state.store.ads["demo-ad-1"]
    assert updated_ad.resolved_cpa_usd == Decimal("8.00")
