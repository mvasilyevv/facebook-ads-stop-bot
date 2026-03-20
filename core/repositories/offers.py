from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import EntityType
from core.models.offers import EntityOfferBinding, Offer, OfferRateVersion
from core.repositories.base import AsyncRepository


class OffersRepository(AsyncRepository):
    """Репозиторий для офферов, ставок и привязок."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_offers(self) -> list[Offer]:
        result = await self.session.scalars(select(Offer).order_by(Offer.code))
        return list(result.all())

    async def get_offer(self, offer_id: str) -> Offer | None:
        return await self.session.get(Offer, offer_id)

    async def get_offer_by_code(self, code: str) -> Offer | None:
        result = await self.session.scalars(select(Offer).where(Offer.code == code))
        return result.first()

    async def create_offer(self, code: str, name: str, is_active: bool = True) -> Offer:
        offer = Offer(code=code, name=name, is_active=is_active)
        self.session.add(offer)
        await self.session.flush()
        return offer

    async def add_rate_version(
        self,
        offer_id: str,
        cpa_usd: Decimal,
        effective_from: datetime,
        effective_to: datetime | None = None,
        note: str | None = None,
    ) -> OfferRateVersion:
        rate_version = OfferRateVersion(
            offer_id=offer_id,
            cpa_usd=cpa_usd,
            effective_from=effective_from,
            effective_to=effective_to,
            note=note,
        )
        self.session.add(rate_version)
        await self.session.flush()
        return rate_version

    async def list_rate_versions(self, offer_id: str) -> list[OfferRateVersion]:
        result = await self.session.scalars(
            select(OfferRateVersion)
            .where(OfferRateVersion.offer_id == offer_id)
            .order_by(OfferRateVersion.effective_from.desc())
        )
        return list(result.all())

    async def resolve_rate_version(
        self,
        offer_id: str,
        captured_at: datetime,
    ) -> OfferRateVersion | None:
        result = await self.session.scalars(
            select(OfferRateVersion)
            .where(
                OfferRateVersion.offer_id == offer_id,
                OfferRateVersion.effective_from <= captured_at,
                (OfferRateVersion.effective_to.is_(None))
                | (OfferRateVersion.effective_to > captured_at),
            )
            .order_by(OfferRateVersion.effective_from.desc())
        )
        return result.first()

    async def upsert_binding(
        self,
        entity_type: EntityType,
        entity_id: str,
        offer_id: str,
        priority: int = 0,
        is_active: bool = True,
    ) -> EntityOfferBinding:
        await self.session.execute(
            delete(EntityOfferBinding).where(
                EntityOfferBinding.entity_type == entity_type,
                EntityOfferBinding.entity_id == entity_id,
            )
        )
        binding = EntityOfferBinding(
            entity_type=entity_type,
            entity_id=entity_id,
            offer_id=offer_id,
            priority=priority,
            is_active=is_active,
        )
        self.session.add(binding)
        await self.session.flush()
        return binding

    async def list_bindings(
        self,
        entity_type: EntityType | None = None,
        entity_id: str | None = None,
        active_only: bool = False,
    ) -> list[EntityOfferBinding]:
        stmt = select(EntityOfferBinding)
        if entity_type is not None:
            stmt = stmt.where(EntityOfferBinding.entity_type == entity_type)
        if entity_id is not None:
            stmt = stmt.where(EntityOfferBinding.entity_id == entity_id)
        if active_only:
            stmt = stmt.where(EntityOfferBinding.is_active.is_(True))
        result = await self.session.scalars(stmt.order_by(EntityOfferBinding.priority.desc()))
        return list(result.all())

    async def resolve_binding(
        self,
        ad_id: str | None,
        adset_id: str | None,
    ) -> EntityOfferBinding | None:
        if ad_id is not None:
            result = await self.session.scalars(
                select(EntityOfferBinding)
                .where(
                    EntityOfferBinding.entity_type == EntityType.AD,
                    EntityOfferBinding.entity_id == ad_id,
                    EntityOfferBinding.is_active.is_(True),
                )
                .order_by(EntityOfferBinding.priority.desc())
            )
            binding = result.first()
            if binding is not None:
                return binding

        if adset_id is not None:
            result = await self.session.scalars(
                select(EntityOfferBinding)
                .where(
                    EntityOfferBinding.entity_type == EntityType.ADSET,
                    EntityOfferBinding.entity_id == adset_id,
                    EntityOfferBinding.is_active.is_(True),
                )
                .order_by(EntityOfferBinding.priority.desc())
            )
            return result.first()

        return None
