from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.writers import upsert_catalog_hierarchy


class _EngineThatMustNotOpen:
    def begin(self):
        raise AssertionError("catalog validation must happen before a DB transaction")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "invalid_id"),
    [
        ("fb_ad_id", None),
        ("fb_ad_id", "ad-123"),
        ("fb_adset_id", ""),
        ("fb_adset_id", " 456 "),
        ("fb_campaign_id", None),
        ("fb_campaign_id", "campaign-789"),
    ],
)
async def test_catalog_writer_rejects_noncanonical_meta_ids_before_db(
    field_name: str,
    invalid_id: str | None,
) -> None:
    engine = cast(AsyncEngine, cast(Any, _EngineThatMustNotOpen()))
    ids: dict[str, str | None] = {
        "fb_ad_id": "120200000000001",
        "fb_adset_id": "120200000000002",
        "fb_campaign_id": "120200000000003",
    }
    ids[field_name] = invalid_id

    with pytest.raises(ValueError, match=field_name):
        await upsert_catalog_hierarchy(
            engine,
            fb_ad_id=ids["fb_ad_id"],  # type: ignore[arg-type]
            ad_name="Creative 1",
            fb_adset_id=ids["fb_adset_id"],  # type: ignore[arg-type]
            adset_name="KE broad",
            fb_campaign_id=ids["fb_campaign_id"],  # type: ignore[arg-type]
            campaign_name="MV | CR2 | KE",
            offer_id=None,
            delivery_status="ACTIVE",
            ad_account_id="123",
        )
