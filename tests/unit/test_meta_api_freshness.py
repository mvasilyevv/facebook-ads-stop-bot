"""Freshness boundary for automatic Meta pause decisions."""

from datetime import UTC, datetime, timedelta

from core.meta_api.freshness import snapshot_is_fresh


def test_snapshot_is_fresh_through_exactly_two_scan_intervals() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    assert snapshot_is_fresh(
        latest_cycle_at=now - timedelta(seconds=180),
        interval_seconds=90,
        now=now,
    )
    assert not snapshot_is_fresh(
        latest_cycle_at=now - timedelta(seconds=181),
        interval_seconds=90,
        now=now,
    )
    assert not snapshot_is_fresh(latest_cycle_at=None, interval_seconds=90, now=now)
