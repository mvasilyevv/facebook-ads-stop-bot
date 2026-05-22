# -*- coding: utf-8 -*-
"""Проверяет, что модель ScanRun создаётся с обязательными полями
и принимает все типы данных из спецификации."""

from datetime import UTC, datetime

from core.models import ScanRun


def test_scan_run_constructor_minimal():
    """ScanRun создаётся с минимальным набором обязательных полей."""
    run = ScanRun(
        scan_id=1,
        started_at=datetime.now(UTC),
        outcome="RUNNING",
    )
    assert run.scan_id == 1
    assert run.outcome == "RUNNING"


def test_scan_run_constructor_full():
    """ScanRun принимает все поля результата."""
    run = ScanRun(
        scan_id=2,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        outcome="OK_PARTIAL",
        rows_total=58,
        rows_partial=3,
        rows_with_data=47,
        alerts_warning=2,
        alerts_stop=1,
        phase_timings={"refresh_ms": 200, "first_row_ms": 600},
        warnings=["loader_visible_long"],
        empty_reason=None,
        error_kind=None,
        error_message=None,
        threat_level="MEDIUM",
        next_interval_s=45,
    )
    assert run.rows_total == 58
    assert "loader_visible_long" in run.warnings
    assert run.phase_timings["refresh_ms"] == 200
