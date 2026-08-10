from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from apps.cleanup_worker.retention import get_default_policy
from core.adoption.bundle import (
    AdoptionAccountV1,
    AdoptionObserverSettingsV1,
    AdoptionOfferRuleV1,
    AdoptionOfferV1,
    AdoptionOperatorDisplaySettingsV1,
    AdoptionRecipientPreferenceV1,
    AdoptionRecipientV1,
    AdoptionSectionsV1,
    AdoptionSystemSettingsV1,
    AdoptionValidationError,
    build_adoption_bundle,
    canonical_bundle_json,
    parse_adoption_bundle_json,
)


def _sections() -> AdoptionSectionsV1:
    return AdoptionSectionsV1(
        accounts=[AdoptionAccountV1(account_id="222"), AdoptionAccountV1(account_id="111")],
        offers=[
            AdoptionOfferV1(
                code="GH_CR2",
                name="GH_CR2",
                vertical="iGaming",
                pixel_id="987",
                is_active=True,
                account_ids=["222", "111"],
                countries=["KE", "GH"],
            )
        ],
        offer_rules=[
            AdoptionOfferRuleV1(
                offer_code="GH_CR2",
                cpa_threshold="3.00",
                currency="USD",
                frequency_threshold="2.50",
                stop_percent_of_rule="80",
                warning_percent_of_stop="80",
            )
        ],
        observer_settings=AdoptionObserverSettingsV1(
            interval_seconds=30,
            owner_campaign_tag="MV",
            campaign_ids=["9002", "9001"],
        ),
        operator_display_settings=AdoptionOperatorDisplaySettingsV1(
            timezone_name="Europe/Kaliningrad",
        ),
        recipients=[
            AdoptionRecipientV1(
                chat_id=42,
                telegram_user_id=42,
                username="owner",
                display_name="Owner",
                role="owner",
            )
        ],
        recipient_preferences=[
            AdoptionRecipientPreferenceV1(
                telegram_user_id=42,
                timezone="Europe/Kaliningrad",
                min_severity="warning",
                quiet_hours_start="23:00:00",
                quiet_hours_end="07:00:00",
                digest_local_time="09:00:00",
                categories={"digest": "inherit"},
                is_enabled=True,
            )
        ],
        system_settings=AdoptionSystemSettingsV1(
            retention_policy=get_default_policy(),
            web_app_url="https://panel.example.test/tma/",
        ),
    )


def _bundle_json() -> str:
    bundle = build_adoption_bundle(
        _sections(),
        exported_at=datetime(2026, 8, 9, 12, 0, tzinfo=UTC),
        source_fingerprint="a" * 64,
    )
    return canonical_bundle_json(bundle)


def test_bundle_is_canonical_and_section_manifest_round_trips() -> None:
    payload = _bundle_json()

    parsed = parse_adoption_bundle_json(payload)

    assert canonical_bundle_json(parsed) == payload
    assert parsed.entity_counts["accounts"] == 2
    assert parsed.sections.accounts[0].account_id == "111"
    assert parsed.sections.offers[0].account_ids == ["111", "222"]
    assert parsed.sections.offer_rules[0].cpa_threshold == "3"
    assert parsed.sections.offer_rules[0].frequency_threshold == "2.5"
    assert parsed.entity_counts["operator_display_settings"] == 1
    assert parsed.sections.operator_display_settings is not None
    assert parsed.sections.operator_display_settings.timezone_name == "Europe/Kaliningrad"
    assert set(json.loads(payload)["sections"]["operator_display_settings"]) == {"timezone_name"}
    assert parsed.sections.system_settings is not None
    assert parsed.sections.system_settings.web_app_url == "https://panel.example.test/tma"


def test_tampered_section_is_rejected_by_manifest() -> None:
    raw = json.loads(_bundle_json())
    raw["sections"]["offers"][0]["name"] = "tampered"

    with pytest.raises(AdoptionValidationError, match="validation failed"):
        parse_adoption_bundle_json(json.dumps(raw))


def test_unknown_or_secret_fields_are_rejected() -> None:
    raw = json.loads(_bundle_json())
    raw["sections"]["observer_settings"]["bot_token"] = "secret"

    with pytest.raises(AdoptionValidationError, match="validation failed"):
        parse_adoption_bundle_json(json.dumps(raw))


def test_non_usd_offer_rule_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AdoptionOfferRuleV1(
            offer_code="GH_CR2",
            cpa_threshold="3.00",
            currency="EUR",
            frequency_threshold=None,
            stop_percent_of_rule="80",
            warning_percent_of_stop="80",
        )


def test_active_offer_requires_known_cabinet() -> None:
    with pytest.raises(ValidationError, match="unknown cabinets"):
        AdoptionSectionsV1(
            accounts=[AdoptionAccountV1(account_id="111")],
            offers=[
                AdoptionOfferV1(
                    code="GH_CR2",
                    name="GH_CR2",
                    is_active=True,
                    account_ids=["222"],
                )
            ],
        )


def test_recipient_roster_requires_exactly_one_dm_owner() -> None:
    with pytest.raises(ValidationError, match="exactly one owner"):
        AdoptionSectionsV1(
            recipients=[
                AdoptionRecipientV1(
                    chat_id=42,
                    telegram_user_id=42,
                    role="recipient",
                )
            ]
        )

    with pytest.raises(ValidationError, match="DM identity"):
        AdoptionRecipientV1(
            chat_id=43,
            telegram_user_id=42,
            role="owner",
        )


def test_preference_requires_recipient_and_complete_quiet_hours() -> None:
    with pytest.raises(ValidationError, match="quiet hours"):
        AdoptionRecipientPreferenceV1(
            telegram_user_id=42,
            timezone="UTC",
            min_severity="warning",
            quiet_hours_start="23:00:00",
            quiet_hours_end=None,
            categories={},
            is_enabled=True,
        )

    preference = AdoptionRecipientPreferenceV1(
        telegram_user_id=99,
        timezone="UTC",
        min_severity="warning",
        categories={},
        is_enabled=True,
    )
    with pytest.raises(ValidationError, match="unknown recipient"):
        AdoptionSectionsV1(recipient_preferences=[preference])


def test_operator_display_timezone_is_backend_validated_and_owner_bound() -> None:
    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        AdoptionOperatorDisplaySettingsV1(timezone_name="Mars/Olympus")

    with pytest.raises(ValidationError, match="exactly one owner"):
        AdoptionSectionsV1(
            operator_display_settings=AdoptionOperatorDisplaySettingsV1(timezone_name="UTC")
        )
