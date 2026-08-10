"""Observer uses tracker registrations without Meta double counting."""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

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
