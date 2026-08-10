from __future__ import annotations

import pytest

from core.meta_api.identity import graph_ad_account_id, require_ad_account_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123", "123"),
        (123, "123"),
        (" act_456 ", "456"),
        ("ACT_789", "789"),
        ("9" * 32, "9" * 32),
    ],
)
def test_require_ad_account_id_returns_one_canonical_identity(raw, expected):
    assert require_ad_account_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [None, True, "", "act_", "12a", "-1", "1.5", "9" * 33],
)
def test_require_ad_account_id_rejects_non_canonical_identity(raw):
    with pytest.raises(ValueError, match="explicit numeric account id"):
        require_ad_account_id(raw)


def test_graph_ad_account_id_uses_validated_canonical_identity():
    assert graph_ad_account_id("ACT_42") == "act_42"
