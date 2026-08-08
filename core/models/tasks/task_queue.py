# -*- coding: utf-8 -*-
"""Unified outbox table for the active operator task types."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from core.models.base import Base, BigIntPrimaryKey, Timestamp


class TaskQueue(BigIntPrimaryKey, Timestamp, Base):
    """Единая очередь задач со всеми типами.

    task_type ∈ {meta_api_mutation, observer_scan, campaign_create,
                 tracker_event_process}
    status ∈ {pending, running, succeeded, failed, retrying, cancelled}

    Retention:
    - succeeded + completed_at < NOW() - 30d → DELETE (cleanup_worker)
    - failed/cancelled + completed_at < NOW() - 90d → DELETE
    """

    __tablename__ = "task_queue"

    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    # Optional Telegram initiator for operator audit correlation.
    created_by_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Atomic boundary before the first external call. A positive tracker event may
    # cancel an automatic pause only while this field is NULL.
    external_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Safety-first scheduler metadata.  PostgreSQL remains the source of truth:
    # workers claim rows with SKIP LOCKED, then every state transition is fenced
    # by ``lease_owner + lease_token``.
    lane: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_token: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        server_default=text("gen_random_uuid()"),
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_queue_idempotency_key"),
        CheckConstraint(
            "task_type IN ('meta_api_mutation', 'observer_scan', "
            "'campaign_create', 'tracker_event_process')",
            name=conv("ck_task_queue_task_type"),
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'retrying', 'cancelled')",
            name="ck_task_queue_status",
        ),
        CheckConstraint(
            "lane IN ('money', 'interactive', 'bulk', 'background')",
            name=conv("ck_task_queue_lane"),
        ),
        CheckConstraint(
            "task_type <> 'meta_api_mutation' OR "
            "COALESCE(jsonb_typeof(payload->'ad_account_id') = 'string' "
            "AND payload->>'ad_account_id' ~ '^[0-9]+$', FALSE)",
            name=conv("ck_task_queue_meta_account_identity"),
        ),
        Index(
            "ix_task_queue_runnable",
            "lane",
            text("priority DESC"),
            "available_at",
            "created_at",
            "id",
            postgresql_where=text("status IN ('pending', 'retrying')"),
        ),
        Index(
            "ix_task_queue_money_runnable",
            text("priority DESC"),
            "available_at",
            "created_at",
            "id",
            postgresql_where=text("lane = 'money' AND status IN ('pending', 'retrying')"),
        ),
        Index(
            "ix_task_queue_lease_expiry",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "ix_task_queue_running",
            "updated_at",
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "ix_task_queue_completed",
            "completed_at",
            postgresql_where=text("completed_at IS NOT NULL"),
        ),
        Index("ix_task_queue_requested_by", "requested_by", "created_at"),
        Index("ix_task_queue_payload", "payload", postgresql_using="gin"),
        Index(
            "ix_task_queue_created_by_chat",
            "created_by_chat_id",
            "status",
            postgresql_where=text("created_by_chat_id IS NOT NULL"),
        ),
    )
