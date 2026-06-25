# -*- coding: utf-8 -*-
"""ORM: per-offer счётчик кодов креативов + реестр выданных кодов.

offer_creative_seq — атомарный аллокатор сквозной нумерации OFFER_CRxxx (см.
core/campaign_builder/creative_ledger.py). campaign_creative — append-only реестр
созданных в Meta креативов (какие коды/creative_id залиты по офферу).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly, UUIDPrimaryKey


class OfferCreativeSeq(Base):
    """High-water-mark номера кода креатива по офферу. next_seq = последний выданный."""

    __tablename__ = "offer_creative_seq"

    offer_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))


class CampaignCreative(UUIDPrimaryKey, CreatedAtOnly, Base):
    """Реестр созданных креативов (append-only). UNIQUE(offer_code, code) → идемпотентность."""

    __tablename__ = "campaign_creative"
    __table_args__ = (
        UniqueConstraint("offer_code", "code", name="uq_campaign_creative_offer_code"),
    )

    offer_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    meta_creative_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
