from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
    canonical_bundle_sha256,
)
from core.adoption.repository import AdoptionTargetPreflightError
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
                        (SELECT count(*) FROM operator_display_preferences) AS display_preferences,
                        (SELECT count(*) FROM adoption_receipt) AS receipts
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
        "receipts": 0,
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
        async with pg_engine.begin() as conn:
            await conn.execute(text("UPDATE offers SET name = 'Owner changed after adoption'"))
        retried = await adopt_first_release_bundle(pg_engine, bundle=bundle)

        assert imported.imported is True
        assert retried.imported is False
        assert imported.section_sha256 == retried.section_sha256
        async with pg_engine.connect() as conn:
            receipt = (
                (
                    await conn.execute(
                        text(
                            """
                            SELECT id, schema_version, bundle_sha256, source_fingerprint,
                                   entity_counts, section_sha256, imported_at
                            FROM adoption_receipt
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
        assert receipt["id"] == 1
        assert receipt["schema_version"] == "adoption-bundle/v1"
        assert receipt["bundle_sha256"] == canonical_bundle_sha256(bundle)
        assert receipt["source_fingerprint"] == bundle.source_fingerprint
        assert receipt["entity_counts"] == bundle.entity_counts
        assert receipt["section_sha256"] == bundle.section_sha256
        assert receipt["imported_at"].tzinfo is not None
        assert receipt["imported_at"].utcoffset() is not None

        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM adoption_receipt"))
        with pytest.raises(AdoptionTargetPreflightError, match="application data"):
            await adopt_first_release_bundle(pg_engine, bundle=bundle)
    finally:
        async with pg_engine.begin() as conn:
            for table in (
                "adoption_receipt",
                "operator_display_preferences",
                "telegram_recipient_preferences",
                "telegram_recipients",
                "observer_config",
                "operator_revision_events",
                "offer_rules",
                "offers",
                "ad_accounts",
                "telegram_config",
                "adsetpro_credentials",
            ):
                await conn.execute(text(f'DELETE FROM "{table}"'))
            await conn.execute(text("DELETE FROM system_config WHERE key = 'web_app_url'"))


@pytest.mark.asyncio
async def test_concurrent_first_release_adoption_commits_one_receipt(pg_engine) -> None:
    bundle = build_adoption_bundle(
        AdoptionSectionsV1(
            system_settings=AdoptionSystemSettingsV1(
                retention_policy=get_default_policy(),
                web_app_url=None,
            )
        ),
        exported_at=datetime(2026, 8, 9, tzinfo=UTC),
        source_fingerprint="c" * 64,
    )

    try:
        first, second = await asyncio.gather(
            adopt_first_release_bundle(pg_engine, bundle=bundle),
            adopt_first_release_bundle(pg_engine, bundle=bundle),
        )

        assert sorted((first.imported, second.imported)) == [False, True]
        async with pg_engine.connect() as conn:
            assert await conn.scalar(text("SELECT count(*) FROM adoption_receipt")) == 1
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM adoption_receipt"))
            await conn.execute(text("DELETE FROM system_config WHERE key = 'web_app_url'"))


@pytest.mark.asyncio
async def test_adoption_receipt_is_a_database_enforced_singleton(pg_engine) -> None:
    with pytest.raises(IntegrityError):
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO adoption_receipt (
                        id, schema_version, bundle_sha256, source_fingerprint,
                        entity_counts, section_sha256
                    ) VALUES (
                        2, 'adoption-bundle/v1', :digest, :digest,
                        '{}'::jsonb, '{}'::jsonb
                    )
                    """
                ),
                {"digest": "d" * 64},
            )
