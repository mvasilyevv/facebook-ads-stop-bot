from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

import core.adoption.repository as adoption_repository
from apps.cleanup_worker.retention import get_default_policy
from core.ad_account_catalog import ad_account_catalog
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
)
from core.adoption.profiles import (
    LEGACY_0036_PROFILE,
    LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE,
    get_source_profile,
)
from core.adoption.repository import (
    AdoptionTargetPreflightError,
    LegacyArraySourceRepository,
    NormalizedTargetRepository,
)
from migrations.baseline_contract import BASELINE_REVISION


def _sections() -> AdoptionSectionsV1:
    return AdoptionSectionsV1(
        accounts=[AdoptionAccountV1(account_id="111")],
        offers=[
            AdoptionOfferV1(
                code="GH_CR2",
                name="Ghana",
                vertical="iGaming",
                pixel_id="987",
                is_active=True,
                account_ids=["111"],
                countries=["GH"],
            )
        ],
        offer_rules=[
            AdoptionOfferRuleV1(
                offer_code="GH_CR2",
                cpa_threshold="3",
                currency="USD",
                frequency_threshold="2.5",
                stop_percent_of_rule="80",
                warning_percent_of_stop="70",
            )
        ],
        observer_settings=AdoptionObserverSettingsV1(
            interval_seconds=30,
            owner_campaign_tag="MV",
            campaign_ids=["9001"],
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
            web_app_url="https://panel.example.test/tma",
        ),
    )


class _WriteConnection:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement, _params=None):
        table_name = statement.table.name
        values = dict(statement.compile().params)
        self.events.append((table_name, values))
        return None


@pytest.mark.asyncio
async def test_normalized_import_uses_fk_safe_order_and_forces_scanning_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _WriteConnection()

    async def create_accounts(_conn, account_ids):
        values = tuple(account_ids)
        conn.events.append(("ad_accounts", {"account_ids": values}))
        return values

    async def replace_accounts(_conn, *, offer_id, account_ids):
        values = tuple(account_ids)
        conn.events.append(("offer_ad_accounts", {"offer_id": offer_id, "account_ids": values}))
        return values

    monkeypatch.setattr(ad_account_catalog, "create_accounts", create_accounts)
    monkeypatch.setattr(ad_account_catalog, "replace_offer_accounts", replace_accounts)

    await NormalizedTargetRepository(conn).import_sections(  # type: ignore[arg-type]
        _sections()
    )

    assert [name for name, _values in conn.events] == [
        "ad_accounts",
        "offers",
        "offer_ad_accounts",
        "offer_rules",
        "observer_config",
        "telegram_recipients",
        "operator_display_preferences",
        "telegram_recipient_preferences",
        "system_config",
        "system_config",
    ]
    offer_values = conn.events[1][1]
    assert isinstance(offer_values["id"], UUID)
    assert conn.events[2][1]["offer_id"] == offer_values["id"]
    assert conn.events[2][1]["account_ids"] == ("111",)

    rule_values = conn.events[3][1]
    assert rule_values["offer_id"] == offer_values["id"]
    assert rule_values["cpa_threshold"] == Decimal("3")
    assert rule_values["currency"] == "USD"

    observer_values = conn.events[4][1]
    assert observer_values["is_scanning_enabled"] is False

    recipient_values = conn.events[5][1]
    assert isinstance(recipient_values["id"], UUID)
    assert recipient_values["role"] == "owner"
    assert recipient_values["invite_id"] is None
    assert recipient_values["revoked_at"] is None
    assert conn.events[6][1] == {
        "owner_recipient_id": recipient_values["id"],
        "timezone_name": "Europe/Kaliningrad",
    }
    assert conn.events[7][1]["recipient_id"] == recipient_values["id"]
    assert conn.events[7][1]["quiet_hours_start"] == time(23, 0)
    assert [values["key"] for _name, values in conn.events[8:]] == [
        "retention_policy",
        "web_app_url",
    ]


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _LegacyReadConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "adoption:legacy-offers" in sql:
            return _Rows(
                [
                    {
                        "code": "GH_CR2",
                        "name": "Ghana",
                        "vertical": "iGaming",
                        "pixel_id": "987",
                        "is_active": True,
                        "ad_account_ids": ["111", "111"],
                        "countries": ["gh", "GH"],
                    }
                ]
            )
        if "adoption:legacy-rules-with-currency" in sql:
            return _Rows(
                [
                    {
                        "offer_code": "GH_CR2",
                        "cpa_threshold": Decimal("3.00"),
                        "currency": "USD",
                        "frequency_threshold": None,
                        "stop_percent_of_rule": Decimal("80"),
                        "warning_percent_of_stop": Decimal("80"),
                    }
                ]
            )
        if "adoption:legacy-rules-implicit-usd" in sql:
            return _Rows(
                [
                    {
                        "offer_code": "GH_CR2",
                        "cpa_threshold": Decimal("3.00"),
                        "frequency_threshold": None,
                        "stop_percent_of_rule": Decimal("80"),
                        "warning_percent_of_stop": Decimal("80"),
                    }
                ]
            )
        if "adoption:legacy-observer" in sql:
            return _Rows(
                [
                    {
                        "interval_seconds": 30,
                        "owner_campaign_tag": "MV",
                        "campaign_ids": ["9001"],
                    }
                ]
            )
        if "adoption:legacy-recipients" in sql:
            return _Rows(
                [
                    {
                        "id": UUID("00000000-0000-0000-0000-000000000001"),
                        "chat_id": 42,
                        "telegram_user_id": 42,
                        "username": "owner",
                        "display_name": "Owner",
                        "role": "owner",
                    }
                ]
            )
        if "adoption:legacy-recipient-preferences" in sql:
            return _Rows(
                [
                    {
                        "telegram_user_id": 42,
                        "timezone": "Europe/Kaliningrad",
                        "min_severity": "warning",
                        "quiet_hours_start": None,
                        "quiet_hours_end": None,
                        "digest_local_time": None,
                        "categories": {},
                        "is_enabled": True,
                    }
                ]
            )
        if "adoption:legacy-operator-display-settings" in sql:
            return _Rows([{"timezone_name": "America/New_York"}])
        if "adoption:allowlisted-system-settings" in sql:
            return _Rows(
                [
                    {"key": "retention_policy", "value": get_default_policy()},
                    {"key": "web_app_url", "value": {"url": "https://panel.test"}},
                ]
            )
        raise AssertionError(f"unexpected source query: {sql}")


