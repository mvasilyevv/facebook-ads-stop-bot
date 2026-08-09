"""Single data-access seam for canonical ad accounts and offer membership.

Callers own the connection transaction.  In particular, offer creation/update
and membership replacement use the same transaction so an API response can
never expose a partially written catalog configuration.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from core.meta_api.identity import require_ad_account_id
from core.models.catalog.ad_account import AdAccount, OfferAdAccount
from core.models.catalog.offer import Offer


class OfferNotFoundError(LookupError):
    """The requested offer does not exist in the catalog transaction."""


def canonical_account_ids(values: Iterable[str]) -> tuple[str, ...]:
    """Return sorted, unique, canonical account identities."""

    return tuple(sorted({require_ad_account_id(value) for value in values}))


class AdAccountCatalog:
    """Database interface for account identities and offer membership."""

    async def list_accounts(self, conn: AsyncConnection) -> list[str]:
        """List the complete global account catalog, including unlinked rows."""

        stmt = select(AdAccount.account_id).order_by(AdAccount.account_id)
        return list((await conn.scalars(stmt)).all())

    async def list_by_offer(
        self,
        conn: AsyncConnection,
        *,
        offer_ids: Iterable[uuid.UUID] | None = None,
    ) -> dict[uuid.UUID, list[str]]:
        """List account IDs grouped by offer with deterministic ordering."""

        requested = tuple(dict.fromkeys(offer_ids)) if offer_ids is not None else None
        if requested == ():
            return {}
        stmt = select(OfferAdAccount.offer_id, OfferAdAccount.account_id).order_by(
            OfferAdAccount.offer_id,
            OfferAdAccount.account_id,
        )
        if requested is not None:
            stmt = stmt.where(OfferAdAccount.offer_id.in_(requested))
        rows = (await conn.execute(stmt)).all()
        grouped: dict[uuid.UUID, list[str]] = {}
        for offer_id, account_id in rows:
            grouped.setdefault(offer_id, []).append(account_id)
        return grouped

    async def create_accounts(
        self,
        conn: AsyncConnection,
        account_ids: Iterable[str],
    ) -> tuple[str, ...]:
        """Create any missing canonical account rows without changing existing rows."""

        canonical = canonical_account_ids(account_ids)
        if canonical:
            await conn.execute(
                insert(AdAccount)
                .values([{"account_id": account_id} for account_id in canonical])
                .on_conflict_do_nothing(index_elements=[AdAccount.account_id])
            )
        return canonical

    async def replace_offer_accounts(
        self,
        conn: AsyncConnection,
        *,
        offer_id: uuid.UUID,
        account_ids: Iterable[str],
    ) -> tuple[str, ...]:
        """Atomically replace one offer's complete membership set."""

        offer_exists = await conn.scalar(select(Offer.id).where(Offer.id == offer_id))
        if offer_exists is None:
            raise OfferNotFoundError(str(offer_id))

        canonical = await self.create_accounts(conn, account_ids)
        await conn.execute(delete(OfferAdAccount).where(OfferAdAccount.offer_id == offer_id))
        if canonical:
            await conn.execute(
                insert(OfferAdAccount),
                [{"offer_id": offer_id, "account_id": account_id} for account_id in canonical],
            )
        return canonical

    async def resolve_scan_set(self, conn: AsyncConnection) -> list[str]:
        """Return the sorted union of accounts linked to active offers."""

        stmt = (
            select(OfferAdAccount.account_id)
            .join(Offer, Offer.id == OfferAdAccount.offer_id)
            .where(Offer.is_active.is_(True))
            .distinct()
            .order_by(OfferAdAccount.account_id)
        )
        return list((await conn.scalars(stmt)).all())

    async def list_active_offers_without_accounts(self, conn: AsyncConnection) -> list[str]:
        """Return active offer codes that currently have no membership rows."""

        membership = exists().where(OfferAdAccount.offer_id == Offer.id)
        stmt = select(Offer.code).where(Offer.is_active.is_(True), ~membership).order_by(Offer.code)
        return list((await conn.scalars(stmt)).all())

    async def offer_has_account(
        self,
        conn: AsyncConnection,
        *,
        offer_id: uuid.UUID,
        account_id: str,
    ) -> bool:
        """Return whether the exact canonical account belongs to the offer."""

        canonical = require_ad_account_id(account_id)
        stmt = select(
            exists().where(
                OfferAdAccount.offer_id == offer_id,
                OfferAdAccount.account_id == canonical,
            )
        )
        return bool(await conn.scalar(stmt))


ad_account_catalog = AdAccountCatalog()

__all__ = [
    "AdAccountCatalog",
    "OfferNotFoundError",
    "ad_account_catalog",
    "canonical_account_ids",
]
