# -*- coding: utf-8 -*-
"""Unified outbox-таблица для всех типов задач (disable/enable/plan_run/meta/ad_library)."""

from __future__ import annotations

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
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, BigIntPrimaryKey, Timestamp


class TaskQueue(BigIntPrimaryKey, Timestamp, Base):
    """Единая очередь задач со всеми типами.

    task_type ∈ {disable, enable, plan_run, meta_api_mutation, ad_library_scan}
    status ∈ {draft, pending, running, succeeded, failed, retrying, cancelled}

    Retention:
    - succeeded + completed_at < NOW() - 30d → DELETE (cleanup_worker)
    - failed/cancelled + completed_at < NOW() - 90d → DELETE
    - draft + created_at < NOW() - 24h → DELETE
    """

    __tablename__ = "task_queue"

    task_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    # TG chat_id инициатора (для owner ACL над DRAFT). NULL если задача создана через MCP/HTTP.
    created_by_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_queue_idempotency_key"),
        CheckConstraint(
            "task_type IN ('disable', 'enable', 'plan_run', 'meta_api_mutation', 'ad_library_scan')",
            name="ck_task_queue_task_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'pending', 'running', 'succeeded', 'failed', 'retrying', 'cancelled')",
            name="ck_task_queue_status",
        ),
        Index(
            "ix_task_queue_runnable",
            "task_type",
            "next_retry_at",
            postgresql_where=text("status IN ('pending', 'retrying')"),
        ),
        Index(
            "ix_task_queue_running",
            "updated_at",
            postgresql_where=text("status = 'running'"),
        ),
        Index(
            "ix_task_queue_draft",
            "created_at",
            postgresql_where=text("status = 'draft'"),
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
