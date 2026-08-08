# -*- coding: utf-8 -*-
"""Singleton-конфигурация Telegram-бота."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, SingletonMixin, Timestamp, UUIDPrimaryKey


class TelegramConfig(UUIDPrimaryKey, SingletonMixin, Timestamp, Base):
    """Единственная строка с токеном бота.

    bot_token_encrypted — Fernet-шифрование через core.crypto.
    Runtime updates и delivery state живут в durable webhook/outbox таблицах.
    """

    __tablename__ = "telegram_config"

    bot_token_encrypted: Mapped[str] = mapped_column(
        String,
        nullable=False,
    )
    bot_token_fingerprint: Mapped[bytes | None] = mapped_column(
        LargeBinary(32),
        nullable=True,
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )

    # Durable setWebhook/deleteWebhook generation. A bot-token write and the
    # corresponding desired webhook generation commit atomically.
    webhook_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    webhook_applied_generation: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    webhook_operation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    webhook_desired_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    webhook_secret_digest: Mapped[bytes | None] = mapped_column(
        LargeBinary(32),
        nullable=True,
    )
    webhook_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'unconfigured'"),
    )
    webhook_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    webhook_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("0"),
    )
    webhook_lease_owner: Mapped[str | None] = mapped_column(String(96), nullable=True)
    webhook_lease_token: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    webhook_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Last getWebhookInfo snapshot. The secret itself is never returned by
    # Telegram; webhook_secret_digest plus an explicitly successful
    # setWebhook generation is the proof recorded by webhook_state=configured.
    webhook_remote_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    webhook_remote_pending_update_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    webhook_remote_last_error_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    webhook_remote_last_error_message: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    webhook_remote_max_connections: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    webhook_remote_allowed_updates: Mapped[list[Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    webhook_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    webhook_configured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    webhook_last_error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    webhook_last_error_detail: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "bot_token_fingerprint IS NULL OR octet_length(bot_token_fingerprint) = 32",
            name="bot_token_fingerprint_sha256",
        ),
        CheckConstraint(
            "webhook_state IN ('unconfigured','pending','applying','retry','configured','failed')",
            name="webhook_state",
        ),
        CheckConstraint(
            "webhook_operation IS NULL OR webhook_operation IN ('configure','delete')",
            name="webhook_operation",
        ),
        CheckConstraint(
            "webhook_generation >= 0 AND "
            "(webhook_applied_generation IS NULL OR "
            "(webhook_applied_generation >= 0 "
            "AND webhook_applied_generation <= webhook_generation))",
            name="webhook_generation",
        ),
        CheckConstraint(
            "webhook_attempt_count >= 0",
            name="webhook_attempt_count",
        ),
        CheckConstraint(
            "webhook_secret_digest IS NULL OR octet_length(webhook_secret_digest) = 32",
            name="webhook_secret_digest_sha256",
        ),
        CheckConstraint(
            "webhook_desired_url IS NULL OR webhook_desired_url LIKE 'https://%'",
            name="webhook_desired_url_https",
        ),
        CheckConstraint(
            "webhook_remote_pending_update_count IS NULL "
            "OR webhook_remote_pending_update_count >= 0",
            name="webhook_remote_pending_nonnegative",
        ),
    )
