from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import DecisionType, DeliveryStatus, ScopePresence, TrackingMode
from core.models.advertising import Ad, AdSet, Campaign, MetricSnapshot
from core.repositories.base import AsyncRepository


class AdsRepository(AsyncRepository):
    """Репозиторий для кампаний, адсетов, объявлений и снимков метрик."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def upsert_campaign(
        self,
        fb_campaign_id: str,
        name: str,
        tracking_mode: TrackingMode = TrackingMode.TRACKED,
        last_seen_at: datetime | None = None,
    ) -> Campaign:
        campaign = await self.get_campaign_by_fb_id(fb_campaign_id)
        if campaign is None:
            campaign = Campaign(
                fb_campaign_id=fb_campaign_id,
                name=name,
                tracking_mode=tracking_mode,
                last_seen_at=last_seen_at,
            )
            self.session.add(campaign)
        else:
            campaign.name = name
            campaign.tracking_mode = tracking_mode
            campaign.last_seen_at = last_seen_at
        await self.session.flush()
        return campaign

    async def get_campaign_by_fb_id(self, fb_campaign_id: str) -> Campaign | None:
        result = await self.session.scalars(
            select(Campaign).where(Campaign.fb_campaign_id == fb_campaign_id)
        )
        return result.first()

    async def upsert_adset(
        self,
        fb_adset_id: str,
        campaign_id: str,
        name: str,
        tracking_mode: TrackingMode = TrackingMode.TRACKED,
        last_seen_at: datetime | None = None,
    ) -> AdSet:
        adset = await self.get_adset_by_fb_id(fb_adset_id)
        if adset is None:
            adset = AdSet(
                fb_adset_id=fb_adset_id,
                campaign_id=campaign_id,
                name=name,
                tracking_mode=tracking_mode,
                last_seen_at=last_seen_at,
            )
            self.session.add(adset)
        else:
            adset.campaign_id = campaign_id
            adset.name = name
            adset.tracking_mode = tracking_mode
            adset.last_seen_at = last_seen_at
        await self.session.flush()
        return adset

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
        result = await self.session.scalars(select(Ad).where(Ad.fb_ad_id == fb_ad_id))
        return result.first()

    async def list_ads(self) -> list[Ad]:
        result = await self.session.scalars(select(Ad).order_by(Ad.fb_ad_id))
        return list(result.all())

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
    ) -> MetricSnapshot:
        snapshot = MetricSnapshot(
            fb_ad_id=fb_ad_id,
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
