# -*- coding: utf-8 -*-
"""Интеграционные сценарии: classify_scan_outcome + scan_run_writer.

Эмулирует прохождение полного цикла observer'а: классификация исхода ScanResult,
запись в scan_runs через begin/finish с моком AsyncSession.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.python_grpc.client import ScanResult
from core.observer.outcome_classifier import ScanOutcome, classify_scan_outcome


class _MockRow:
    """Имитатор ScannedAdRow с fb_ad_id."""

    def __init__(self, fb_ad_id: str):
        self.fb_ad_id = fb_ad_id


@pytest.mark.asyncio
async def test_full_ok_path_records_scan_run():
    """Полный успешный цикл: classify → OK → begin → finish."""
    from core.observer.scan_run_writer import begin_scan_run, finish_scan_run

    # Готовим ScanResult с 2 строками без partial и без stale
    rows = [_MockRow("ad1"), _MockRow("ad2")]
    result = ScanResult(rows=rows, total_passes=1, duration_seconds=5.5)

    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.OK

    # Эмулируем сессию: begin создаёт draft с id=42, finish делает UPDATE
    session = AsyncMock()
    captured = {}

    def fake_add(obj):
        captured["draft"] = obj

    async def fake_flush():
        captured["draft"].id = 42

    session.add = MagicMock(side_effect=fake_add)
    session.flush = AsyncMock(side_effect=fake_flush)
    session.execute = AsyncMock()

    run_id = await begin_scan_run(session, scan_id=100)
    assert run_id == 42

    await finish_scan_run(
        session,
        run_id=run_id,
        outcome=outcome.kind.value,
        rows_total=len(rows),
        rows_with_data=len(rows),
        threat_level="LOW",
        next_interval_s=60,
    )
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_stale_data_path_records_with_error_kind():
    """STALE_DATA: классификатор ловит → finish_scan_run пишет error_kind=stale_data."""
    from core.observer.scan_run_writer import begin_scan_run, finish_scan_run

    rows = [_MockRow(f"ad{i}") for i in range(10)]
    result = ScanResult(
        rows=rows,
        total_passes=1,
        duration_seconds=8.0,
        rows_with_all_metrics_empty=9,  # 90% строк пустые
    )

    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.STALE_DATA
    assert outcome.note  # должна быть нота "9/10 строк без метрик"

    session = AsyncMock()
    captured = {}

    def fake_add(obj):
        captured["draft"] = obj

    async def fake_flush():
        captured["draft"].id = 99

    session.add = MagicMock(side_effect=fake_add)
    session.flush = AsyncMock(side_effect=fake_flush)
    session.execute = AsyncMock()

    run_id = await begin_scan_run(session, scan_id=101)
    await finish_scan_run(
        session,
        run_id=run_id,
        outcome=outcome.kind.value,
        rows_total=len(rows),
        rows_with_data=1,
        error_kind="stale_data",
        error_message=outcome.note,
    )

    assert run_id == 99
    assert session.execute.await_count == 1


@pytest.mark.asyncio
async def test_empty_bad_path():
    """0 строк без empty_reason → EMPTY_BAD → finish с empty_reason=table_not_found."""
    result = ScanResult(rows=[], total_passes=0, duration_seconds=0.5, empty_reason=None)
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: False
    )
    assert outcome.kind == ScanOutcome.EMPTY_BAD
    assert outcome.empty_reason == "table_not_found"


@pytest.mark.asyncio
async def test_empty_ok_no_active_ads():
    """0 строк + empty_reason=no_active_ads → EMPTY_OK."""
    result = ScanResult(rows=[], total_passes=0, duration_seconds=0.3, empty_reason="no_active_ads")
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: False
    )
    assert outcome.kind == ScanOutcome.EMPTY_OK
    assert outcome.empty_reason == "no_active_ads"


@pytest.mark.asyncio
async def test_ok_partial_when_rows_have_partial_ids():
    """Есть partial_row_ids → OK_PARTIAL с правильным partial_count."""
    rows = [_MockRow("ad1"), _MockRow("ad2"), _MockRow("ad3")]
    result = ScanResult(
        rows=rows,
        total_passes=1,
        duration_seconds=4.0,
        partial_row_ids=["ad1", "ad3"],
    )
    outcome = classify_scan_outcome(
        result, stale_threshold=0.9, has_history_for_ids=lambda ids: True
    )
    assert outcome.kind == ScanOutcome.OK_PARTIAL
    assert outcome.partial_count == 2
