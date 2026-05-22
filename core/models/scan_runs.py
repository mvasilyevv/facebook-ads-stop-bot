# -*- coding: utf-8 -*-
"""SQLAlchemy-модель таблицы scan_runs — история циклов observer'а.

Жизненный цикл:
  1. Observer вставляет «черновик» в начале цикла (outcome='RUNNING', finished_at=NULL).
  2. По завершении делает UPDATE: outcome + все метрики.
  3. Если процесс упал — фоновая задача API через 5 мин ставит outcome='INTERRUPTED'.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from core.db.base import Base

# В Postgres-проде используем JSONB и ARRAY(TEXT). В SQLite-тестах падаем
# на универсальный JSON и JSON-массив строк через variant.
_JSONB_OR_JSON = JSONB().with_variant(JSON(), "sqlite")
_TEXT_ARRAY_OR_JSON = ARRAY(Text).with_variant(JSON(), "sqlite")


class ScanRun(Base):
    """Одна строка на цикл сканирования observer'а."""

    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    rows_total: Mapped[int | None] = mapped_column(Integer)
    rows_partial: Mapped[int | None] = mapped_column(Integer)
    rows_with_data: Mapped[int | None] = mapped_column(Integer)
    alerts_warning: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    alerts_stop: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    phase_timings: Mapped[dict[str, Any] | None] = mapped_column(_JSONB_OR_JSON)
    warnings: Mapped[list[str] | None] = mapped_column(_TEXT_ARRAY_OR_JSON)
    empty_reason: Mapped[str | None] = mapped_column(String(64))
    error_kind: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    threat_level: Mapped[str | None] = mapped_column(String(32))
    next_interval_s: Mapped[int | None] = mapped_column(Integer)
