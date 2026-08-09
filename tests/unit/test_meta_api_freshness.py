"""Freshness boundary for automatic Meta pause decisions."""

from datetime import UTC, datetime, timedelta

from core.meta_api.freshness import snapshot_is_fresh


def test_snapshot_requires_confirmed_decision_with_absolute_sixty_second_cap() -> None:
    now = datetime(2026, 7, 14, 12, tzinfo=UTC)
    assert snapshot_is_fresh(
        latest_cycle_at=now - timedelta(seconds=60),
        decision_confirmed=True,
        now=now,
    )
    assert not snapshot_is_fresh(
        latest_cycle_at=now - timedelta(seconds=61),
        decision_confirmed=True,
        now=now,
    )
    assert not snapshot_is_fresh(
        latest_cycle_at=now + timedelta(microseconds=1),
        decision_confirmed=True,
        now=now,
    )
    assert not snapshot_is_fresh(
        latest_cycle_at=now,
        decision_confirmed=False,
        now=now,
    )
    assert not snapshot_is_fresh(
        latest_cycle_at=None,
        decision_confirmed=True,
        now=now,
    )
