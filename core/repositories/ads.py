from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from core.domain import DecisionType, DeliveryStatus, ScopePresence, TrackingMode
from core.models.advertising import Ad, AdSet, Campaign, MetricSnapshot
from core.models.operations import ScanRun
from core.repositories.base import AsyncRepository


class AdsRepository(AsyncRepository):
    """Репозиторий для кампаний, адсетов, объявлений и снимков метрик."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    def _is_postgresql(self) -> bool:
        """Проверяет, используется ли PostgreSQL в качестве диалекта."""
        bind = self.session.get_bind()
        return bind.dialect.name == "postgresql"

    def _restore_utc(self, value: datetime | None) -> datetime | None:
        """Возвращает дату с UTC, если SQLite снял timezone при чтении."""
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)

    def _restore_ad_datetimes(self, ad: Ad) -> None:
        """Возвращает timezone на чтении так, чтобы ORM не считал объект изменённым."""
        restored_last_seen_at = self._restore_utc(ad.last_seen_at)
        if restored_last_seen_at is not ad.last_seen_at:
            set_committed_value(ad, "last_seen_at", restored_last_seen_at)
        restored_last_action_at = self._restore_utc(ad.last_action_at)
        if restored_last_action_at is not ad.last_action_at:
            set_committed_value(ad, "last_action_at", restored_last_action_at)

    async def upsert_campaign(
        self,
        scope_key: str,
        name: str,
        tracking_mode: TrackingMode = TrackingMode.TRACKED,
        last_seen_at: datetime | None = None,
        fb_campaign_id: str | None = None,
    ) -> Campaign:
        if self._is_postgresql():
            return await self._upsert_campaign_pg(
                scope_key, name, tracking_mode, last_seen_at, fb_campaign_id
            )
        return await self._upsert_campaign_fallback(
            scope_key, name, tracking_mode, last_seen_at, fb_campaign_id
        )

    async def _upsert_campaign_pg(
        self,
        scope_key: str,
        name: str,
        tracking_mode: TrackingMode,
        last_seen_at: datetime | None,
        fb_campaign_id: str | None,
    ) -> Campaign:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(Campaign).values(
            scope_key=scope_key,
            fb_campaign_id=fb_campaign_id,
            name=name,
            tracking_mode=tracking_mode,
            last_seen_at=last_seen_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Campaign.scope_key],
            set_={
                "name": stmt.excluded.name,
                "fb_campaign_id": stmt.excluded.fb_campaign_id,
                "tracking_mode": stmt.excluded.tracking_mode,
                "last_seen_at": stmt.excluded.last_seen_at,
            },
        ).returning(Campaign)
        result = await self.session.execute(stmt)
        campaign = result.scalars().first()
        await self.session.flush()
        return campaign

    async def _upsert_campaign_fallback(
        self,
        scope_key: str,
        name: str,
        tracking_mode: TrackingMode,
        last_seen_at: datetime | None,
        fb_campaign_id: str | None,
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
        if self._is_postgresql():
            return await self._upsert_adset_pg(
                scope_key, campaign_id, name, tracking_mode, last_seen_at, fb_adset_id
            )
        return await self._upsert_adset_fallback(
            scope_key, campaign_id, name, tracking_mode, last_seen_at, fb_adset_id
        )

    async def _upsert_adset_pg(
        self,
        scope_key: str,
        campaign_id: str,
        name: str,
        tracking_mode: TrackingMode,
        last_seen_at: datetime | None,
        fb_adset_id: str | None,
    ) -> AdSet:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(AdSet).values(
            scope_key=scope_key,
            fb_adset_id=fb_adset_id,
            campaign_id=campaign_id,
            name=name,
            tracking_mode=tracking_mode,
            last_seen_at=last_seen_at,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[AdSet.scope_key],
            set_={
                "campaign_id": stmt.excluded.campaign_id,
                "name": stmt.excluded.name,
                "fb_adset_id": stmt.excluded.fb_adset_id,
                "tracking_mode": stmt.excluded.tracking_mode,
                "last_seen_at": stmt.excluded.last_seen_at,
            },
        ).returning(AdSet)
        result = await self.session.execute(stmt)
        adset = result.scalars().first()
        await self.session.flush()
        return adset

    async def _upsert_adset_fallback(
        self,
        scope_key: str,
        campaign_id: str,
        name: str,
        tracking_mode: TrackingMode,
        last_seen_at: datetime | None,
        fb_adset_id: str | None,
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
        if self._is_postgresql():
            return await self._upsert_ad_pg(
                fb_ad_id,
                campaign_id,
                adset_id,
                name,
                delivery_status,
                tracking_mode,
                scope_presence,
                last_seen_at,
                last_action_source,
                last_action_at,
                last_decision,
                last_scan_run_id,
            )
        return await self._upsert_ad_fallback(
            fb_ad_id,
            campaign_id,
            adset_id,
            name,
            delivery_status,
            tracking_mode,
            scope_presence,
            last_seen_at,
            last_action_source,
            last_action_at,
            last_decision,
            last_scan_run_id,
        )

    async def _upsert_ad_pg(
        self,
        fb_ad_id: str,
        campaign_id: str,
        adset_id: str,
        name: str,
        delivery_status: DeliveryStatus,
        tracking_mode: TrackingMode,
        scope_presence: ScopePresence,
        last_seen_at: datetime | None,
        last_action_source: str | None,
        last_action_at: datetime | None,
        last_decision: DecisionType,
        last_scan_run_id: str | None,
    ) -> Ad:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = pg_insert(Ad).values(
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
        stmt = stmt.on_conflict_do_update(
            index_elements=[Ad.fb_ad_id],
            set_={
                "campaign_id": stmt.excluded.campaign_id,
                "adset_id": stmt.excluded.adset_id,
                "name": stmt.excluded.name,
                "delivery_status": stmt.excluded.delivery_status,
                "tracking_mode": stmt.excluded.tracking_mode,
                "scope_presence": stmt.excluded.scope_presence,
                "last_seen_at": stmt.excluded.last_seen_at,
                "last_action_source": func.coalesce(
                    stmt.excluded.last_action_source,
                    Ad.last_action_source,
                ),
                "last_action_at": func.coalesce(
                    stmt.excluded.last_action_at,
                    Ad.last_action_at,
                ),
                "last_decision": stmt.excluded.last_decision,
                "last_scan_run_id": stmt.excluded.last_scan_run_id,
            },
        ).returning(Ad)
        result = await self.session.execute(stmt)
        ad = result.scalars().first()
        await self.session.flush()
        return ad

    async def _upsert_ad_fallback(
        self,
        fb_ad_id: str,
        campaign_id: str,
        adset_id: str,
        name: str,
        delivery_status: DeliveryStatus,
        tracking_mode: TrackingMode,
        scope_presence: ScopePresence,
        last_seen_at: datetime | None,
        last_action_source: str | None,
        last_action_at: datetime | None,
        last_decision: DecisionType,
        last_scan_run_id: str | None,
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
            if last_action_source is not None:
                ad.last_action_source = last_action_source
            if last_action_at is not None:
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
        ad = result.first()
        if ad is not None:
            await self.session.refresh(ad)
            self._restore_ad_datetimes(ad)
        return ad

    async def get_ads_by_fb_ad_ids(self, fb_ad_ids: list[str]) -> dict[str, Ad]:
        if not fb_ad_ids:
            return {}
        result = await self.session.scalars(
            select(Ad)
            .options(
                selectinload(Ad.campaign),
                selectinload(Ad.adset),
            )
            .where(Ad.fb_ad_id.in_(fb_ad_ids))
        )
        return {ad.fb_ad_id: ad for ad in result.all()}

    async def list_profile_fb_ad_ids(self, profile_id: UUID | str) -> list[str]:
        result = await self.session.scalars(
            select(Ad.fb_ad_id)
            .join(ScanRun, Ad.last_scan_run_id == ScanRun.id)
            .where(ScanRun.profile_id == self._coerce_uuid(profile_id))
            .order_by(Ad.fb_ad_id)
        )
        return list(result.all())

    async def list_ads(
        self,
        *,
        profile_id: UUID | str | None = None,
        profile_launch_id: UUID | str | None = None,
    ) -> list[Ad]:
        stmt = (
            select(Ad)
            .options(
                selectinload(Ad.campaign),
                selectinload(Ad.adset),
            )
            .order_by(Ad.fb_ad_id)
        )
        if profile_launch_id is not None:
            stmt = (
                stmt.join(MetricSnapshot, MetricSnapshot.ad_id == Ad.id)
                .join(ScanRun, MetricSnapshot.scan_run_id == ScanRun.id)
                .where(ScanRun.profile_launch_id == self._coerce_uuid(profile_launch_id))
                .distinct()
            )
            if profile_id is not None:
                stmt = stmt.where(ScanRun.profile_id == self._coerce_uuid(profile_id))
        elif profile_id is not None:
            stmt = stmt.join(ScanRun, Ad.last_scan_run_id == ScanRun.id).where(
                ScanRun.profile_id == self._coerce_uuid(profile_id)
            )
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_latest_metric_snapshots(
        self,
        fb_ad_ids: list[str],
        profile_launch_id: UUID | str | None = None,
    ) -> dict[str, MetricSnapshot]:
        if not fb_ad_ids:
            return {}

        ranked_base = select(
            MetricSnapshot.id.label("snapshot_id"),
            MetricSnapshot.fb_ad_id.label("fb_ad_id"),
            func.row_number()
            .over(
                partition_by=MetricSnapshot.fb_ad_id,
                order_by=(MetricSnapshot.captured_at.desc(), MetricSnapshot.id.desc()),
            )
            .label("row_number"),
        )
        if profile_launch_id is not None:
            ranked_base = ranked_base.join(ScanRun, MetricSnapshot.scan_run_id == ScanRun.id).where(
                ScanRun.profile_launch_id == self._coerce_uuid(profile_launch_id)
            )
        ranked_snapshots = ranked_base.where(MetricSnapshot.fb_ad_id.in_(fb_ad_ids)).subquery()

        result = await self.session.scalars(
            select(MetricSnapshot)
            .join(
                ranked_snapshots,
                MetricSnapshot.id == ranked_snapshots.c.snapshot_id,
            )
            .where(ranked_snapshots.c.row_number == 1)
        )
        return {snapshot.fb_ad_id: snapshot for snapshot in result.all()}

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
        if last_action_source is not None:
            ad.last_action_source = last_action_source
        if last_action_at is not None:
            ad.last_action_at = last_action_at
        await self.session.flush()
        self._restore_ad_datetimes(ad)
        return ad

    async def mark_unseen_ads(
        self,
        *,
        seen_fb_ad_ids: list[str],
        profile_id: UUID | str,
        profile_launch_id: UUID | str | None = None,
    ) -> int:
        """Помечает объявления профиля, отсутствующие в текущем успешном скане, как NOT_SEEN_THIS_SCAN."""

        stmt = (
            select(Ad)
            .join(ScanRun, Ad.last_scan_run_id == ScanRun.id)
            .where(
                Ad.scope_presence == ScopePresence.IN_SCOPE,
                ScanRun.profile_id == self._coerce_uuid(profile_id),
            )
        )
        if profile_launch_id is not None:
            stmt = stmt.where(ScanRun.profile_launch_id == self._coerce_uuid(profile_launch_id))
        if seen_fb_ad_ids:
            stmt = stmt.where(Ad.fb_ad_id.notin_(seen_fb_ad_ids))
        result = await self.session.scalars(stmt)
        count = 0
        for ad in result.all():
            ad.scope_presence = ScopePresence.NOT_SEEN_THIS_SCAN
            count += 1
        if count > 0:
            await self.session.flush()
        return count

    @staticmethod
    def _coerce_uuid(value: UUID | str) -> UUID:
        if isinstance(value, UUID):
            return value
        return UUID(str(value))

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
