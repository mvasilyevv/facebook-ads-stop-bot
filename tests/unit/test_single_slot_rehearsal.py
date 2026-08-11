from __future__ import annotations

import json

import pytest

from core.adoption.bundle import parse_adoption_bundle_json
from tests.rehearsal.single_slot import (
    RehearsalError,
    _require_ci_acknowledgement,
    build_adoption_bundle,
)


def test_rehearsal_adoption_fixture_uses_one_monitored_usd_cabinet() -> None:
    parsed = parse_adoption_bundle_json(json.dumps(build_adoption_bundle()))

    assert parsed.entity_counts["accounts"] == 1
    assert parsed.entity_counts["recipients"] == 1
    assert parsed.entity_counts["system_settings"] == 1
    assert parsed.sections.offer_rules[0].currency == "USD"
    assert parsed.sections.recipients[0].role == "owner"
    assert parsed.sections.observer_settings is not None
    assert parsed.sections.observer_settings.campaign_ids == ["9001"]


def test_rehearsal_refuses_non_ephemeral_or_unacknowledged_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("FB_AGENT_REHEARSAL_ACK", raising=False)
    with pytest.raises(RehearsalError, match="ephemeral Actions host"):
        _require_ci_acknowledgement()

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(RehearsalError, match="ACK"):
        _require_ci_acknowledgement()

    monkeypatch.setenv("FB_AGENT_REHEARSAL_ACK", "single-slot")
    _require_ci_acknowledgement()
