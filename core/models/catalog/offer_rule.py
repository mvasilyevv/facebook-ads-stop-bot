# -*- coding: utf-8 -*-
"""Конфигурация 6 стоп-правил per оффер (1:1 с offers)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class OfferRule(UUIDPrimaryKey, Timestamp, Base):
    """Пороговые значения стоп-правил для оффера.

    UNIQUE(offer_id) — строго 1:1 с offers.
    Все пороги nullable — правило считается неактивным при NULL.
    """

    __tablename__ = "offer_rules"
    __table_args__ = (UniqueConstraint("offer_id", name="uq_offer_rules_offer_id"),)

    offer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    spend_no_event_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    cpa_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    cpm_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    ctr_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    frequency_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )
    funnel_ratio_threshold: Mapped[Decimal | None] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    offer: Mapped["Offer"] = relationship(  # noqa: F821
        "Offer",
        back_populates="rules",
    )
