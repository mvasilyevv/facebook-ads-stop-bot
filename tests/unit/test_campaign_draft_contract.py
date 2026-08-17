from __future__ import annotations

import json
import pickle

import pytest
from pydantic import ValidationError

from core.campaign_drafts import CampaignDraftState
from core.campaign_drafts.repository import (
    CampaignDraftConflictError,
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


def test_campaign_draft_conflict_supports_exception_traceback_protocol() -> None:
    conflict = CampaignDraftConflictError(expected_revision=3, actual_revision=4)

    # Async transaction context managers reassign __traceback__ while
    # propagating an exception. Domain exceptions must permit that protocol.
    conflict.__traceback__ = None

    assert isinstance(conflict, RuntimeError)
    assert conflict.expected_revision == 3
    assert conflict.actual_revision == 4

    restored = pickle.loads(pickle.dumps(conflict))
    assert restored.expected_revision == 3
    assert restored.actual_revision == 4


def test_draft_allows_the_strategy_three_quarters_of_the_cabinet_runs() -> None:
    """41 живая кампания из 55 идёт на «Максимальное количество».

    Замок на COST_CAP означал, что наш создатель не воспроизводит три четверти
    того, что в кабинетах уже работает (замер 17.08 по трём кабинетам).
    """
    from core.campaign_drafts.contracts import CampaignDraftGoal

    assert (
        CampaignDraftGoal(bid_strategy="LOWEST_COST_WITHOUT_CAP").bid_strategy
        == "LOWEST_COST_WITHOUT_CAP"
    )


def test_uncapped_strategy_does_not_demand_a_bid() -> None:
    """`bid_amount` — поле кэпа. Требовать его у стратегии без кэпа значит
    закрыть её замком, который сам же и придумал."""
    from core.campaign_drafts.contracts import CampaignDraftGoal

    assert CampaignDraftGoal(bid_strategy="LOWEST_COST_WITHOUT_CAP", bid_amount="").bid_amount == ""


def test_draft_still_rejects_a_strategy_meta_does_not_have() -> None:
    """Список расширяется правкой кода осознанно: справочник у Meta мы не
    читаем, и молча появиться в контракте ничего не должно."""
    from core.campaign_drafts.contracts import CampaignDraftGoal

    with pytest.raises(ValidationError):
        CampaignDraftGoal(bid_strategy="TARGET_COST")
