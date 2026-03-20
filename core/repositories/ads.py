from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.domain import DecisionType, DeliveryStatus, ScopePresence, TrackingMode
from core.models.advertising import Ad, AdSet, Campaign, MetricSnapshot
from core.repositories.base import AsyncRepository


class AdsRepository(AsyncRepository):
    """Репозиторий для кампаний, адсетов, объявлений и снимков метрик."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def upsert_campaign(
        self,
        scope_key: str,
        name: str,
        tracking_mode: TrackingMode = TrackingMode.TRACKED,
        last_seen_at: datetime | None = None,
        fb_campaign_id: str | None = None,
    ) -> Campaign:
        campaign = await self.get_campaign_by_scope_key(scope_key)
        if campaign is None and fb_campaign_id is not None:
            campaign = await self.get_campaign_by_fb_id(fb_campaign_id)
        if campaign is None:
            campaign = Campaign(
                scope_key=scope_key,
                fb_campaign_id=fb_campaign_id,
                name=name,
                tracking_mode=tracking_mode,
                last_seen_at=last_seen_at,
            )
            self.session.add(campaign)
        else:
            campaign.scope_key = scope_key
            campaign.name = name
            campaign.fb_campaign_id = fb_campaign_id
            campaign.tracking_mode = tracking_mode
            campaign.last_seen_at = last_seen_at
        await self.session.flush()
        return campaign

    async def get_campaign_by_scope_key(self, scope_key: str) -> Campaign | None:
        result = await self.session.scalars(select(Campaign).where(Campaign.scope_key == scope_key))
        return result.first()

    async def get_campaign_by_fb_id(self, fb_campaign_id: str) -> Campaign | None:
        result = await self.session.scalars(
            select(Campaign).where(Campaign.fb_campaign_id == fb_campaign_id)
        )
        return result.first()

    async def upsert_adset(
        self,
        scope_key: str,
        campaign_id: str,
        name: str,
        tracking_mode: TrackingMode = TrackingMode.TRACKED,
        last_seen_at: datetime | None = None,
        fb_adset_id: str | None = None,
    ) -> AdSet:
        adset = await self.get_adset_by_scope_key(scope_key)
        if adset is None and fb_adset_id is not None:
            adset = await self.get_adset_by_fb_id(fb_adset_id)
        if adset is None:
            adset = AdSet(
                scope_key=scope_key,
                fb_adset_id=fb_adset_id,
                campaign_id=campaign_id,
                name=name,
                tracking_mode=tracking_mode,
                last_seen_at=last_seen_at,
            )
            self.session.add(adset)
        else:
            adset.scope_key = scope_key
            adset.campaign_id = campaign_id
            adset.name = name
            adset.fb_adset_id = fb_adset_id
            adset.tracking_mode = tracking_mode
            adset.last_seen_at = last_seen_at
        await self.session.flush()
        return adset

    async def get_adset_by_scope_key(self, scope_key: str) -> AdSet | None:
        result = await self.session.scalars(select(AdSet).where(AdSet.scope_key == scope_key))
        return result.first()

    async def get_adset_by_fb_id(self, fb_adset_id: str) -> AdSet | None:
        result = await self.session.scalars(select(AdSet).where(AdSet.fb_adset_id == fb_adset_id))
        return result.first()

    async def upsert_ad(
        self,
        fb_ad_id: str,
        campaign_id: str,
        adset_id: str,
        name: str,
        delivery_status: DeliveryStatus = DeliveryStatus.UNKNOWN,
        tracking_mode: TrackingMode = TrackingMode.TRACKED,
        scope_presence: ScopePresence = ScopePresence.NOT_SEEN_THIS_SCAN,
        last_seen_at: datetime | None = None,
        last_action_source: str | None = None,
        last_action_at: datetime | None = None,
        last_decision: DecisionType = DecisionType.NO_ACTION,
        last_scan_run_id: str | None = None,
    ) -> Ad:
        ad = await self.get_ad_by_fb_id(fb_ad_id)
        if ad is None:
            ad = Ad(
                fb_ad_id=fb_ad_id,
                campaign_id=campaign_id,
                adset_id=adset_id,
                name=name,
                delivery_status=delivery_status,
                tracking_mode=tracking_mode,
                scope_presence=scope_presence,
                last_seen_at=last_seen_at,
                last_action_source=last_action_source,
                last_action_at=last_action_at,
                last_decision=last_decision,
                last_scan_run_id=last_scan_run_id,
            )
            self.session.add(ad)
        else:
            ad.campaign_id = campaign_id
            ad.adset_id = adset_id
            ad.name = name
            ad.delivery_status = delivery_status
            ad.tracking_mode = tracking_mode
            ad.scope_presence = scope_presence
            ad.last_seen_at = last_seen_at
            ad.last_action_source = last_action_source
            ad.last_action_at = last_action_at
            ad.last_decision = last_decision
            ad.last_scan_run_id = last_scan_run_id
        await self.session.flush()
        return ad

    async def get_ad_by_fb_id(self, fb_ad_id: str) -> Ad | None:
        result = await self.session.scalars(
            select(Ad)
            .options(
                selectinload(Ad.campaign),
                selectinload(Ad.adset),
            )
            .where(Ad.fb_ad_id == fb_ad_id)
        )
        return result.first()

    async def list_ads(self) -> list[Ad]:
        result = await self.session.scalars(
            select(Ad)
            .options(
                selectinload(Ad.campaign),
                selectinload(Ad.adset),
            )
            .order_by(Ad.fb_ad_id)
        )
        return list(result.all())

    async def update_ad_review_state(
        self,
        fb_ad_id: str,
        *,
        tracking_mode: TrackingMode | None = None,
        scope_presence: ScopePresence | None = None,
        last_decision: DecisionType | None = None,
        last_action_source: str | None = None,
        last_action_at: datetime | None = None,
    ) -> Ad | None:
        ad = await self.get_ad_by_fb_id(fb_ad_id)
        if ad is None:
            return None
        if tracking_mode is not None:
            ad.tracking_mode = tracking_mode
        if scope_presence is not None:
            ad.scope_presence = scope_presence
        if last_decision is not None:
            ad.last_decision = last_decision
        ad.last_action_source = last_action_source
        ad.last_action_at = last_action_at
        await self.session.flush()
        return ad

    async def add_metric_snapshot(
        self,
        fb_ad_id: str,
        scan_run_id: str,
        captured_at: datetime,
        spend: Decimal | None = None,
        clicks: int | None = None,
        cpc: Decimal | None = None,
        leads: int | None = None,
        cost_per_lead: Decimal | None = None,
        registrations: int | None = None,
        cost_per_registration: Decimal | None = None,
        deposits: int | None = None,
        offer_id: str | None = None,
        offer_rate_version_id: str | None = None,
        resolved_cpa_usd: Decimal | None = None,
        ad_id: str | None = None,
    ) -> MetricSnapshot:
        snapshot = MetricSnapshot(
            fb_ad_id=fb_ad_id,
            ad_id=ad_id,
            scan_run_id=scan_run_id,
            captured_at=captured_at,
            spend=spend,
            clicks=clicks,
            cpc=cpc,
            leads=leads,
            cost_per_lead=cost_per_lead,
            registrations=registrations,
            cost_per_registration=cost_per_registration,
            deposits=deposits,
            offer_id=offer_id,
            offer_rate_version_id=offer_rate_version_id,
            resolved_cpa_usd=resolved_cpa_usd,
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot
