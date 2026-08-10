"""Single owner campaign draft persisted between web and TMA sessions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, Timestamp


class CampaignDraft(Timestamp, Base):
    """Bounded form state only; run/task/secret state is intentionally absent."""

    __tablename__ = "campaign_draft"

    singleton_key: Mapped[str] = mapped_column(
        String(16),
        primary_key=True,
        server_default=text("'owner'"),
    )
    state: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    revision: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        server_default=text("1"),
    )

    __table_args__ = (
        CheckConstraint("singleton_key = 'owner'", name="singleton_owner"),
        CheckConstraint("revision > 0", name="revision_positive"),
        CheckConstraint("jsonb_typeof(state) = 'object'", name="state_object"),
        CheckConstraint(
            "octet_length(state::text) <= 262144",
            name="state_bounded",
        ),
    )


__all__ = ["CampaignDraft"]
