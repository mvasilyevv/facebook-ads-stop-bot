"""Pure contracts for fail-closed cabinet autostart execution guards."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.meta_api.bulk as bulk
from core.meta_api.schemas import MetaMutationPayload


def _payload(*, guard: dict | None) -> MetaMutationPayload:
    params = {
        "action": "activate",
        "ad_ids": ["101"],
        "autostart_day": "2026-07-27",
    }
    if guard is not None:
        params["activation_guards"] = {"101": guard}
    return MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="autostart:123:test",
        ad_account_id="123",
        params=params,
    )


@pytest.mark.asyncio
async def test_revalidation_rejects_missing_scheduler_guard(monkeypatch) -> None:
    observed = {"version": 1, "generation": "current"}
    monkeypatch.setattr(
        bulk,
        "capture_autostart_activation_guards",
        AsyncMock(
            return_value=bulk.AutostartActivationGuards(
                guards_by_ad_id={"101": observed},
                rejected_by_ad_id={},
            )
        ),
    )

    decision = await bulk.revalidate_autostart_activation_guards(
        object(),
        payload=_payload(guard=None),
        task_id=7,
    )

    assert decision.guards_by_ad_id == {}
    assert decision.rejected_by_ad_id == {"101": "scheduler_guard_missing"}


@pytest.mark.asyncio
async def test_revalidation_rejects_changed_generation(monkeypatch) -> None:
    monkeypatch.setattr(
        bulk,
        "capture_autostart_activation_guards",
        AsyncMock(
            return_value=bulk.AutostartActivationGuards(
                guards_by_ad_id={"101": {"version": 1, "generation": "new"}},
                rejected_by_ad_id={},
            )
        ),
    )

    decision = await bulk.revalidate_autostart_activation_guards(
        object(),
        payload=_payload(guard={"version": 1, "generation": "old"}),
        task_id=8,
    )

    assert decision.guards_by_ad_id == {}
    assert decision.rejected_by_ad_id == {"101": "scheduler_generation_changed"}


def test_guard_rejections_are_not_projected_as_modified_ids() -> None:
    execution = bulk.GuardedAutostartExecution(
        payload=_payload(guard={"version": 1, "generation": "current"}),
        requested_ad_ids=("101", "102"),
        executable_ad_ids=("101",),
        rejected_by_ad_id={"102": "fsm_state:disabled"},
        external_started=True,
    )

    result = bulk.merge_guarded_bulk_result(
        {
            "success": True,
            "modified_ids": ["101"],
            "succeeded": 1,
            "failed": 0,
            "sub_results": [{"id": "101", "success": True, "code": 200}],
        },
        execution,
    )

    assert result["modified_ids"] == ["101"]
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["guard_rejected"] == {"102": "fsm_state:disabled"}
