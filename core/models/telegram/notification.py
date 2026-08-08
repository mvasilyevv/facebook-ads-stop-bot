# -*- coding: utf-8 -*-
"""Durable incident, notification outbox and Telegram inbox models.

PostgreSQL is the source of truth for notification intent and delivery state.
Redis or ``LISTEN/NOTIFY`` may wake workers, but neither is required for
correctness: workers always drain these tables with ``SKIP LOCKED``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from core.models.base import Base, BigIntPrimaryKey, CreatedAtOnly, Timestamp, UUIDPrimaryKey


class Incident(UUIDPrimaryKey, Timestamp, Base):
    """Correlated operator incident with a single active generation."""

    __tablename__ = "incidents"

    incident_key: Mapped[str] = mapped_column(String(160), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(160), nullable=False)
    ad_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(700), nullable=True)
    facts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()")
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "severity IN ('ok','warning','critical','unknown')",
            name="incident_severity",
        ),
        CheckConstraint(
            "status IN ('open','acknowledged','executing','resolved','failed')",
            name="incident_status",
        ),
        CheckConstraint("generation > 0", name="incident_generation_positive"),
        Index(
            "uq_incidents_active_key",
            "incident_key",
            unique=True,
            postgresql_where=text("status IN ('open','acknowledged','executing')"),
        ),
        Index("ix_incidents_resource", "resource_type", "resource_id"),
        Index("ix_incidents_correlation", "correlation_id"),
        Index(
            "ix_incidents_active_severity",
            "severity",
            "opened_at",
            postgresql_where=text("status IN ('open','acknowledged','executing')"),
        ),
        Index(
            "ix_incidents_terminal_retention",
            "resolved_at",
            "updated_at",
            postgresql_where=text("status IN ('resolved','failed')"),
        ),
    )


class NotificationEvent(UUIDPrimaryKey, CreatedAtOnly, Base):
    """Immutable, typed notification intent created with the business write."""

    __tablename__ = "notification_events"

    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("incidents.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    audience: Mapped[str] = mapped_column(String(32), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    facts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    correlation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=text("gen_random_uuid()")
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('ok','warning','critical','unknown')",
            name="notification_event_severity",
        ),
        CheckConstraint("template_version > 0", name="notification_template_version_positive"),
        UniqueConstraint("dedupe_key", name="uq_notification_events_dedupe_key"),
        Index("ix_notification_events_incident", "incident_id", "created_at"),
        Index("ix_notification_events_correlation", "correlation_id"),
        Index("ix_notification_events_retention", "created_at"),
    )


class NotificationDelivery(BigIntPrimaryKey, Timestamp, Base):
    """One durable delivery stream per event and recipient."""

    __tablename__ = "notification_deliveries"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_recipients.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bot_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'telegram'")
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"))
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(96), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_operation_kind: Mapped[str | None] = mapped_column(String(8), nullable=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending','leased','retry','sent','dead','superseded','unknown')",
            name="notification_delivery_state",
        ),
        CheckConstraint("attempt_count >= 0", name="notification_attempt_nonnegative"),
        CheckConstraint("bot_generation > 0", name="notification_bot_generation"),
        CheckConstraint("max_attempts > 0", name="notification_max_attempts_positive"),
        CheckConstraint(
            "telegram_message_id IS NULL OR telegram_message_id > 0",
            name="notification_message_id_positive",
        ),
        CheckConstraint(
            "external_operation_kind IS NULL OR external_operation_kind IN ('send','edit')",
            name=conv("ck_notification_deliveries_external_operation_kind"),
        ),
        UniqueConstraint(
            "event_id",
            "recipient_id",
            "channel",
            name="uq_notification_delivery_event_recipient_channel",
        ),
        Index(
            "ix_notification_delivery_claim",
            "scheduled_at",
            "id",
            postgresql_where=text("state IN ('pending','retry')"),
        ),
        Index(
            "ix_notification_delivery_expired_lease",
            "lease_expires_at",
            postgresql_where=text("state = 'leased'"),
        ),
        Index("ix_notification_delivery_event", "event_id"),
        Index(
            "ix_notification_delivery_recipient_active",
            "recipient_id",
            "state",
            "id",
            postgresql_where=text("state IN ('pending','retry','leased')"),
        ),
        Index(
            "ix_notification_delivery_terminal_window",
            "completed_at",
            postgresql_where=text("state IN ('sent','dead','unknown','superseded')"),
        ),
    )


class TelegramMessageSlot(UUIDPrimaryKey, Timestamp, Base):
    """The single editable Telegram card for an incident and recipient."""

    __tablename__ = "telegram_message_slots"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_recipients.id", ondelete="CASCADE"),
        nullable=False,
    )
    last_event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_events.id", ondelete="RESTRICT"),
        nullable=False,
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    incident_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    render_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "message_id > 0",
            name=conv("ck_telegram_message_slots_message_positive"),
        ),
        CheckConstraint("incident_generation > 0", name="telegram_message_slot_generation"),
        UniqueConstraint(
            "incident_id",
            "recipient_id",
            name="uq_telegram_message_slot_incident_recipient",
        ),
        Index("ix_telegram_message_slot_recipient", "recipient_id"),
    )


class TelegramActionToken(UUIDPrimaryKey, CreatedAtOnly, Base):
    """Digest-only capability token used by ``callback_data=a:<token>``."""

    __tablename__ = "telegram_action_tokens"

    token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    delivery_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"),
        nullable=True,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_events.id", ondelete="CASCADE"),
        nullable=True,
    )
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=True,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_recipients.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    action_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(160), nullable=False)
    target_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    required_role: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'owner'")
    )
    incident_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    command_idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("task_queue.id", ondelete="SET NULL"),
        nullable=True,
    )
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("required_role IN ('owner','recipient')", name="telegram_action_role"),
        CheckConstraint(
            "incident_generation IS NULL OR incident_generation > 0",
            name="telegram_action_generation",
        ),
        UniqueConstraint("token_digest", name="uq_telegram_action_token_digest"),
        Index(
            "ix_telegram_action_active_digest",
            "token_digest",
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_telegram_action_delivery", "delivery_id", "action_key"),
        Index("ix_telegram_action_expiry", "expires_at"),
    )


class TelegramNavigationToken(UUIDPrimaryKey, CreatedAtOnly, Base):
    """One-time digest-only capability resolving a TMA destination."""

    __tablename__ = "telegram_navigation_tokens"

    token_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_recipients.id", ondelete="CASCADE"),
        nullable=False,
    )
    delivery_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("notification_deliveries.id", ondelete="CASCADE"),
        nullable=True,
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification_events.id", ondelete="CASCADE"),
        nullable=True,
    )
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(160), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "target_kind IN ('ad','action','incident')",
            name="telegram_navigation_target_kind",
        ),
        UniqueConstraint("token_digest", name="uq_telegram_navigation_token_digest"),
        Index(
            "ix_telegram_navigation_active_digest",
            "token_digest",
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        Index("ix_telegram_navigation_expiry", "expires_at"),
    )


class TelegramUpdateInbox(Base):
    """Durable inbox keyed by the bot generation and Telegram update id."""

    __tablename__ = "telegram_updates_inbox"

    bot_generation: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(96), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending','leased','retry','processed','dead')",
            name="telegram_update_inbox_state",
        ),
        CheckConstraint("bot_generation > 0", name="telegram_update_bot_generation"),
        CheckConstraint("attempt_count >= 0", name="telegram_update_attempt_nonnegative"),
        Index(
            "ix_telegram_update_inbox_claim",
            "scheduled_at",
            "bot_generation",
            "update_id",
            postgresql_where=text("state IN ('pending','retry')"),
        ),
        Index(
            "ix_telegram_update_inbox_expired_lease",
            "lease_expires_at",
            postgresql_where=text("state = 'leased'"),
        ),
        Index(
            "ix_telegram_update_terminal_retention",
            "processed_at",
            postgresql_where=text("state IN ('processed','dead')"),
        ),
    )


class TelegramCommandReply(BigIntPrimaryKey, Timestamp, Base):
    """Durable ``sendMessage`` operation emitted by a webhook command.

    The handler only appends the reply intent.  A delivery worker crosses the
    external Bot API boundary after the inbox row and this outbox row have been
    committed atomically.
    """

    __tablename__ = "telegram_command_replies"

    bot_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_text: Mapped[str] = mapped_column("text", Text, nullable=False)
    parse_mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    reply_markup: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("8"))
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_owner: Mapped[str | None] = mapped_column(String(96), nullable=True)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    external_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["bot_generation", "update_id"],
            [
                "telegram_updates_inbox.bot_generation",
                "telegram_updates_inbox.update_id",
            ],
            ondelete="CASCADE",
            name="fk_telegram_command_reply_update_generation",
        ),
        CheckConstraint("bot_generation > 0", name="telegram_command_reply_bot_generation"),
        CheckConstraint("ordinal >= 0", name="telegram_command_reply_ordinal"),
        CheckConstraint("chat_id <> 0", name="telegram_command_reply_chat"),
        CheckConstraint(
            "char_length(text) BETWEEN 1 AND 4096",
            name="telegram_command_reply_text_length",
        ),
        CheckConstraint(
            "parse_mode IS NULL OR parse_mode = 'HTML'",
            name="telegram_command_reply_parse_mode",
        ),
        CheckConstraint(
            "state IN ('pending','leased','retry','sent','dead','unknown')",
            name="telegram_command_reply_state",
        ),
        CheckConstraint("attempt_count >= 0", name="telegram_command_reply_attempts"),
        CheckConstraint("max_attempts > 0", name="telegram_command_reply_max_attempts"),
        CheckConstraint(
            "reply_to_message_id IS NULL OR reply_to_message_id > 0",
            name="telegram_command_reply_target",
        ),
        CheckConstraint(
            "telegram_message_id IS NULL OR telegram_message_id > 0",
            name="telegram_command_reply_message",
        ),
        UniqueConstraint(
            "bot_generation",
            "update_id",
            "ordinal",
            name="uq_telegram_command_reply_update_ordinal",
        ),
        Index(
            "ix_telegram_command_reply_claim",
            "scheduled_at",
            "id",
            postgresql_where=text("state IN ('pending','retry')"),
        ),
        Index(
            "ix_telegram_command_reply_expired_lease",
            "lease_expires_at",
            postgresql_where=text("state = 'leased'"),
        ),
        Index(
            "ix_telegram_command_reply_retention",
            "completed_at",
            postgresql_where=text("state IN ('sent','dead','unknown')"),
        ),
    )


class TelegramRecipientPreference(Timestamp, Base):
    """Per-recipient notification thresholds, local time and quiet hours."""

    __tablename__ = "telegram_recipient_preferences"

    recipient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("telegram_recipients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'Europe/Kaliningrad'")
    )
    min_severity: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'warning'")
    )
    quiet_hours_start: Mapped[time | None] = mapped_column(Time(timezone=False), nullable=True)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time(timezone=False), nullable=True)
    digest_local_time: Mapped[time | None] = mapped_column(Time(timezone=False), nullable=True)
    categories: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        CheckConstraint(
            "min_severity IN ('ok','warning','critical','unknown')",
            name="telegram_preference_severity",
        ),
    )


__all__ = [
    "Incident",
    "NotificationDelivery",
    "NotificationEvent",
    "TelegramActionToken",
    "TelegramCommandReply",
    "TelegramMessageSlot",
    "TelegramRecipientPreference",
    "TelegramUpdateInbox",
]
