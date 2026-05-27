# -*- coding: utf-8 -*-
"""Append-only audit-лог Marketing API. Partitioned by month."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Index, Integer, PrimaryKeyConstraint, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base, CreatedAtOnly


class MetaApiAuditLog(CreatedAtOnly, Base):
    """Audit-лог каждого HTTP-вызова к Marketing API.

    PARTITIONED BY RANGE (created_at). Retention 30 дней.
    BigSerial PK + composite (id, created_at) — append-only паттерн.
    """

    __tablename__ = "meta_api_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    http_method: Mapped[str] = mapped_column(String(8), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    ad_account_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    initiated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # created_at наследуется от CreatedAtOnly — partition key

    __table_args__ = (
        PrimaryKeyConstraint("id", "created_at"),
        Index("ix_meta_audit_created", "created_at"),
        Index("ix_meta_audit_initiated", "initiated_by", "created_at"),
        Index(
            "ix_meta_audit_errors",
            "created_at",
            postgresql_where=text("http_status >= 400"),
        ),
        Index(
            "ix_meta_audit_account",
            "ad_account_id",
            "created_at",
            postgresql_where=text("ad_account_id IS NOT NULL"),
        ),
        {"postgresql_partition_by": "RANGE (created_at)"},
    )
