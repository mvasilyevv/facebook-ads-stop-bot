# -*- coding: utf-8 -*-
"""Facebook-кампания — первый уровень иерархии объявлений."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class FbCampaign(UUIDPrimaryKey, Timestamp, Base):
    """Кампания Facebook.

    offer_id — nullable, ON DELETE SET NULL: если оффер удалён, кампания остаётся unmatched.
    fb_campaign_id — Meta numeric ID, уникален только среди NOT NULL (partial UNIQUE).
    UNIQUE(campaign_name) — для upsert в observer/snapshot_writer.
    """

    __tablename__ = "fb_campaigns"
    __table_args__ = (
        UniqueConstraint("campaign_name", name="uq_fb_campaigns_campaign_name"),
        Index(
            "ix_fb_campaigns_fb_id_unique",
            "fb_campaign_id",
            unique=True,
            postgresql_where=text("fb_campaign_id IS NOT NULL"),
        ),
        Index(
            "ix_fb_campaigns_offer",
            "offer_id",
            postgresql_where=text("offer_id IS NOT NULL"),
        ),
        Index(
            "ix_fb_campaigns_active",
            "id",
            postgresql_where=text("is_active = true"),
        ),
    )

    fb_campaign_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    campaign_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    offer_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("offers.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    adsets: Mapped[list["FbAdset"]] = relationship(  # noqa: F821
        "FbAdset",
        back_populates="campaign",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
