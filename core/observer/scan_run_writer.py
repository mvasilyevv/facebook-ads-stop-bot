# -*- coding: utf-8 -*-
"""Запись/обновление строк scan_runs.

API:
    - begin_scan_run(session, scan_id) → run_id (создаёт черновик outcome='RUNNING')
    - finish_scan_run(session, run_id, outcome, **fields) (UPDATE с финальными данными)
    - mark_interrupted_runs(session, older_than) (помечает зависшие черновики)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import ScanRun


async def begin_scan_run(session: AsyncSession, *, scan_id: int) -> int:
    """Создать «черновик» цикла. Возвращает id вставленной записи."""
    run = ScanRun(
        scan_id=scan_id,
        started_at=datetime.now(UTC),
        outcome="RUNNING",
    )
    session.add(run)
    await session.flush()
    return run.id


async def finish_scan_run(
    session: AsyncSession,
    *,
    run_id: int,
    outcome: str,
    rows_total: int | None = None,
    rows_partial: int | None = None,
    rows_with_data: int | None = None,
    alerts_warning: int = 0,
    alerts_stop: int = 0,
    phase_timings: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    empty_reason: str | None = None,
    error_kind: str | None = None,
    error_message: str | None = None,
    threat_level: str | None = None,
    next_interval_s: int | None = None,
) -> None:
    """Завершить цикл — UPDATE со всеми финальными полями."""
    await session.execute(
        update(ScanRun)
        .where(ScanRun.id == run_id)
        .values(
            outcome=outcome,
            finished_at=datetime.now(UTC),
            rows_total=rows_total,
            rows_partial=rows_partial,
            rows_with_data=rows_with_data,
            alerts_warning=alerts_warning,
            alerts_stop=alerts_stop,
            phase_timings=phase_timings,
            warnings=warnings,
            empty_reason=empty_reason,
            error_kind=error_kind,
            error_message=error_message,
            threat_level=threat_level,
            next_interval_s=next_interval_s,
        )
    )


async def mark_interrupted_runs(session: AsyncSession, *, older_than: datetime) -> int:
    """Пометить RUNNING-черновики со started_at < older_than как INTERRUPTED.

    Возвращает количество обновлённых строк (rowcount).
    """
    result = await session.execute(
        update(ScanRun)
        .where(ScanRun.outcome == "RUNNING", ScanRun.started_at < older_than)
        .values(outcome="INTERRUPTED", finished_at=datetime.now(UTC))
    )
    return result.rowcount or 0
