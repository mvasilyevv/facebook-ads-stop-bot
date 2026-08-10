# -*- coding: utf-8 -*-
"""Short-lived leases for browser operations outside ``task_queue``."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly


class BrowserOperationLease(CreatedAtOnly, Base):
    """One active direct browser operation guarded by the maintenance barrier.

    Queue-backed browser work is represented by ``task_queue.status=running``.
    This table covers the deliberately synchronous operator/platform paths
    (configuration changes, reconnect and campaign discovery) so maintenance
    can block new work and drain every existing browser user atomically.
    """

    __tablename__ = "browser_operation_leases"

    operation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    owner: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    operation_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index(
            "ix_browser_operation_leases_active",
            "lease_expires_at",
        ),
        Index(
            "ix_browser_operation_leases_owner",
            "owner",
        ),
    )
