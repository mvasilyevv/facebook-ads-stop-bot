from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import (
    EntityType,
    extract_offer_code_from_ad_name,
    normalize_offer_lookup_key,
)
from core.models.offers import EntityOfferBinding, Offer, OfferRateVersion
from core.repositories.base import AsyncRepository


class OffersRepository(AsyncRepository):
    """Репозиторий для офферов, ставок и привязок."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    @staticmethod
    def _coerce_uuid(value: UUID | str) -> UUID:
        if isinstance(value, UUID):
            return value
        return UUID(str(value))

    async def list_offers(self, *, active_only: bool = False) -> list[Offer]:
        stmt = select(Offer).order_by(Offer.code)
        if active_only:
            stmt = stmt.where(Offer.is_active.is_(True))
        result = await self.session.scalars(stmt)
        return list(result.all())

    async def get_offer(self, offer_id: str) -> Offer | None:
        return await self.session.get(Offer, self._coerce_uuid(offer_id))

    async def get_offer_by_code(self, code: str) -> Offer | None:
        result = await self.session.scalars(select(Offer).where(Offer.code == code))
        return result.first()

    async def create_offer(self, code: str, name: str, is_active: bool = True) -> Offer:
        offer = Offer(code=code, name=name, is_active=is_active)
        self.session.add(offer)
        await self.session.flush()
        return offer

    async def delete_offer(self, offer_id: str) -> bool:
        offer = await self.get_offer(offer_id)
        if offer is None:
            return False
        await self.session.execute(
            delete(OfferRateVersion).where(OfferRateVersion.offer_id == self._coerce_uuid(offer_id))
        )
        await self.session.execute(
            delete(EntityOfferBinding).where(
                EntityOfferBinding.offer_id == self._coerce_uuid(offer_id)
            )
        )
        await self.session.delete(offer)
        await self.session.flush()
        return True

    async def add_rate_version(
        self,
        offer_id: str,
        cpa_usd: Decimal,
        effective_from: datetime,
        effective_to: datetime | None = None,
        note: str | None = None,
    ) -> OfferRateVersion:
        rate_version = OfferRateVersion(
            offer_id=self._coerce_uuid(offer_id),
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
            .where(OfferRateVersion.offer_id == self._coerce_uuid(offer_id))
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
                OfferRateVersion.offer_id == self._coerce_uuid(offer_id),
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
            offer_id=self._coerce_uuid(offer_id),
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
        adset_scope_key: str | None,
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

        if adset_scope_key is not None:
            result = await self.session.scalars(
                select(EntityOfferBinding)
                .where(
                    EntityOfferBinding.entity_type == EntityType.ADSET,
                    EntityOfferBinding.entity_id == adset_scope_key,
                    EntityOfferBinding.is_active.is_(True),
                )
                .order_by(EntityOfferBinding.priority.desc())
            )
            return result.first()

        return None

    async def resolve_offer_by_ad_name(
        self,
        ad_name: str | None,
        *,
        resolved_offer_code: str | None = None,
    ) -> Offer | None:
        candidate = (resolved_offer_code or "").strip() or extract_offer_code_from_ad_name(ad_name)
        if candidate is None:
            return None

        offers = await self.list_offers(active_only=True)
        for offer in offers:
            if offer.code == candidate or offer.name == candidate:
                return offer

        normalized_candidate = normalize_offer_lookup_key(candidate)
        if not normalized_candidate:
            return None

        for offer in offers:
            if normalize_offer_lookup_key(offer.code) == normalized_candidate:
                return offer
            if normalize_offer_lookup_key(offer.name) == normalized_candidate:
                return offer
        return None

    async def resolve_offer_for_ad(
        self,
        *,
        ad_name: str | None,
        resolved_offer_code: str | None = None,
        ad_id: str | None = None,
        adset_scope_key: str | None = None,
    ) -> Offer | None:
        """Ищет оффер по неймингу объявления и только затем по legacy-привязкам."""

        offer = await self.resolve_offer_by_ad_name(
            ad_name,
            resolved_offer_code=resolved_offer_code,
        )
        if offer is not None:
            return offer

        binding = await self.resolve_binding(ad_id, adset_scope_key)
        if binding is None:
            return None
        offer = await self.get_offer(str(binding.offer_id))
        if offer is None or not offer.is_active:
            return None
        return offer
