# -*- coding: utf-8 -*-
"""Проверяет новые поля dataclass ScanResult: phase_timings, partial_row_ids,
warnings, empty_reason, rows_with_all_metrics_empty."""

from clients.python_grpc.client import ScanResult


def test_scan_result_default_new_fields():
    """По умолчанию новые поля заполнены пустыми коллекциями / None / 0."""
    result = ScanResult(rows=[], total_passes=0, duration_seconds=0.0)
    assert result.phase_timings == {}
    assert result.partial_row_ids == []
    assert result.warnings == []
    assert result.empty_reason is None
    assert result.rows_with_all_metrics_empty == 0


def test_scan_result_explicit_new_fields():
    """Все новые поля корректно сохраняются."""
    result = ScanResult(
        rows=[],
        total_passes=1,
        duration_seconds=2.5,
        phase_timings={"refresh_ms": 200, "total_ms": 5000},
        partial_row_ids=["1", "2"],
        warnings=["loader_visible_long"],
        empty_reason="no_active_ads",
        rows_with_all_metrics_empty=5,
    )
    assert result.phase_timings["refresh_ms"] == 200
    assert result.partial_row_ids == ["1", "2"]
    assert result.empty_reason == "no_active_ads"
    assert result.rows_with_all_metrics_empty == 5
