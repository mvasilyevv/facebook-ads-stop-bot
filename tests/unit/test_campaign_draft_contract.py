from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from core.campaign_drafts import CampaignDraftState
from core.campaign_drafts.repository import (
    CampaignDraftTooLargeError,
    _serialized_state,
)


def _state() -> dict[str, object]:
    return {
        "current_step": 5,
        "start": {"mode": "new", "preset_id": None},
        "identity": {
            "act_id": "123",
            "page_id": "456",
            "pixel_id": "789",
            "account_context_state": "ready",
            "timezone_name": "Europe/Kaliningrad",
            "currency": "USD",
            "currency_exponent": 2,
            "account_context_observed_at": "2026-08-09T10:00:00Z",
            "account_context_issue": None,
            "offer_code": "GH_CR2",
            "byer_tag": "MV",
        },
        "goal": {
            "objective": "OUTCOME_SALES",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "custom_event_type": "PURCHASE",
            "destination_link": "https://example.com/click",
            "cta": "PLAY_GAME",
            "text_optimizations": "OPT_OUT",
            "start_date": "2099-08-10",
            "budget_level": "campaign",
            "daily_budget": "200.00",
            "bid_amount": "5.00",
            "bid_strategy": "COST_CAP",
            "countries": ["BR"],
            "age_min": 21,
            "age_max": 65,
            "advantage_audience": True,
            "click_through_days": 1,
            "view_through_days": 1,
            "ad_text_mode": "none",
            "ad_text_primary": "",
        },
        "structure": {"campaigns": [{"key": "static", "label": None, "adset_count": 2}]},
        "creatives": {
            "upload_id": "f20e271e-0cb6-4f70-86bb-3e1b60f8e943",
            "concepts": [
                {
                    "ref": "creative.jpg",
                    "original_name": "creative.jpg",
                    "size_bytes": 1024,
                    "content_type": "image/jpeg",
                    "campaign_keys": ["static"],
                }
            ],
            "copies_per_concept": 2,
        },
    }


def test_campaign_draft_contract_accepts_only_recoverable_form_state() -> None:
    draft = CampaignDraftState.model_validate(_state())

    payload = draft.model_dump(mode="json")
    assert payload["identity"]["currency"] == "USD"
    assert "run_id" not in payload
    assert "preview" not in payload
    assert "secret" not in json.dumps(payload)


@pytest.mark.parametrize(
    "forbidden_key",
    ["run_id", "task_id", "preview", "api_key", "access_token", "worker_checkpoint"],
)
def test_campaign_draft_rejects_runtime_and_secret_fields(forbidden_key: str) -> None:
    payload = _state()
    payload[forbidden_key] = "must-not-persist"

    with pytest.raises(ValidationError):
        CampaignDraftState.model_validate(payload)


def test_campaign_draft_rejects_unknown_campaign_reference() -> None:
    payload = _state()
    payload["creatives"]["concepts"][0]["campaign_keys"] = ["missing"]  # type: ignore[index]

    with pytest.raises(ValidationError, match="reference draft campaigns"):
        CampaignDraftState.model_validate(payload)


def test_campaign_draft_rejects_non_usd_currency() -> None:
    payload = _state()
    payload["identity"]["currency"] = "EUR"  # type: ignore[index]

    with pytest.raises(ValidationError):
        CampaignDraftState.model_validate(payload)


def test_campaign_draft_serialization_is_bounded() -> None:
    payload = _state()
    payload["goal"]["ad_text_primary"] = "я" * 5_000  # type: ignore[index]
    state = CampaignDraftState.model_validate(payload)
    assert len(_serialized_state(state).encode("utf-8")) < 256 * 1024

    object.__setattr__(state.goal, "ad_text_primary", "я" * 200_000)
    with pytest.raises(CampaignDraftTooLargeError):
        _serialized_state(state)
