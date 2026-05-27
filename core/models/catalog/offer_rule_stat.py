# -*- coding: utf-8 -*-
"""ML-confidence для пары (offer × rule_code)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, UUIDPrimaryKey


class OfferRuleStat(UUIDPrimaryKey, Base):
    """Статистика уверенности правила для оффера.

    UNIQUE(offer_id, rule_code) — один stat per пара.
    Не имеет updated_at — перезаписывается INSERT ... ON CONFLICT DO UPDATE.
    """

    __tablename__ = "offer_rule_stats"
    __table_args__ = (
        UniqueConstraint("offer_id", "rule_code", name="uq_offer_rule_stats_offer_rule"),
    )

    offer_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_code: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(5, 4),
        nullable=False,
    )
    sample_size: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    last_computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    offer: Mapped["Offer"] = relationship(  # noqa: F821
        "Offer",
        back_populates="rule_stats",
    )
