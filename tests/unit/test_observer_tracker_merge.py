"""Observer uses tracker registrations without Meta double counting."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from core.adset_pro.queries import _query_window
from core.observer.pipeline import with_effective_tracker_registrations
from core.scanner.models import ScannedAdRow


def _row(registrations: int) -> ScannedAdRow:
    return ScannedAdRow(
        fb_ad_id="238001",
        campaign_name="campaign",
        adset_name="adset",
        ad_name="ad",
        delivery_status="Active",
        spend=Decimal("10"),
        registrations=registrations,
    )


@pytest.mark.parametrize(
    ("meta", "tracker", "expected"),
    [(0, 2, 2), (2, 0, 2), (2, 2, 2), (2, 3, 3)],
)
def test_effective_registrations_are_max_not_sum(meta: int, tracker: int, expected: int) -> None:
    source = _row(meta)
    effective = with_effective_tracker_registrations(source, tracker)
    assert effective.registrations == expected
    assert source.registrations == meta
    with pytest.raises(FrozenInstanceError):
        source.registrations = 99  # type: ignore[misc]


def test_tracker_merge_uses_explicit_half_open_cabinet_day() -> None:
    cycle_ts = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)
    cabinet_midnight = datetime(2026, 7, 14, 21, 0, tzinfo=UTC)

    start, end = _query_window(
        window=timedelta(hours=24),
        now=None,
        window_start=cabinet_midnight,
        window_end=cycle_ts,
    )

    assert start == cabinet_midnight
    assert end == cycle_ts
    assert cycle_ts - start != timedelta(hours=24)


def test_tracker_merge_retains_rolling_window_for_legacy_callers() -> None:
    now = datetime(2026, 7, 15, 10, 30, tzinfo=UTC)

    start, end = _query_window(
        window=timedelta(hours=6),
        now=now,
        window_start=None,
        window_end=None,
    )

    assert start == now - timedelta(hours=6)
    assert end == now
