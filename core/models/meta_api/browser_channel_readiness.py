"""Short-lived PostgreSQL evidence for browser-channel scheduling readiness."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from core.models.base import Base, Timestamp


class BrowserChannelReadiness(Timestamp, Base):
    """Bounded scheduling evidence for the canonical Meta browser channel.

    This row is not operation authority.  Every controlled RPC still proves the
    exact live contract/profile/session and consumes a one-shot capability.
    """

    __tablename__ = "browser_channel_readiness"

    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    vision_config_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("vision_config.id", ondelete="CASCADE"),
        nullable=False,
    )
    vision_config_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    expected_profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_profile_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    observed_contract_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
    readiness_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    writer_instance: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )
    last_ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "channel = 'meta_api'",
            name=conv("ck_browser_channel_readiness_channel"),
        ),
        CheckConstraint(
            "state IN ('ready', 'unavailable', 'incompatible', 'profile_mismatch', 'maintenance')",
            name=conv("ck_browser_channel_readiness_state"),
        ),
        CheckConstraint(
            "("
            "state = 'ready' "
            "AND observed_contract_version = 5 "
            "AND observed_profile_id = expected_profile_id "
            "AND observed_session_id IS NOT NULL "
            "AND length(observed_session_id) > 0 "
            "AND readiness_expires_at IS NOT NULL "
            "AND readiness_expires_at > observed_at"
            ") OR ("
            "state <> 'ready' "
            "AND readiness_expires_at IS NULL"
            ")",
            name=conv("ck_browser_channel_readiness_evidence"),
        ),
    )


__all__ = ["BrowserChannelReadiness"]
