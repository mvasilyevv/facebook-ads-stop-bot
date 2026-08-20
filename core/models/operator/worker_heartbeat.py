# -*- coding: utf-8 -*-
"""Durable per-worker liveness behind the operator snapshot (issue #176)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.models.base import Base


class WorkerHeartbeat(Base):
    """Одна строка на долгоживущий фоновый воркер.

    Читается и пишется сырым SQL (``core/worker_liveness.py``,
    ``core/operator/queries.py``): heartbeat не должен ронять рабочий цикл, и
    ORM-сессия там лишняя. Модель существует, чтобы таблица, созданная
    ревизией ``0009_worker_heartbeats``, была в ``Base.metadata`` — иначе
    ``alembic check`` видит таблицу, которой нет в метаданных, и предлагает её
    удалить.

    Две колонки по отдельности: ``last_heartbeat_at`` — процесс жив,
    ``last_poll_success_at`` — рабочий цикл завершил итерацию. Зависший цикл
    при живом процессе — ровно тот разрыв, который скрыл инцидент 18.08.
    """

    __tablename__ = "worker_heartbeats"

    worker_name: Mapped[str] = mapped_column(Text, primary_key=True)
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_poll_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["WorkerHeartbeat"]
