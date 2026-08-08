# -*- coding: utf-8 -*-
"""Трекинг каждого scan-цикла observer. PARTITIONED BY month."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from core.models.base import Base


class ScanRun(Base):
    """Запись о цикле сканирования observer.

    PARTITIONED BY RANGE (started_at). Retention 30 дней.
    scan_id — монотонный ID из PostgreSQL sequence, создаётся в одной
    транзакции с scan_runs и связан с started_at через UNIQUE.
    """

    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True, nullable=False)
    scan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)  # success/error/timeout
    rows_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alerts_warning: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alerts_stop: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Явный числовой ID кабинета (без act_). Scan без identity не создаётся.
    ad_account_id: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("id", "started_at", name="pk_scan_runs"),
        UniqueConstraint("scan_id", "started_at", name="uq_scan_runs_scan_id"),
        CheckConstraint(
            "ad_account_id ~ '^[0-9]+$'",
            name=conv("ck_scan_runs_ad_account_identity"),
        ),
        Index("ix_scan_runs_scan_id", "scan_id"),
        Index("ix_scan_runs_started", "started_at"),
        {"postgresql_partition_by": "RANGE (started_at)"},
    )
