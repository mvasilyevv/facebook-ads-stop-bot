from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.domain import EntityType
from core.models.base_mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Offer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "offers"

    code: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)

    rate_versions: Mapped[list["OfferRateVersion"]] = relationship(back_populates="offer")
    bindings: Mapped[list["EntityOfferBinding"]] = relationship(back_populates="offer")


class OfferRateVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "offer_rate_versions"

    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"))
    cpa_usd: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(String(255))

    offer: Mapped[Offer] = relationship(back_populates="rate_versions")


class EntityOfferBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "entity_offer_bindings"

    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType, name="entity_type_enum"))
    entity_id: Mapped[str] = mapped_column(String(255), index=True)
    offer_id: Mapped[str] = mapped_column(ForeignKey("offers.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)

    offer: Mapped[Offer] = relationship(back_populates="bindings")
    adset_id: Mapped[str | None] = mapped_column(ForeignKey("adsets.id", ondelete="CASCADE"))
    ad_id: Mapped[str | None] = mapped_column(ForeignKey("ads.id", ondelete="CASCADE"))
    adset = relationship("AdSet", back_populates="offer_bindings")
    ad = relationship("Ad", back_populates="offer_bindings")
