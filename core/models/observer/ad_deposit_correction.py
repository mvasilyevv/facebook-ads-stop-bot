# -*- coding: utf-8 -*-
"""Ручные корректировки ложных депозитов per объявление."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class AdDepositCorrection(UUIDPrimaryKey, Timestamp, Base):
    """Корректировка фейковых депозитов per ad (1:1).

    Retention: CASCADE через fb_ads.
    """

    __tablename__ = "ad_deposit_corrections"

    ad_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_ads.id", ondelete="CASCADE"),
        nullable=False,
    )
    corrected_deposits: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (UniqueConstraint("ad_id", name="uq_ad_deposit_corrections_ad"),)
