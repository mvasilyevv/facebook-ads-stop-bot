"""Canonical, secret-free adoption-bundle/v1 contract.

This module is the validation seam for export, dry-run and import. Database and
CLI adapters must pass through it; they may not invent their own allowlists or
normalization rules.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from core.operator.timezones import validate_iana_timezone

SCHEMA_VERSION = "adoption-bundle/v1"
SECTION_NAMES = (
    "accounts",
    "offers",
    "offer_rules",
    "observer_settings",
    "operator_display_settings",
    "recipients",
    "recipient_preferences",
    "system_settings",
)

AccountId = Annotated[str, Field(pattern=r"^[0-9]{1,32}$")]
OfferCode = Annotated[str, Field(pattern=r"^[A-Z0-9_.-]{1,32}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Severity = Literal["ok", "warning", "critical", "unknown"]
PreferenceThreshold = Literal["off", "inherit", "ok", "warning", "critical", "unknown"]

_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?$")
_RETENTION_RE = re.compile(
    r"^(?:[1-9][0-9]* (?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)"
    r"|forever|immediate|redis_ttl_only)$"
)
_RETENTION_KEYS = frozenset(
    {
        "ad_metrics",
        "alert_events",
        "scan_runs",
        "meta_api_audit_log",
        "adsetpro_postback_events",
        "task_queue_completed",
        "task_queue_failed",
        "adset_duplicate_previews_expired",
        "browser_operation_capabilities_expired",
        "telegram_invites_expired",
        "operator_revision_events",
        "incidents_terminal",
        "notification_events_terminal",
        "telegram_action_tokens_terminal",
        "telegram_navigation_tokens_terminal",
        "telegram_updates_terminal",
        "telegram_command_replies_terminal",
        "ai_cache",
    }
)


class AdoptionValidationError(ValueError):
    """The bundle is well-formed JSON but violates its signed contract."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _sorted_unique(values: list[str], *, field: str) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError(f"{field} must not contain duplicates")
    return sorted(values)


def _decimal_string(value: str | None, *, field: str) -> str | None:
    if value is None:
        return None
    if not _DECIMAL_RE.fullmatch(value):
        raise ValueError(f"{field} must be a canonical non-negative decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a finite decimal string") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    # Semantic manifests must not depend on PostgreSQL NUMERIC scale.  For
    # example, source ``3.00`` and target ``3.000000`` represent the same
    # threshold and therefore canonicalize to the same signed value.
    canonical = format(parsed, "f")
    if "." in canonical:
        canonical = canonical.rstrip("0").rstrip(".")
    return canonical or "0"


class AdoptionAccountV1(_StrictModel):
    account_id: AccountId


class AdoptionOfferV1(_StrictModel):
    code: OfferCode
    name: str = Field(min_length=1, max_length=128)
    vertical: str | None = Field(default=None, max_length=32)
    pixel_id: str | None = Field(default=None, pattern=r"^[0-9]{1,64}$")
    is_active: bool
    account_ids: list[AccountId] = Field(default_factory=list)
    countries: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = Field(default_factory=list)

    @field_validator("account_ids")
    @classmethod
    def sort_accounts(cls, values: list[str]) -> list[str]:
        return _sorted_unique(values, field="account_ids")

    @field_validator("countries")
    @classmethod
    def sort_countries(cls, values: list[str]) -> list[str]:
        return _sorted_unique(values, field="countries")

    @model_validator(mode="after")
    def active_offer_requires_account(self) -> Self:
        if self.is_active and not self.account_ids:
            raise ValueError("active offers require at least one cabinet account")
        return self


class AdoptionOfferRuleV1(_StrictModel):
    offer_code: OfferCode
    cpa_threshold: str | None = None
    currency: Literal["USD"] | None = None
    frequency_threshold: str | None = None
    stop_percent_of_rule: str
    warning_percent_of_stop: str

    @field_validator(
        "cpa_threshold",
        "frequency_threshold",
        "stop_percent_of_rule",
        "warning_percent_of_stop",
    )
    @classmethod
    def validate_decimal(cls, value: str | None, info) -> str | None:
        return _decimal_string(value, field=info.field_name)

    @model_validator(mode="after")
    def validate_rule_semantics(self) -> Self:
        if (self.cpa_threshold is None) != (self.currency is None):
            raise ValueError("cpa_threshold and USD currency must be set together")
        for field_name in ("stop_percent_of_rule", "warning_percent_of_stop"):
            value = Decimal(getattr(self, field_name))
            if value < 1 or value > 100:
                raise ValueError(f"{field_name} must be between 1 and 100")
        if self.cpa_threshold is not None and Decimal(self.cpa_threshold) <= 0:
            raise ValueError("cpa_threshold must be positive")
        if self.frequency_threshold is not None and Decimal(self.frequency_threshold) <= 0:
            raise ValueError("frequency_threshold must be positive")
        return self


