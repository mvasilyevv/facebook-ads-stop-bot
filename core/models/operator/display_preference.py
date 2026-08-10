"""Server-authoritative presentation timezone for the interactive owner."""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp


class OperatorDisplayPreference(Timestamp, Base):
    """One display preference bound to one authenticated owner recipient.

    This timezone is presentation-only. Cabinet-day boundaries and money
    evidence continue to use the independently persisted Meta cabinet timezone.
    """

    __tablename__ = "operator_display_preferences"

    owner_recipient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_recipients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False)


__all__ = ["OperatorDisplayPreference"]
