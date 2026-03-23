from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.db.base import Base
from core.domain import DecisionType, DeliveryStatus, RiskBand, ScopePresence, TrackingMode
from core.models.base_mixins import TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from core.models.offers import EntityOfferBinding
    from core.models.operations import Decision, ScanRun


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    scope_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    fb_campaign_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    tracking_mode: Mapped[TrackingMode] = mapped_column(
        Enum(TrackingMode, name="tracking_mode_enum"),
        default=TrackingMode.TRACKED,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    adsets: Mapped[list["AdSet"]] = relationship(back_populates="campaign")


class AdSet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "adsets"

    scope_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    fb_adset_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    tracking_mode: Mapped[TrackingMode] = mapped_column(
        Enum(TrackingMode, name="tracking_mode_enum"),
        default=TrackingMode.TRACKED,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[Campaign] = relationship(back_populates="adsets")
    ads: Mapped[list["Ad"]] = relationship(back_populates="adset")
    offer_bindings: Mapped[list["EntityOfferBinding"]] = relationship(back_populates="adset")


class Ad(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ads"
    __table_args__ = (
        Index("ix_ads_last_seen_at", "last_seen_at"),
        Index("ix_ads_last_scan_run_id", "last_scan_run_id"),
        Index("ix_ads_risk_band_last_risk_at", "risk_band", "last_risk_at"),
    )

    fb_ad_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    adset_id: Mapped[str] = mapped_column(ForeignKey("adsets.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status_enum"),
        default=DeliveryStatus.UNKNOWN,
    )
    tracking_mode: Mapped[TrackingMode] = mapped_column(
        Enum(TrackingMode, name="tracking_mode_enum"),
        default=TrackingMode.TRACKED,
        index=True,
    )
    scope_presence: Mapped[ScopePresence] = mapped_column(
        Enum(ScopePresence, name="scope_presence_enum"),
        default=ScopePresence.NOT_SEEN_THIS_SCAN,
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_action_source: Mapped[str | None] = mapped_column(String(64))
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_decision: Mapped[DecisionType] = mapped_column(
        Enum(DecisionType, name="decision_type_enum"),
        default=DecisionType.NO_ACTION,
    )
    risk_band: Mapped[RiskBand] = mapped_column(
        Enum(RiskBand, name="risk_band_enum"),
        default=RiskBand.SAFE,
        index=True,
    )
    last_risk_reason: Mapped[str | None] = mapped_column(String(500))
    last_risk_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scan_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("scan_runs.id", ondelete="SET NULL")
    )

    adset: Mapped[AdSet] = relationship(back_populates="ads")
    campaign: Mapped[Campaign] = relationship()
    metric_snapshots: Mapped[list["MetricSnapshot"]] = relationship(back_populates="ad")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="ad")
    offer_bindings: Mapped[list["EntityOfferBinding"]] = relationship(back_populates="ad")


class MetricSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (
        Index("ix_metric_snapshots_scan_run_id", "scan_run_id"),
        Index("ix_metric_snapshots_fb_ad_id", "fb_ad_id"),
    )

    fb_ad_id: Mapped[str] = mapped_column(String(64))
    ad_id: Mapped[str | None] = mapped_column(ForeignKey("ads.id", ondelete="SET NULL"))
    scan_run_id: Mapped[str] = mapped_column(ForeignKey("scan_runs.id", ondelete="CASCADE"))
    offer_id: Mapped[str | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"))
    offer_rate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("offer_rate_versions.id", ondelete="SET NULL")
    )
    resolved_cpa_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    spend: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    clicks: Mapped[int | None]
    cpc: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    leads: Mapped[int | None]
    cost_per_lead: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    registrations: Mapped[int | None]
    cost_per_registration: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    deposits: Mapped[int | None]
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    ad: Mapped[Ad | None] = relationship(back_populates="metric_snapshots")
    scan_run: Mapped["ScanRun"] = relationship(back_populates="metric_snapshots")
