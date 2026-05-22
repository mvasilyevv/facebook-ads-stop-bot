# -*- coding: utf-8 -*-
"""Проверяет classify_scan_outcome для всех 7 исходов цикла observer'а."""

from clients.python_grpc.client import ScanResult
from core.observer.outcome_classifier import ScanOutcome, classify_scan_outcome


def _make_result(rows=None, **overrides) -> ScanResult:
    base = {
        "rows": rows if rows is not None else [],
        "total_passes": 1,
        "duration_seconds": 1.0,
        "empty_reason": None,
        "rows_with_all_metrics_empty": 0,
        "partial_row_ids": [],
        "warnings": [],
    }
    base.update(overrides)
    return ScanResult(**base)


class _MockRow:
    """Минимальный имитатор ScannedAdRow с fb_ad_id."""

    def __init__(self, fb_ad_id: str):
        self.fb_ad_id = fb_ad_id


# Сценарий: всё ок — есть строки, partial нет, метрики не пустые
def test_ok_when_rows_present_and_no_partial():
    rows = [_MockRow("ad1"), _MockRow("ad2")]
    result = _make_result(rows=rows, rows_with_all_metrics_empty=0)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.OK


# Сценарий: есть строки но какие-то partial → OK_PARTIAL
def test_ok_partial_when_some_partial_rows():
    rows = [_MockRow("ad1"), _MockRow("ad2")]
    result = _make_result(rows=rows, partial_row_ids=["ad1"])
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.OK_PARTIAL
    assert outcome.partial_count == 1


# Сценарий: 0 строк, browser-agent сказал no_active_ads — это норма
def test_empty_ok_when_no_active_ads():
    result = _make_result(empty_reason="no_active_ads")
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.EMPTY_OK
    assert outcome.empty_reason == "no_active_ads"


# Сценарий: 0 строк, фильтр исключает всё
def test_empty_ok_when_filter_excludes_all():
    result = _make_result(empty_reason="filter_excludes_all")
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.EMPTY_OK


# Сценарий: 0 строк, browser-agent не увидел таблицу
def test_empty_bad_when_table_not_found():
    result = _make_result(empty_reason="table_not_found")
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.EMPTY_BAD


# Сценарий: 0 строк, причина неопределена — считаем аномалией
def test_empty_bad_when_empty_reason_missing():
    result = _make_result(empty_reason=None)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.EMPTY_BAD


# Сценарий: 90% строк с прочерками И у объявлений раньше были метрики → STALE_DATA
def test_stale_data_when_threshold_exceeded_and_history_exists():
    rows = [_MockRow(f"ad{i}") for i in range(10)]
    result = _make_result(rows=rows, rows_with_all_metrics_empty=9)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.STALE_DATA


# Гард: если у новых объявлений никогда не было метрик — не STALE_DATA, это норма
def test_no_stale_when_history_absent():
    rows = [_MockRow(f"ad{i}") for i in range(10)]
    result = _make_result(rows=rows, rows_with_all_metrics_empty=9)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: False
    )
    assert outcome.kind != ScanOutcome.STALE_DATA


# Меньше порога — не STALE_DATA
def test_no_stale_below_threshold():
    rows = [_MockRow(f"ad{i}") for i in range(10)]
    result = _make_result(rows=rows, rows_with_all_metrics_empty=5)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind != ScanOutcome.STALE_DATA
