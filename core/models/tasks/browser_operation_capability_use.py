"""Durable issuance and single-consume ledger for browser capabilities."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from core.models.base import Base, CreatedAtOnly


class BrowserOperationCapabilityUse(CreatedAtOnly, Base):
    """One issued capability, atomically consumed before the browser boundary."""

    __tablename__ = "browser_operation_capability_uses"

    nonce_sha256: Mapped[bytes] = mapped_column(LargeBinary(32), primary_key=True)
    capability_digest: Mapped[bytes] = mapped_column(
        LargeBinary(32),
        nullable=False,
        unique=True,
    )
    operation_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    browser_contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    caller: Mapped[str] = mapped_column(String(32), nullable=False)
    rpc: Mapped[str] = mapped_column(String(32), nullable=False)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("task_queue.id", ondelete="CASCADE"),
        nullable=False,
    )
    lease_owner: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    lease_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    vision_profile_id: Mapped[str] = mapped_column(String(128), nullable=False)
    ad_account_id: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "caller IN ('autopause', 'meta_api', 'campaign_creator')",
            name=conv("ck_browser_operation_capability_caller"),
        ),
        CheckConstraint(
            "rpc IN ('execute_graph_call', 'upload_image', 'upload_video')",
            name=conv("ck_browser_operation_capability_rpc"),
        ),
        CheckConstraint(
            "browser_contract_version = 5",
            name=conv("ck_browser_operation_capability_contract_version"),
        ),
        Index(
            "ix_browser_operation_capability_expiry",
            "expires_at",
        ),
        Index(
            "ix_browser_operation_capability_unconsumed",
            "expires_at",
            postgresql_where=text("consumed_at IS NULL"),
        ),
        Index(
            "ix_browser_operation_capability_task",
            "task_id",
            "created_at",
        ),
    )


__all__ = ["BrowserOperationCapabilityUse"]
