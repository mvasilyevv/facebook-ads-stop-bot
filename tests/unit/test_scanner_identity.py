from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from clients.python_grpc.client import _proto_to_row
from clients.python_grpc.v1 import scanner_pb2
from core.scanner.identity import find_incomplete_scan_row_ids
from core.scanner.models import ScannedAdRow


def _complete_row() -> ScannedAdRow:
    return ScannedAdRow(
        fb_ad_id="120200000000001",
        campaign_id="120200000000002",
        adset_id="120200000000003",
        campaign_name="MV | CR2 | KE",
        adset_name="KE broad",
        ad_name="Creative 1",
        delivery_status="ACTIVE",
        spend=Decimal("1"),
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("fb_ad_id", "ad-1"),
        ("campaign_id", ""),
        ("adset_id", " 123 "),
        ("campaign_name", " "),
        ("adset_name", ""),
        ("ad_name", ""),
        ("delivery_status", "UNKNOWN"),
    ],
)
def test_python_completeness_guard_rejects_incomplete_identity(
    field_name: str,
    value: str,
) -> None:
    row = replace(_complete_row(), **{field_name: value})

    assert find_incomplete_scan_row_ids([row]) == [row.fb_ad_id.strip() or "missing_fb_ad_id:row_1"]


def test_proto_mapping_carries_canonical_adset_id_without_runtime_fallback() -> None:
    proto = scanner_pb2.ScannedAdRow(
        fb_ad_id="120200000000001",
        campaign_id="120200000000002",
        adset_id="120200000000003",
        campaign_name="MV | CR2 | KE",
        adset_name="KE broad",
        ad_name="Creative 1",
        delivery_status="ACTIVE",
        spend="1",
    )

    row = _proto_to_row(proto)

    assert isinstance(row, ScannedAdRow)
    assert row.adset_id == "120200000000003"
