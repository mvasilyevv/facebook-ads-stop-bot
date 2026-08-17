"""Strict, bounded contract for the single owner campaign draft.

The draft stores only recoverable form state.  Preview evidence, run/task state,
secrets and worker checkpoints deliberately have no fields in this contract.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=False)


class CampaignDraftStart(_DraftModel):
    mode: Literal["new", "preset"] = "new"
    preset_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$",
    )


class CampaignDraftIdentity(_DraftModel):
    act_id: str = Field(default="", max_length=64)
    ad_account_ids: list[str] = Field(default_factory=list, max_length=20)
    page_id: str = Field(default="", max_length=64)
    pixel_id: str = Field(default="", max_length=64)
    account_context_state: Literal["ready", "stale", "unavailable"] = "unavailable"
    timezone_name: str = Field(default="", max_length=64)
    currency: Literal["", "USD"] = ""
    currency_exponent: Literal[2] | None = None
    account_context_observed_at: datetime | None = None
    account_context_issue: str | None = Field(default=None, max_length=96)
    offer_code: str = Field(default="", max_length=64)
    byer_tag: str = Field(default="", max_length=64)

    @field_validator("ad_account_ids")
    @classmethod
    def validate_ad_account_ids(cls, values: list[str]) -> list[str]:
        canonical = [value.removeprefix("act_") for value in values]
        if len(canonical) != len(set(canonical)):
            raise ValueError("ad_account_ids must be unique")
        if any(re.fullmatch(r"[0-9]{1,32}", value) is None for value in canonical):
            raise ValueError("ad_account_ids must contain numeric Meta account IDs")
        return canonical


# Четыре стратегии ставок Meta. Список расширяется правкой кода осознанно:
# справочник у Meta мы не читаем, и молча появиться тут ничего не должно.
# Замер 17.08 по трём кабинетам: 41 живая кампания из 55 идёт на
# LOWEST_COST_WITHOUT_CAP, 12 — на COST_CAP.
BidStrategy = Literal[
    "COST_CAP",
    "LOWEST_COST_WITHOUT_CAP",
    "LOWEST_COST_WITH_BID_CAP",
    "LOWEST_COST_WITH_MIN_ROAS",
]


class CampaignDraftGoal(_DraftModel):
    objective: Literal["OUTCOME_SALES"] = "OUTCOME_SALES"
    optimization_goal: Literal["OFFSITE_CONVERSIONS"] = "OFFSITE_CONVERSIONS"
    custom_event_type: Literal["PURCHASE"] = "PURCHASE"
    destination_link: str = Field(default="", max_length=2048)
    cta: str = Field(default="PLAY_GAME", min_length=1, max_length=64)
    display_link: str = Field(default="", max_length=255)
    text_optimizations: Literal["OPT_OUT"] = "OPT_OUT"
    start_date: str = Field(default="", max_length=10)
    budget_level: Literal["campaign", "adset"] = "campaign"
    daily_budget: str = Field(default="", max_length=32)
    bid_amount: str = Field(default="", max_length=32)
    bid_strategy: BidStrategy = "COST_CAP"
    countries: list[str] = Field(default_factory=list, max_length=50)
    age_min: int = Field(default=21, ge=18, le=65, strict=True)
    age_max: int = Field(default=65, ge=18, le=65, strict=True)
    advantage_audience: bool = True
    genders: list[Literal["male", "female"]] = Field(default_factory=list, max_length=2)
    placements: list[Literal["facebook", "instagram", "messenger", "audience_network"]] = Field(
        default_factory=list, max_length=4
    )
    click_through_days: Literal[1, 7, 28] = 1
    view_through_days: Literal[1, 7, 28] = 1
    naming_template: str = Field(default="", max_length=512)
    url_tags_template: str = Field(default="", max_length=1024)
    ad_text_mode: Literal["none", "text"] = "none"
    ad_text_primary: str = Field(default="", max_length=5000)

    @field_validator("display_link")
    @classmethod
    def validate_display_link(cls, value: str) -> str:
        """Meta принимает в caption только настоящий URL или домен.

        Произвольный текст она отклоняет уже на создании креатива, когда
        кампания уже заведена — ловим раньше неё.
        """
        if not value:
            return value
        if re.fullmatch(r"(?:https?://)?[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/[^\s]*)?", value) is None:
            raise ValueError("display_link must be a URL or a domain")
        return value

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("countries must be unique")
        if any(re.fullmatch(r"[A-Z]{2}", value) is None for value in values):
            raise ValueError("countries must contain uppercase ISO-2 codes")
        return values

    @field_validator("genders", "placements")
    @classmethod
    def validate_unique_tags(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("multi-value targeting fields must be unique")
        return values

    @model_validator(mode="after")
    def validate_age_range(self) -> Self:
        if self.age_min > self.age_max:
            raise ValueError("age_min must not exceed age_max")
        return self


class CampaignDraftCampaign(_DraftModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    label: str | None = Field(default=None, max_length=128)
    adset_count: int = Field(ge=1, le=100, strict=True)


class CampaignDraftStructure(_DraftModel):
    campaigns: list[CampaignDraftCampaign] = Field(default_factory=list, max_length=20)


class CampaignDraftConcept(_DraftModel):
    ref: str = Field(min_length=1, max_length=255, pattern=r"^[^/\\]+$")
    original_name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0, le=500 * 1024 * 1024, strict=True)
    content_type: str | None = Field(default=None, max_length=128)
    campaign_keys: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("campaign_keys")
    @classmethod
    def validate_campaign_keys(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("campaign_keys must be unique")
        if any(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None for value in values):
            raise ValueError("campaign_keys contain an invalid key")
        return values


class CampaignDraftCreatives(_DraftModel):
    upload_id: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    concepts: list[CampaignDraftConcept] = Field(default_factory=list, max_length=50)
    copies_per_concept: int | None = Field(default=None, ge=1, le=100, strict=True)


class CampaignDraftState(_DraftModel):
    """Only form state that is safe and useful after reload/reconnect."""

    current_step: Literal[1, 2, 3, 4, 5, 6, 7] = 1
    start: CampaignDraftStart = Field(default_factory=CampaignDraftStart)
    identity: CampaignDraftIdentity = Field(default_factory=CampaignDraftIdentity)
    goal: CampaignDraftGoal = Field(default_factory=CampaignDraftGoal)
    structure: CampaignDraftStructure = Field(default_factory=CampaignDraftStructure)
    creatives: CampaignDraftCreatives = Field(default_factory=CampaignDraftCreatives)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        campaign_keys = [campaign.key for campaign in self.structure.campaigns]
        if len(campaign_keys) != len(set(campaign_keys)):
            raise ValueError("campaign keys must be unique")
        concept_refs = [concept.ref for concept in self.creatives.concepts]
        if len(concept_refs) != len(set(concept_refs)):
            raise ValueError("concept refs must be unique")
        known_keys = set(campaign_keys)
        for concept in self.creatives.concepts:
            unknown = set(concept.campaign_keys) - known_keys
            if unknown:
                raise ValueError("concept campaign_keys must reference draft campaigns")
        return self


class CampaignDraftDocument(_DraftModel):
    revision: int = Field(ge=1, strict=True)
    state: CampaignDraftState
    updated_at: datetime


class CampaignDraftEnvelope(_DraftModel):
    draft: CampaignDraftDocument | None


class CampaignDraftPutIn(_DraftModel):
    expected_revision: int = Field(ge=0, strict=True)
    state: CampaignDraftState


__all__ = [
    "BidStrategy",
    "CampaignDraftCampaign",
    "CampaignDraftConcept",
    "CampaignDraftCreatives",
    "CampaignDraftDocument",
    "CampaignDraftEnvelope",
    "CampaignDraftGoal",
    "CampaignDraftIdentity",
    "CampaignDraftPutIn",
    "CampaignDraftStart",
    "CampaignDraftState",
    "CampaignDraftStructure",
]
