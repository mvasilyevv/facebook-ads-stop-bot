# -*- coding: utf-8 -*-
"""Facebook Ad Set — второй уровень иерархии объявлений."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, Timestamp, UUIDPrimaryKey


class FbAdset(UUIDPrimaryKey, Timestamp, Base):
    """Группа объявлений Facebook.

    campaign_id — ON DELETE CASCADE: при удалении кампании все adset'ы удаляются.
    fb_adset_id — partial UNIQUE только при NOT NULL.
    UNIQUE(campaign_id, adset_name) — для upsert.
    """

    __tablename__ = "fb_adsets"
    __table_args__ = (
        UniqueConstraint("campaign_id", "adset_name", name="uq_fb_adsets_campaign_adset"),
        Index(
            "ix_fb_adsets_fb_id_unique",
            "fb_adset_id",
            unique=True,
            postgresql_where=text("fb_adset_id IS NOT NULL"),
        ),
        Index("ix_fb_adsets_campaign", "campaign_id"),
        Index(
            "ix_fb_adsets_active",
            "id",
            postgresql_where=text("is_active = true"),
        ),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("fb_campaigns.id", ondelete="CASCADE"),
        nullable=False,
    )
    fb_adset_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )
    adset_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
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

    campaign: Mapped["FbCampaign"] = relationship(  # noqa: F821
        "FbCampaign",
        back_populates="adsets",
    )
    ads: Mapped[list["FbAd"]] = relationship(  # noqa: F821
        "FbAd",
        back_populates="adset",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
