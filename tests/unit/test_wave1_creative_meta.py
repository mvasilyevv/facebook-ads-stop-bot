# -*- coding: utf-8 -*-
"""Scanner contract for creative preview and ad-set metadata fields."""

from __future__ import annotations

from decimal import Decimal

from core.scanner.models import ScannedAdRow


# Optional creative/display metadata defaults to empty on an identity-complete row.
def test_scanned_ad_row_wave1_defaults_empty() -> None:
    """Identity fields stay explicit while optional catalog metadata defaults empty."""
    row = ScannedAdRow(
        fb_ad_id="1",
        campaign_id="2",
        adset_id="3",
        campaign_name="C",
        adset_name="A",
        ad_name="AD",
        delivery_status="ACTIVE",
        spend=Decimal("0"),
    )
    assert row.creative_thumb_url == ""
    assert row.creative_image_url == ""
    assert row.adset_pixel_id == ""
    assert row.adset_daily_budget == ""
    assert row.adset_lifetime_budget == ""
    assert row.adset_budget_remaining == ""
    assert row.adset_learning_stage == ""


# ScannedAdRow: новые поля заполняются и доступны (для writers/pipeline).
def test_scanned_ad_row_wave1_populated() -> None:
    """Заполненные поля доходят до атрибутов dataclass (frozen)."""
    row = ScannedAdRow(
        fb_ad_id="1",
        campaign_id="2",
        adset_id="3",
        campaign_name="C",
        adset_name="A",
        ad_name="AD",
        delivery_status="ACTIVE",
        spend=Decimal("0"),
        creative_thumb_url="https://cdn/t.jpg",
        adset_pixel_id="999",
        adset_learning_stage="LEARNING_LIMITED",
    )
    assert row.creative_thumb_url == "https://cdn/t.jpg"
    assert row.adset_pixel_id == "999"
    assert row.adset_learning_stage == "LEARNING_LIMITED"
