from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from apps.notifier.events import TelegramEvent, TelegramEventPayload, TelegramEventType
from core.domain import DeliveryStatus, EntityType, ScopePresence, TrackingMode
from core.repositories import AdsRepository, OffersRepository


class MemoryTelegramTransport:
    """Простой транспорт для проверки текста Telegram-сообщений в интеграционных сценариях."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


async def seed_demo_ad(async_session) -> tuple[str, str]:
    ads_repo = AdsRepository(async_session)
    campaign = await ads_repo.upsert_campaign(
        fb_campaign_id="demo-campaign-1",
        name="Демо кампания",
        tracking_mode=TrackingMode.TRACKED,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
    )
    adset = await ads_repo.upsert_adset(
        fb_adset_id="demo-adset-1",
        campaign_id=campaign.id,
        name="Демо адсет",
        tracking_mode=TrackingMode.TRACKED,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
    )
    ad = await ads_repo.upsert_ad(
        fb_ad_id="demo-ad-1",
        campaign_id=campaign.id,
        adset_id=adset.id,
        name="Демо объявление",
        delivery_status=DeliveryStatus.ACTIVE,
        tracking_mode=TrackingMode.TRACKED,
        scope_presence=ScopePresence.IN_SCOPE,
        last_seen_at=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
    )
    await async_session.flush()
    return ad.fb_ad_id, adset.fb_adset_id


async def create_bound_offer_with_rate(
    async_session,
    *,
    offer_code: str,
    offer_name: str,
    cpa_usd: Decimal,
    effective_from: datetime,
    entity_type: EntityType,
    entity_external_id: str,
    priority: int = 0,
) -> str:
    offers_repo = OffersRepository(async_session)
    offer = await offers_repo.create_offer(code=offer_code, name=offer_name)
    await offers_repo.add_rate_version(
        offer_id=offer.id,
        cpa_usd=cpa_usd,
        effective_from=effective_from,
    )
    await offers_repo.upsert_binding(
        entity_type=entity_type,
        entity_id=entity_external_id,
        offer_id=offer.id,
        priority=priority,
    )
    return offer.id


async def resolve_current_cpa(async_session, *, fb_ad_id: str, adset_id: str) -> Decimal | None:
    offers_repo = OffersRepository(async_session)
    binding = await offers_repo.resolve_binding(fb_ad_id, adset_id)
    if binding is None:
        return None
    rate = await offers_repo.resolve_rate_version(
        binding.offer_id, datetime(2026, 3, 20, 12, 0, tzinfo=UTC)
    )
    if rate is None:
        return None
    return rate.cpa_usd


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
