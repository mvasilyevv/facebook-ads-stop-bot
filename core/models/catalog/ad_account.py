# -*- coding: utf-8 -*-
"""Canonical advertising-account catalog and offer membership relation."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.schema import conv

from core.models.base import Base, Timestamp


class AdAccount(Timestamp, Base):
    """A canonical Meta advertising-account identity.

    ``account_id`` is the immutable external identity.  Account rows are not
    owned by offers and may intentionally remain unlinked for future campaign
    setup.
    """

    __tablename__ = "ad_accounts"
    __table_args__ = (
        CheckConstraint(
            "account_id ~ '^[0-9]{1,32}$'",
            name=conv("ck_ad_accounts_account_id"),
        ),
    )

    account_id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
    )

    offer_links: Mapped[list["OfferAdAccount"]] = relationship(
        "OfferAdAccount",
        back_populates="account",
        passive_deletes=True,
    )


class OfferAdAccount(Base):
    """Many-to-many membership between an offer and an ad account."""

    __tablename__ = "offer_ad_accounts"
    __table_args__ = (
        # The PK starts with offer_id and covers per-offer listing/replacement.
        # This reverse index serves account ownership/FK lookup paths.
        Index("ix_offer_ad_accounts_account_offer", "account_id", "offer_id"),
    )

    offer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("ad_accounts.account_id", ondelete="RESTRICT"),
        primary_key=True,
    )

    offer: Mapped["Offer"] = relationship(  # noqa: F821
        "Offer",
        back_populates="ad_account_links",
    )
    account: Mapped[AdAccount] = relationship(
        "AdAccount",
        back_populates="offer_links",
    )


__all__ = ["AdAccount", "OfferAdAccount"]
