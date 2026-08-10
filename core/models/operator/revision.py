"""Append-only monotonic revision events for operator reconciliation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class OperatorRevisionEvent(Base):
    __tablename__ = "operator_revision_events"
    __table_args__ = (Index("ix_operator_revision_events_created_at", "created_at"),)

    revision: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
