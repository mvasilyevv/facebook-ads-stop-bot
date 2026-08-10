from __future__ import annotations

import uuid
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
from core.adoption.service import adopt_first_release_bundle, apply_adoption_bundle


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


@pytest.mark.asyncio
async def test_first_release_adoption_allows_secret_bootstrap_and_reconciles_retry(
    pg_engine,
) -> None:
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
        recipients=[
            AdoptionRecipientV1(
                chat_id=42,
                telegram_user_id=42,
                role="owner",
            )
        ],
        system_settings=AdoptionSystemSettingsV1(
            retention_policy=get_default_policy(),
            web_app_url="https://app.adpulse.su/tma/",
        ),
    )
    bundle = build_adoption_bundle(
        sections,
        exported_at=datetime(2026, 8, 9, tzinfo=UTC),
        source_fingerprint="b" * 64,
    )

    try:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_config (
                        id, singleton_key, bot_token_encrypted
                    ) VALUES (
                        :telegram_id, 'default', 'encrypted-test-token'
                    )
                    """
                ),
                {"telegram_id": uuid.uuid4()},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO adsetpro_credentials (
                        id, singleton_key, api_key_encrypted
                    ) VALUES (
                        :adsetpro_id, 'default', :encrypted_key
                    )
                    """
                ),
                {"adsetpro_id": uuid.uuid4(), "encrypted_key": b"encrypted-test-key"},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO system_config (id, key, value, description)
                    VALUES (
                        :config_id,
                        'web_app_url',
                        CAST('{"url":"https://app.adpulse.su/tma/"}' AS jsonb),
                        'bootstrap test'
                    )
                    """
                ),
                {"config_id": uuid.uuid4()},
            )

        imported = await adopt_first_release_bundle(pg_engine, bundle=bundle)
        retried = await adopt_first_release_bundle(pg_engine, bundle=bundle)

        assert imported.imported is True
        assert retried.imported is False
        assert imported.section_sha256 == retried.section_sha256
    finally:
        async with pg_engine.begin() as conn:
            for table in (
                "operator_display_preferences",
                "telegram_recipient_preferences",
                "telegram_recipients",
                "operator_revision_events",
                "observer_config",
                "offer_rules",
                "offers",
                "ad_accounts",
                "telegram_config",
                "adsetpro_credentials",
            ):
                await conn.execute(text(f'DELETE FROM "{table}"'))
            await conn.execute(text("DELETE FROM system_config WHERE key = 'web_app_url'"))
