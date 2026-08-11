# -*- coding: utf-8 -*-
"""Immutable receipt for the one-time configuration adoption."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class AdoptionReceipt(Base):
    """Database-authoritative proof that one reviewed bundle was committed."""

    __tablename__ = "adoption_receipt"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint(
            "schema_version = 'adoption-bundle/v1'",
            name="schema_version",
        ),
        CheckConstraint(
            "bundle_sha256 ~ '^[0-9a-f]{64}$'",
            name="bundle_sha256",
        ),
        CheckConstraint(
            "source_fingerprint ~ '^[0-9a-f]{64}$'",
            name="source_fingerprint",
        ),
        CheckConstraint(
            "jsonb_typeof(entity_counts) = 'object'",
            name="entity_counts_object",
        ),
        CheckConstraint(
            "jsonb_typeof(section_sha256) = 'object'",
            name="section_sha256_object",
        ),
    )

    id: Mapped[int] = mapped_column(
        SmallInteger,
        primary_key=True,
        server_default=text("1"),
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_counts: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    section_sha256: Mapped[dict[str, str]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


__all__ = ["AdoptionReceipt"]
