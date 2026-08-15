import pytest

from core.scanner.status import is_delivery_activatable


@pytest.mark.parametrize("status", ["OFF", "PAUSED", " paused "])
def test_ad_level_paused_status_is_activatable(status: str) -> None:
    assert is_delivery_activatable(status)


@pytest.mark.parametrize(
    "status",
    [
        "ACTIVE",
        "WITH_ISSUES",
        "DISAPPROVED",
        "PENDING_REVIEW",
        "ADSET_PAUSED",
        "CAMPAIGN_PAUSED",
        "ARCHIVED",
        "DELETED",
        "UNKNOWN",
        None,
    ],
)
def test_non_ad_level_pause_is_not_activatable(status: str | None) -> None:
    assert not is_delivery_activatable(status)
