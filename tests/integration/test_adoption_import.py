from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from apps.cleanup_worker.retention import get_default_policy
from core.adoption.bundle import (
    AdoptionAccountV1,
    AdoptionObserverSettingsV1,
    AdoptionOfferRuleV1,
    AdoptionOfferV1,
    AdoptionOperatorDisplaySettingsV1,
    AdoptionRecipientV1,
    AdoptionSectionsV1,
    AdoptionSystemSettingsV1,
    build_adoption_bundle,
)
from core.adoption.service import apply_adoption_bundle


@pytest.mark.asyncio
async def test_dry_run_imports_normalized_bundle_and_rolls_back(pg_engine) -> None:
    sections = AdoptionSectionsV1(
        accounts=[AdoptionAccountV1(account_id="111")],
        offers=[
            AdoptionOfferV1(
                code="GH_CR2",
                name="Ghana",
                is_active=True,
                account_ids=["111"],
                countries=["GH"],
            )
        ],
        offer_rules=[
            AdoptionOfferRuleV1(
                offer_code="GH_CR2",
                cpa_threshold="3.00",
                currency="USD",
                frequency_threshold=None,
                stop_percent_of_rule="80",
                warning_percent_of_stop="80",
            )
        ],
        observer_settings=AdoptionObserverSettingsV1(
            interval_seconds=30,
            campaign_ids=["9001"],
        ),
        operator_display_settings=AdoptionOperatorDisplaySettingsV1(
            timezone_name="Europe/Kaliningrad",
        ),
        recipients=[
            AdoptionRecipientV1(
                chat_id=42,
                telegram_user_id=42,
                role="owner",
            )
        ],
        system_settings=AdoptionSystemSettingsV1(
            retention_policy=get_default_policy(),
            web_app_url=None,
        ),
    )
    bundle = build_adoption_bundle(
        sections,
        exported_at=datetime(2026, 8, 9, tzinfo=UTC),
        source_fingerprint="a" * 64,
    )

    result = await apply_adoption_bundle(
        pg_engine,
        bundle=bundle,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.entity_counts["accounts"] == 1
    async with pg_engine.connect() as conn:
        counts = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT
                        (SELECT count(*) FROM ad_accounts) AS accounts,
                        (SELECT count(*) FROM offers) AS offers,
                        (SELECT count(*) FROM offer_ad_accounts) AS links,
                        (SELECT count(*) FROM offer_rules) AS rules,
                        (SELECT count(*) FROM observer_config) AS observer,
                        (SELECT count(*) FROM telegram_recipients) AS recipients,
                        (SELECT count(*) FROM operator_display_preferences) AS display_preferences
                    """
                    )
                )
            )
            .mappings()
            .one()
        )
    assert dict(counts) == {
        "accounts": 0,
        "offers": 0,
        "links": 0,
        "rules": 0,
        "observer": 0,
        "recipients": 0,
        "display_preferences": 0,
    }
