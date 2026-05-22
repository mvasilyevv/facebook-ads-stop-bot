# -*- coding: utf-8 -*-
"""Проверяет жизненный цикл записи: begin → finish, и mark_interrupted_runs.

Используем AsyncMock — реальная БД в unit-тестах не поднимается.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_begin_creates_running_draft_and_returns_id():
    """begin_scan_run добавляет в сессию ScanRun с outcome='RUNNING' и возвращает его id."""
    from core.observer.scan_run_writer import begin_scan_run

    session = AsyncMock()
    session.flush = AsyncMock()

    # Эмулируем поведение SQLAlchemy: после flush у объекта появляется id
    captured = {}

    def fake_add(obj):
        captured["obj"] = obj

    async def fake_flush():
        captured["obj"].id = 777

    session.add = MagicMock(side_effect=fake_add)
    session.flush = AsyncMock(side_effect=fake_flush)

    run_id = await begin_scan_run(session, scan_id=42)

    assert run_id == 777
    obj = captured["obj"]
    assert obj.scan_id == 42
    assert obj.outcome == "RUNNING"
    assert obj.finished_at is None
    assert obj.started_at is not None


@pytest.mark.asyncio
async def test_finish_updates_outcome_and_fields():
    """finish_scan_run выполняет UPDATE со всеми полями."""
    from core.observer.scan_run_writer import finish_scan_run

    session = AsyncMock()
    session.execute = AsyncMock()

    await finish_scan_run(
        session,
        run_id=123,
        outcome="OK",
        rows_total=58,
        rows_partial=0,
        rows_with_data=47,
        alerts_warning=1,
        alerts_stop=0,
        phase_timings={"refresh_ms": 200, "total_ms": 6400},
        warnings=[],
        empty_reason=None,
        error_kind=None,
        error_message=None,
        threat_level="MEDIUM",
        next_interval_s=45,
    )

    assert session.execute.await_count == 1
    # Проверяем, что выполнен update statement
    call_args = session.execute.await_args[0][0]
    # Это объект Update — у него есть метод _values_to_compile_state или просто atрибут
    # Достаточно проверить, что это не select.
    assert "UPDATE" in str(call_args).upper()


@pytest.mark.asyncio
async def test_mark_interrupted_marks_stale_running_rows():
    """mark_interrupted_runs возвращает rowcount от update."""
    from core.observer.scan_run_writer import mark_interrupted_runs

    session = AsyncMock()
    result_mock = MagicMock()
    result_mock.rowcount = 3
    session.execute = AsyncMock(return_value=result_mock)

    cutoff = datetime.now(UTC) - timedelta(minutes=5)
    marked = await mark_interrupted_runs(session, older_than=cutoff)

    assert marked == 3
    assert session.execute.await_count == 1
