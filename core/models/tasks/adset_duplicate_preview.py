"""Durable, principal-bound capability for one ad-set duplicate plan."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class AdsetDuplicatePreview(Base):
    """Short-lived PostgreSQL authority for launching a reviewed bulk plan."""

    __tablename__ = "adset_duplicate_previews"

    token_digest: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    principal: Mapped[str] = mapped_column(String(64), nullable=False)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    task_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    plan_digest: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("task_queue.id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "octet_length(token_digest) = 32",
            name="token_digest_sha256",
        ),
        CheckConstraint(
            "octet_length(plan_digest) = 32",
            name="plan_digest_sha256",
        ),
        CheckConstraint(
            "char_length(principal) BETWEEN 1 AND 64",
            name="principal_length",
        ),
        CheckConstraint(
            "idempotency_key ~ '^meta:duplicate-adset:[0-9a-f]{64}$'",
            name="idempotency_key_format",
        ),
        CheckConstraint(
            "jsonb_typeof(preview) = 'object'",
            name="preview_object",
        ),
        CheckConstraint(
            "jsonb_typeof(task_payload) = 'object'",
            name="task_payload_object",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="valid_expiry",
        ),
        CheckConstraint(
            "(task_id IS NULL AND consumed_at IS NULL) "
            "OR (task_id IS NOT NULL AND consumed_at IS NOT NULL)",
            name="consumption_coherent",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= created_at",
            name="valid_consumed_at",
        ),
        Index(
            "ix_adset_duplicate_previews_expires_at",
            "expires_at",
            postgresql_where=text("task_id IS NULL"),
        ),
        Index(
            "ix_adset_duplicate_previews_task_id",
            "task_id",
            postgresql_where=text("task_id IS NOT NULL"),
        ),
    )


__all__ = ["AdsetDuplicatePreview"]