class AdoptionObserverSettingsV1(_StrictModel):
    interval_seconds: int = Field(ge=30, le=600)
    owner_campaign_tag: str | None = Field(default=None, max_length=255)
    campaign_ids: list[AccountId] = Field(default_factory=list)

    @field_validator("owner_campaign_tag")
    @classmethod
    def normalize_owner_tag(cls, value: str | None) -> str | None:
        normalized = (value or "").strip()
        return normalized or None

    @field_validator("campaign_ids")
    @classmethod
    def sort_campaign_ids(cls, values: list[str]) -> list[str]:
        return _sorted_unique(values, field="campaign_ids")


class AdoptionOperatorDisplaySettingsV1(_StrictModel):
    """Owner presentation preference without recipient identity duplication."""

    timezone_name: str = Field(min_length=1, max_length=64)

    @field_validator("timezone_name")
    @classmethod
    def validate_timezone_name(cls, value: str) -> str:
        return validate_iana_timezone(value)


class AdoptionRecipientV1(_StrictModel):
    chat_id: int = Field(gt=0)
    telegram_user_id: int = Field(gt=0)
    username: str | None = Field(default=None, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    role: Literal["owner", "recipient"]

    @model_validator(mode="after")
    def require_dm_identity(self) -> Self:
        if self.chat_id != self.telegram_user_id:
            raise ValueError("recipient must be a DM identity")
        return self


class AdoptionRecipientPreferenceV1(_StrictModel):
    telegram_user_id: int = Field(gt=0)
    timezone: str = Field(min_length=1, max_length=64)
    min_severity: Severity
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    digest_local_time: str | None = None
    categories: dict[Annotated[str, Field(min_length=1, max_length=64)], PreferenceThreshold] = (
        Field(default_factory=dict, max_length=32)
    )
    is_enabled: bool

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown IANA timezone") from exc
        return value

    @field_validator("quiet_hours_start", "quiet_hours_end", "digest_local_time")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        if value is not None and not _TIME_RE.fullmatch(value):
            raise ValueError("time must use canonical HH:MM:SS[.ffffff]")
        return value

    @model_validator(mode="after")
    def validate_quiet_hours_pair(self) -> Self:
        if (self.quiet_hours_start is None) != (self.quiet_hours_end is None):
            raise ValueError("quiet hours must be set as a pair")
        return self


class AdoptionSystemSettingsV1(_StrictModel):
    retention_policy: dict[str, str] | None = None
    web_app_url: str | None = None

    @field_validator("retention_policy")
    @classmethod
    def validate_retention(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        if set(value) != _RETENTION_KEYS:
            raise ValueError("retention_policy must contain the exact reviewed key set")
        for duration in value.values():
            if not _RETENTION_RE.fullmatch(duration):
                raise ValueError("retention_policy contains an invalid duration")
        return dict(sorted(value.items()))

    @field_validator("web_app_url")
    @classmethod
    def validate_web_app_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError("web_app_url must be a safe HTTPS URL") from exc
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("web_app_url must be a safe HTTPS base URL")
        return value.rstrip("/")


class AdoptionSectionsV1(_StrictModel):
    accounts: list[AdoptionAccountV1] = Field(default_factory=list)
    offers: list[AdoptionOfferV1] = Field(default_factory=list)
    offer_rules: list[AdoptionOfferRuleV1] = Field(default_factory=list)
    observer_settings: AdoptionObserverSettingsV1 | None = None
    operator_display_settings: AdoptionOperatorDisplaySettingsV1 | None = None
    recipients: list[AdoptionRecipientV1] = Field(default_factory=list)
    recipient_preferences: list[AdoptionRecipientPreferenceV1] = Field(default_factory=list)
    system_settings: AdoptionSystemSettingsV1 | None = None

    @field_validator("accounts")
    @classmethod
    def sort_accounts(cls, values: list[AdoptionAccountV1]) -> list[AdoptionAccountV1]:
        if len({value.account_id for value in values}) != len(values):
            raise ValueError("accounts must be unique")
        return sorted(values, key=lambda value: value.account_id)

    @field_validator("offers")
    @classmethod
    def sort_offers(cls, values: list[AdoptionOfferV1]) -> list[AdoptionOfferV1]:
        if len({value.code for value in values}) != len(values):
            raise ValueError("offers must be unique")
        return sorted(values, key=lambda value: value.code)

    @field_validator("offer_rules")
    @classmethod
    def sort_rules(cls, values: list[AdoptionOfferRuleV1]) -> list[AdoptionOfferRuleV1]:
        if len({value.offer_code for value in values}) != len(values):
            raise ValueError("offer_rules must be unique per offer")
        return sorted(values, key=lambda value: value.offer_code)

    @field_validator("recipients")
    @classmethod
    def sort_recipients(cls, values: list[AdoptionRecipientV1]) -> list[AdoptionRecipientV1]:
        if len({value.telegram_user_id for value in values}) != len(values):
            raise ValueError("telegram recipients must have unique user identities")
        if len({value.chat_id for value in values}) != len(values):
            raise ValueError("telegram recipients must have unique DM chats")
        return sorted(values, key=lambda value: value.telegram_user_id)

    @field_validator("recipient_preferences")
    @classmethod
    def sort_preferences(
        cls, values: list[AdoptionRecipientPreferenceV1]
    ) -> list[AdoptionRecipientPreferenceV1]:
        if len({value.telegram_user_id for value in values}) != len(values):
            raise ValueError("recipient preferences must be unique")
        return sorted(values, key=lambda value: value.telegram_user_id)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        account_ids = {account.account_id for account in self.accounts}
        for offer in self.offers:
            missing = set(offer.account_ids) - account_ids
            if missing:
                raise ValueError(f"offer {offer.code} references unknown cabinets")
        offer_codes = {offer.code for offer in self.offers}
        if any(rule.offer_code not in offer_codes for rule in self.offer_rules):
            raise ValueError("offer rule references an unknown offer")
        recipient_ids = {recipient.telegram_user_id for recipient in self.recipients}
        if any(
            preference.telegram_user_id not in recipient_ids
            for preference in self.recipient_preferences
        ):
            raise ValueError("recipient preference references an unknown recipient")
        owner_count = sum(recipient.role == "owner" for recipient in self.recipients)
        if self.recipients and owner_count != 1:
            raise ValueError("bundle must contain exactly one owner")
        if self.operator_display_settings is not None and owner_count != 1:
            raise ValueError("operator display settings require exactly one owner")
        return self


class AdoptionBundleV1(_StrictModel):
    schema_version: Literal["adoption-bundle/v1"]
    exported_at: AwareDatetime
    source_fingerprint: Sha256
    entity_counts: dict[str, int]
    section_sha256: dict[str, Sha256]
    sections: AdoptionSectionsV1

    @model_validator(mode="after")
    def verify_manifest(self) -> Self:
        expected_counts = _entity_counts(self.sections)
        if self.entity_counts != expected_counts:
            raise ValueError("entity_counts do not match bundle sections")
        expected_hashes = _section_hashes(self.sections)
        if self.section_sha256 != expected_hashes:
            raise ValueError("section_sha256 does not match bundle sections")
        return self


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _section_payloads(sections: AdoptionSectionsV1) -> dict[str, Any]:
    return sections.model_dump(mode="json")


def _section_hashes(sections: AdoptionSectionsV1) -> dict[str, str]:
    payloads = _section_payloads(sections)
    return {
        name: hashlib.sha256(_canonical_json(payloads[name]).encode("utf-8")).hexdigest()
        for name in SECTION_NAMES
    }


def _entity_counts(sections: AdoptionSectionsV1) -> dict[str, int]:
    return {
        "accounts": len(sections.accounts),
        "offers": len(sections.offers),
        "offer_rules": len(sections.offer_rules),
        "observer_settings": int(sections.observer_settings is not None),
        "operator_display_settings": int(sections.operator_display_settings is not None),
        "recipients": len(sections.recipients),
        "recipient_preferences": len(sections.recipient_preferences),
        "system_settings": int(sections.system_settings is not None),
    }


def build_adoption_bundle(
    sections: AdoptionSectionsV1,
    *,
    exported_at: datetime,
    source_fingerprint: str,
) -> AdoptionBundleV1:
    """Build an integrity manifest from already allowlisted configuration."""
    return AdoptionBundleV1(
        schema_version=SCHEMA_VERSION,
        exported_at=exported_at,
        source_fingerprint=source_fingerprint,
        entity_counts=_entity_counts(sections),
        section_sha256=_section_hashes(sections),
        sections=sections,
    )


def canonical_bundle_json(bundle: AdoptionBundleV1) -> str:
    """Serialize with one deterministic UTF-8 representation."""
    return _canonical_json(bundle.model_dump(mode="json")) + "\n"


def canonical_bundle_sha256(bundle: AdoptionBundleV1) -> str:
    """Hash the exact canonical UTF-8 bundle representation."""

    return hashlib.sha256(canonical_bundle_json(bundle).encode("utf-8")).hexdigest()


def parse_adoption_bundle_json(payload: str | bytes) -> AdoptionBundleV1:
    """Parse, validate references and verify every section digest."""
    try:
        bundle = AdoptionBundleV1.model_validate_json(payload)
    except Exception as exc:
        raise AdoptionValidationError("adoption bundle validation failed") from exc
    canonical = canonical_bundle_json(bundle)
    try:
        reparsed = AdoptionBundleV1.model_validate_json(canonical)
    except Exception as exc:  # pragma: no cover - defensive serializer invariant
        raise AdoptionValidationError("canonical adoption bundle is invalid") from exc
    return reparsed


__all__ = [
    "AdoptionAccountV1",
    "AdoptionBundleV1",
    "AdoptionOfferRuleV1",
    "AdoptionOfferV1",
    "AdoptionObserverSettingsV1",
    "AdoptionOperatorDisplaySettingsV1",
    "AdoptionRecipientPreferenceV1",
    "AdoptionRecipientV1",
    "AdoptionSectionsV1",
    "AdoptionSystemSettingsV1",
    "AdoptionValidationError",
    "SCHEMA_VERSION",
    "SECTION_NAMES",
    "build_adoption_bundle",
    "canonical_bundle_json",
    "canonical_bundle_sha256",
    "parse_adoption_bundle_json",
]
