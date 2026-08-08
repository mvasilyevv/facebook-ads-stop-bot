"""Durable PostgreSQL authority for owner panel authentication."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class PanelOidcAttempt(Base):
    __tablename__ = "panel_oidc_attempts"
    __table_args__ = (
        CheckConstraint("octet_length(state_digest) = 32", name="state_digest_sha256"),
        CheckConstraint("expires_at > created_at", name="valid_expiry"),
        Index("ix_panel_oidc_attempts_expires_at", "expires_at"),
    )

    state_digest: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    return_to: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PanelLoginTicket(Base):
    __tablename__ = "panel_login_tickets"
    __table_args__ = (
        CheckConstraint("octet_length(ticket_digest) = 32", name="ticket_digest_sha256"),
        CheckConstraint("telegram_user_id > 0", name="positive_telegram_user_id"),
        CheckConstraint("expires_at > issued_at", name="valid_expiry"),
        Index("ix_panel_login_tickets_expires_at", "expires_at"),
    )

    ticket_digest: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    return_to: Mapped[str] = mapped_column(String(2048), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PanelSessionRecord(Base):
    __tablename__ = "panel_sessions"
    __table_args__ = (
        CheckConstraint("octet_length(token_digest) = 32", name="token_digest_sha256"),
        CheckConstraint("telegram_user_id > 0", name="positive_telegram_user_id"),
        CheckConstraint("role = 'owner'", name="owner_role"),
        CheckConstraint("expires_at > issued_at", name="valid_expiry"),
        Index("ix_panel_sessions_expires_at", "expires_at"),
    )

    token_digest: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["PanelLoginTicket", "PanelOidcAttempt", "PanelSessionRecord"]