@pytest.mark.asyncio
async def test_no_preferences_profile_projects_only_allowlisted_legacy_configuration() -> None:
    conn = _LegacyReadConnection()
    repository = LegacyArraySourceRepository(  # type: ignore[arg-type]
        conn,
        get_source_profile(LEGACY_0036_PROFILE),
    )

    sections = await repository.project()

    assert [account.account_id for account in sections.accounts] == ["111"]
    assert sections.offers[0].account_ids == ["111"]
    assert sections.offers[0].countries == ["GH"]
    assert sections.offer_rules[0].currency == "USD"
    assert sections.recipient_preferences == []
    assert sections.operator_display_settings is None
    assert not any("legacy-recipient-preferences" in sql for sql in conn.statements)
    assert not any("legacy-operator-display-settings" in sql for sql in conn.statements)
    assert all("SELECT *" not in sql.upper() for sql in conn.statements)


@pytest.mark.asyncio
async def test_display_profile_exports_one_owner_timezone_without_identity_duplication() -> None:
    conn = _LegacyReadConnection()
    repository = LegacyArraySourceRepository(  # type: ignore[arg-type]
        conn,
        get_source_profile(LEGACY_BASELINE_DISPLAY_PREFERENCES_PROFILE),
    )

    sections = await repository.project()

    assert sections.operator_display_settings is not None
    assert sections.operator_display_settings.model_dump() == {"timezone_name": "America/New_York"}
    assert sum("adoption:legacy-operator-display-settings" in sql for sql in conn.statements) == 1


class _WrongRevisionScalars:
    def all(self) -> list[str]:
        return ["wrong"]


class _WrongRevisionConnection:
    async def scalars(self, _statement) -> _WrongRevisionScalars:
        return _WrongRevisionScalars()


@pytest.mark.asyncio
async def test_target_preflight_rejects_wrong_baseline_before_any_write() -> None:
    with pytest.raises(AdoptionTargetPreflightError, match="revision"):
        await NormalizedTargetRepository(  # type: ignore[arg-type]
            _WrongRevisionConnection()
        ).preflight_fresh()


class _FreshGuardScalars:
    def __init__(self, values: list[str]) -> None:
        self._values = values

    def all(self) -> list[str]:
        return self._values


class _DirtyFreshTargetConnection:
    async def scalars(self, statement) -> _FreshGuardScalars:
        if "adoption:target-revision" in str(statement):
            return _FreshGuardScalars([BASELINE_REVISION])
        if "adoption:target-fresh-data" in str(statement):
            return _FreshGuardScalars(["offers"])
        raise AssertionError(f"unexpected scalar query: {statement}")

    async def execute(self, _statement) -> _Rows:
        return _Rows([])

    async def run_sync(self, _callback) -> list[Any]:
        return []


@pytest.mark.asyncio
async def test_target_preflight_rejects_any_existing_application_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(adoption_repository, "assert_catalog_artifacts", lambda _rows: None)
    monkeypatch.setattr(
        adoption_repository,
        "validate_database_extension_layout",
        lambda _rows, *, baseline_installed: None,
    )
    monkeypatch.setattr(
        adoption_repository,
        "describe_standalone_public_catalog_objects",
        lambda _rows, *, allow_manifested_routines: [],
    )
    monkeypatch.setattr(
        adoption_repository,
        "validate_public_partition_layout",
        lambda _rows, *, require_baseline_defaults: [],
    )

    with pytest.raises(AdoptionTargetPreflightError, match="application data"):
        await NormalizedTargetRepository(  # type: ignore[arg-type]
            _DirtyFreshTargetConnection()
        ).preflight_fresh()
