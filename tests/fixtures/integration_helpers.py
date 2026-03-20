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
from apps.api.services.state import ApiState, build_api_state
from apps.notifier.events import TelegramEvent, TelegramEventPayload, TelegramEventType


class MemoryTelegramTransport:
    """Простой транспорт для проверки текста Telegram-сообщений в интеграционных сценариях."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


def build_demo_state() -> tuple[ApiState, OffersService]:
    state = build_api_state(ApiSettings())
    return state, OffersService(store=state.store)


def create_bound_offer_with_rate(
    offers_service: OffersService,
    *,
    offer_code: str,
    offer_name: str,
    cpa_usd: Decimal,
    effective_from: datetime,
    entity_type: ControlFlagTarget,
    entity_external_id: str,
    priority: int = 0,
) -> str:
    offer = offers_service.create_offer(OfferCreateRequest(code=offer_code, name=offer_name)).offer
    offers_service.create_rate(
        offer_id=offer.id,
        payload=OfferRateCreateRequest(
            cpa_usd=cpa_usd,
            effective_from=effective_from,
        ),
    )
    offers_service.bind_offer(
        entity_type=entity_type,
        entity_external_id=entity_external_id,
        payload=OfferBindingCreateRequest(offer_id=offer.id, priority=priority),
    )
    return offer.id


def build_telegram_event(
    event_type: TelegramEventType,
    *,
    reason: str,
    metrics: dict[str, str | int],
    delivery_before: str | None = None,
    delivery_after: str | None = None,
    rule_id: str | None = None,
) -> TelegramEvent:
    return TelegramEvent(
        event_type=event_type,
        dedupe_key=f"{event_type.value}:{reason}",
        created_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
        payload=TelegramEventPayload(
            host="browser-host-01",
            account_name="demo-acc",
            campaign_name="Демо кампания",
            adset_name="Демо адсет",
            ad_name="Демо объявление",
            fb_ad_id="demo-ad-1",
            reason=reason,
            metrics=metrics,
            delivery_before=delivery_before,
            delivery_after=delivery_after,
            rule_id=rule_id,
        ),
    )


def build_low_risk_metrics(
    *,
    spend: Decimal,
    cpc: Decimal | None,
    leads: int,
    cost_per_lead: Decimal | None,
    registrations: int,
    cost_per_registration: Decimal | None,
    deposits: int,
) -> dict[str, Decimal | int | None]:
    return {
        "spend": spend,
        "clicks": 0,
        "cpc": cpc,
        "leads": leads,
        "cost_per_lead": cost_per_lead,
        "registrations": registrations,
        "cost_per_registration": cost_per_registration,
        "deposits": deposits,
    }
