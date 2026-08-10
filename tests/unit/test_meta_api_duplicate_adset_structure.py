"""Unit contract for the selective 3-2-1 ad-set duplication executor.

The fake Graph client intentionally records exact request bodies. This pins the
chosen Marketing API shape while the production transport remains the existing
generic ``execute_graph_call`` convention:

* ``POST /act_<id>/campaigns`` with a JSON PAUSED campaign body;
* ``POST /<source_adset>/copies`` with ``deep_copy=false`` and target campaign;
* ``POST /act_<id>/ads`` with the existing ``creative_id`` only;
* every created campaign/ad set/ad remains PAUSED for manual review.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.adset_duplicates.plan_integrity import duplicate_execution_plan_digest
from core.adset_duplicates.service import _ready_task_payload
from core.meta_api.errors import MutationValidationError, TemporaryError
from core.meta_api.mutations.duplicate_adset_structure import (
    DuplicateAdsetStructureHandler,
    DuplicateAdsetStructurePartialError,
)
from core.meta_api.schemas import MetaMutationPayload


def _params(**overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "source_campaign_id": "101",
        "source_adset_id": "201",
        "selected_ad_ids": ["301"],
        "campaign_count": 3,
        "adsets_per_campaign": 2,
        "budget_level": "ABO",
        "daily_budget": "50.00",
        "currency": "USD",
        "currency_exponent": 2,
        "start_time": "2099-07-16T08:00:00Z",
        "campaign_names": ["CR copy 1", "CR copy 2", "CR copy 3"],
        "adset_names": [
            ["CR1 AS1", "CR1 AS2"],
            ["CR2 AS1", "CR2 AS2"],
            ["CR3 AS1", "CR3 AS2"],
        ],
    }
    params.update(overrides)
    return params


def _payload(**overrides: Any) -> MetaMutationPayload:
    params = _params(**overrides)
    plan_digest = duplicate_execution_plan_digest(
        mutation_kind="duplicate_adset_structure",
        target_id="201",
        params=params,
        ad_account_id="999",
    )
    params["plan_digest"] = plan_digest.hex()
    return MetaMutationPayload(
        mutation_kind="duplicate_adset_structure",
        target_id="201",
        params=params,
        ad_account_id="act_999",
    )


def _tamper_payload(payload: MetaMutationPayload, case: str) -> MetaMutationPayload:
    params = deepcopy(payload.params)
    target_id = payload.target_id
    ad_account_id = payload.ad_account_id
    if case == "money":
        params["daily_budget"] = "75.00"
    elif case == "start":
        params["start_time"] = "2099-07-17T08:00:00Z"
    elif case == "account":
        ad_account_id = "998"
    elif case == "name":
        params["campaign_names"][0] = "tampered campaign"
    elif case == "selection":
        params["selected_ad_ids"] = ["302"]
    elif case == "target":
        target_id = "202"
    elif case == "digest_missing":
        params.pop("plan_digest")
    elif case == "digest_malformed":
        params["plan_digest"] = "not-a-sha256"
    elif case == "digest_mismatch":
        params["plan_digest"] = "0" * 64
    else:  # pragma: no cover - test helper contract
        raise AssertionError(f"unknown tamper case: {case}")
    return MetaMutationPayload(
        mutation_kind=payload.mutation_kind,
        target_id=target_id,
        params=params,
        ad_account_id=ad_account_id,
    )


class FakeGraphClient:
    def __init__(self, *, fail_ad_create_number: int | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_ad_create_number = fail_ad_create_number
        self.campaign_seq = 0
        self.adset_seq = 0
        self.ad_seq = 0
        self.adset_campaign: dict[str, str] = {}
        self.adset_start: dict[str, str] = {}
        self.adset_budget: dict[str, int | None] = {}
        self.ads_by_adset: dict[str, list[tuple[str, str]]] = {}
        self.campaign_budget: dict[str, int | None] = {}
        self.campaign_name: dict[str, str] = {}
        self.campaign_objective: dict[str, str] = {}
        self.campaign_status: dict[str, str] = {}
        self.adset_status: dict[str, str] = {}
        self.ad_status: dict[str, str] = {}
        self.ad_parent: dict[str, str] = {}
        self.ad_name: dict[str, str] = {}
        self.ad_creative: dict[str, str] = {}

    async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        method = kwargs["method"]
        endpoint = kwargs["endpoint"]
        body = kwargs.get("body_json") or {}

        if method == "GET" and endpoint == "/101":
            return {
                "id": "101",
                "account_id": "999",
                "objective": "OUTCOME_LEADS",
                "special_ad_categories": ["NONE"],
                "buying_type": "AUCTION",
                "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            }
        if method == "GET" and endpoint == "/201":
            return {
                "id": "201",
                "account_id": "999",
                "campaign_id": "101",
                "name": "source adset",
            }
        if method == "GET" and endpoint in {"/301", "/302"}:
            creative = "401" if endpoint == "/301" else "402"
            return {
                "id": endpoint[1:],
                "account_id": "999",
                "campaign_id": "101",
                "adset_id": "201",
                "name": f"source ad {endpoint[1:]}",
                "creative": {"id": creative},
            }

        if method == "POST" and endpoint == "/act_999/campaigns":
            self.campaign_seq += 1
            object_id = str(1000 + self.campaign_seq)
            self.campaign_budget[object_id] = body.get("daily_budget")
            self.campaign_name[object_id] = str(body["name"])
            self.campaign_objective[object_id] = str(body["objective"])
            self.campaign_status[object_id] = "PAUSED"
            return {"id": object_id}
        if method == "POST" and endpoint == "/201/copies":
            self.adset_seq += 1
            object_id = str(2000 + self.adset_seq)
            self.adset_campaign[object_id] = str(body["campaign_id"])
            self.ads_by_adset[object_id] = []
            self.adset_status[object_id] = "PAUSED"
            return {"copied_adset_id": object_id}
        if method == "POST" and endpoint == "/act_999/ads":
            self.ad_seq += 1
            if self.ad_seq == self.fail_ad_create_number:
                raise TemporaryError("mid-flight ad create failure")
            object_id = str(3000 + self.ad_seq)
            self.ad_status[object_id] = "PAUSED"
            self.ad_parent[object_id] = str(body["adset_id"])
            self.ad_name[object_id] = str(body["name"])
            self.ad_creative[object_id] = str(body["creative"]["creative_id"])
            self.ads_by_adset[str(body["adset_id"])].append(
                (object_id, str(body["creative"]["creative_id"]))
            )
            return {"id": object_id}

        object_id = endpoint[1:]
        if method == "POST" and object_id in self.adset_campaign and "name" in body:
            self.adset_start[object_id] = str(body["start_time"])
            self.adset_budget[object_id] = body.get("daily_budget")
            return {"success": True}
        if method == "POST" and body.get("status") == "PAUSED":
            if object_id in self.campaign_status:
                self.campaign_status[object_id] = body["status"]
            elif object_id in self.adset_status:
                self.adset_status[object_id] = body["status"]
            elif object_id in self.ad_status:
                self.ad_status[object_id] = body["status"]
            else:
                raise AssertionError(f"status update for unknown object: {object_id}")
            return {"success": True}

        if method == "GET" and object_id in self.campaign_budget:
            row: dict[str, Any] = {
                "id": object_id,
                "account_id": "999",
                "name": self.campaign_name[object_id],
                "objective": self.campaign_objective[object_id],
                "status": self.campaign_status[object_id],
            }
            if self.campaign_budget[object_id] is not None:
                row["daily_budget"] = str(self.campaign_budget[object_id])
            return row
        if method == "GET" and object_id in self.adset_campaign:
            row = {
                "id": object_id,
                "account_id": "999",
                "campaign_id": self.adset_campaign[object_id],
                "status": self.adset_status[object_id],
            }
            if object_id in self.adset_start:
                row["start_time"] = self.adset_start[object_id]
            if self.adset_budget.get(object_id) not in (None, 0):
                row["daily_budget"] = str(self.adset_budget[object_id])
            return row
        if method == "GET" and object_id in self.ad_status:
            adset_id = self.ad_parent[object_id]
            return {
                "id": object_id,
                "account_id": "999",
                "campaign_id": self.adset_campaign[adset_id],
                "adset_id": adset_id,
                "name": self.ad_name[object_id],
                "status": self.ad_status[object_id],
                "creative": {"id": self.ad_creative[object_id]},
            }
        if method == "GET" and endpoint.endswith("/ads"):
            adset_id = endpoint.split("/")[1]
            return {
                "data": [
                    {
                        "id": ad_id,
                        "adset_id": adset_id,
                        "status": self.ad_status[ad_id],
                        "creative": {"id": creative_id},
                    }
                    for ad_id, creative_id in self.ads_by_adset[adset_id]
                ]
            }
        raise AssertionError(f"unexpected Graph call: {kwargs!r}")


class FaultInjectingGraphClient(FakeGraphClient):
    """Fail exactly one executor boundary and retain the call index for assertions."""

    def __init__(self, stage: str) -> None:
        super().__init__()
        self.stage = stage
        self.injected = False
        self.fault_call_index: int | None = None

    @staticmethod
    def _is_stage_call(stage: str, kwargs: dict[str, Any]) -> bool:
        endpoint = kwargs["endpoint"]
        method = kwargs["method"]
        body = kwargs.get("body_json") or {}
        return {
            "create_campaign": method == "POST" and endpoint == "/act_999/campaigns",
            "copy_adset": method == "POST" and endpoint == "/201/copies",
            "configure_adset": method == "POST" and endpoint == "/2001" and "name" in body,
            "create_ad": method == "POST" and endpoint == "/act_999/ads",
            "verify": (
                method == "GET"
                and endpoint == "/1001"
                and kwargs.get("query_params", {}).get("fields") == "id,status,daily_budget"
            ),
        }[stage]

    async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
        if not self.injected and self._is_stage_call(self.stage, kwargs):
            self.injected = True
            if self.stage == "create_campaign":
                # Meta committed the campaign, but the response carrying its ID
                # was lost. The executor cannot safely guess that unknown ID.
                await super().execute_graph_call(**kwargs)
                self.fault_call_index = len(self.calls) - 1
            else:
                # Record the attempted boundary without applying FakeGraphClient's
                # successful state transition.
                self.calls.append(kwargs)
                self.fault_call_index = len(self.calls) - 1
            raise TemporaryError(f"fault injected at {self.stage}")
        return await super().execute_graph_call(**kwargs)


@pytest.mark.asyncio
async def test_321_selective_duplicate_remains_all_paused() -> None:
    client = FakeGraphClient()

    result = await DuplicateAdsetStructureHandler().execute(client, _payload())

    assert result["success"] is True
    assert result["campaign_count"] == 3
    assert result["adset_count"] == 6
    assert result["ad_count"] == 6
    assert result["creation_policy"] == "all_paused"

    campaign_calls = [c for c in client.calls if c["endpoint"] == "/act_999/campaigns"]
    assert len(campaign_calls) == 3
    assert all(c["body_json"]["status"] == "PAUSED" for c in campaign_calls)
    assert all("daily_budget" not in c["body_json"] for c in campaign_calls)

    copy_calls = [c for c in client.calls if c["endpoint"] == "/201/copies"]
    assert len(copy_calls) == 6
    assert all(
        c["body_json"]
        == {
            "campaign_id": c["body_json"]["campaign_id"],
            "deep_copy": False,
            "status_option": "PAUSED",
        }
        for c in copy_calls
    )

    ad_calls = [c for c in client.calls if c["endpoint"] == "/act_999/ads"]
    assert len(ad_calls) == 6
    assert all(c["body_json"]["creative"] == {"creative_id": "401"} for c in ad_calls)
    assert all(c["body_json"]["status"] == "PAUSED" for c in ad_calls)

    active_calls = [
        c
        for c in client.calls
        if c["method"] == "POST" and (c.get("body_json") or {}).get("status") == "ACTIVE"
    ]
    assert active_calls == []
    assert set(client.campaign_status.values()) == {"PAUSED"}
    assert set(client.adset_status.values()) == {"PAUSED"}
    assert set(client.ad_status.values()) == {"PAUSED"}


@pytest.mark.asyncio
async def test_cbo_budget_is_only_on_campaign() -> None:
    client = FakeGraphClient()
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        budget_level="CBO",
        campaign_names=["CBO copy"],
        adset_names=["CBO adset"],
    )

    result = await DuplicateAdsetStructureHandler().execute(client, payload)

    assert result["success"] is True
    campaign_call = next(c for c in client.calls if c["endpoint"] == "/act_999/campaigns")
    assert campaign_call["body_json"]["daily_budget"] == 5000
    configure_call = next(
        c for c in client.calls if c["endpoint"] == "/2001" and "name" in (c.get("body_json") or {})
    )
    assert "daily_budget" not in configure_call["body_json"]
    assert "lifetime_budget" not in configure_call["body_json"]


@pytest.mark.asyncio
async def test_nested_copy_response_cannot_be_used_to_configure_the_source_adset() -> None:
    class NestedSourceIdClient(FakeGraphClient):
        async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
            response = await super().execute_graph_call(**kwargs)
            if kwargs["method"] == "POST" and kwargs["endpoint"] == "/201/copies":
                return {"data": [{"id": "201"}]}
            return response

    client = NestedSourceIdClient()
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["safe campaign"],
        adset_names=["safe adset"],
    )

    with pytest.raises(DuplicateAdsetStructurePartialError, match="schema is not exact") as exc:
        await DuplicateAdsetStructureHandler().execute(client, payload)

    assert exc.value.created_ids == {
        "campaigns": ["1001"],
        "adsets": [],
        "ads": [],
    }
    assert not any(call["method"] == "POST" and call["endpoint"] == "/201" for call in client.calls)
    assert not any(call["endpoint"] == "/act_999/ads" for call in client.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "colliding_id",
    ["101", "201", "301", "401", "1001"],
    ids=[
        "source-campaign",
        "source-adset",
        "source-ad",
        "source-creative",
        "created-campaign",
    ],
)
async def test_copy_response_rejects_source_and_cross_type_id_collisions(
    colliding_id: str,
) -> None:
    class CollidingCopyClient(FakeGraphClient):
        async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
            response = await super().execute_graph_call(**kwargs)
            if kwargs["method"] == "POST" and kwargs["endpoint"] == "/201/copies":
                return {"copied_adset_id": colliding_id}
            return response

    client = CollidingCopyClient()
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["safe campaign"],
        adset_names=["safe adset"],
    )

    with pytest.raises(DuplicateAdsetStructurePartialError, match="collides") as exc:
        await DuplicateAdsetStructureHandler().execute(client, payload)

    assert exc.value.created_ids == {
        "campaigns": ["1001"],
        "adsets": [],
        "ads": [],
    }
    assert not any(
        call["method"] == "POST"
        and call["endpoint"] == f"/{colliding_id}"
        and "name" in (call.get("body_json") or {})
        for call in client.calls
    )


@pytest.mark.asyncio
async def test_wrong_parent_copy_proof_stops_before_configuration() -> None:
    class WrongParentProofClient(FakeGraphClient):
        async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
            response = await super().execute_graph_call(**kwargs)
            if (
                kwargs["method"] == "GET"
                and kwargs["endpoint"] == "/2001"
                and kwargs.get("query_params", {}).get("fields")
                == "id,account_id,campaign_id,status"
            ):
                return {**response, "campaign_id": "101"}
            return response

    client = WrongParentProofClient()
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["safe campaign"],
        adset_names=["safe adset"],
    )

    with pytest.raises(DuplicateAdsetStructurePartialError, match="parent mismatch") as exc:
        await DuplicateAdsetStructureHandler().execute(client, payload)

    assert exc.value.created_ids == {
        "campaigns": ["1001"],
        "adsets": [],
        "ads": [],
    }
    assert not any(
        call["method"] == "POST"
        and call["endpoint"] == "/2001"
        and "name" in (call.get("body_json") or {})
        for call in client.calls
    )


@pytest.mark.asyncio
async def test_cbo_refuses_completion_if_copy_retains_adset_budget() -> None:
    class RetainedBudgetClient(FakeGraphClient):
        async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
            response = await super().execute_graph_call(**kwargs)
            if (
                kwargs["method"] == "GET"
                and kwargs["endpoint"] == "/2001"
                and kwargs.get("query_params", {}).get("fields")
                == "id,campaign_id,status,daily_budget,lifetime_budget,start_time"
            ):
                response["daily_budget"] = "777"
            return response

    client = RetainedBudgetClient()
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        budget_level="CBO",
        campaign_names=["CBO copy"],
        adset_names=["CBO adset"],
    )

    with pytest.raises(DuplicateAdsetStructurePartialError, match="retained") as exc_info:
        await DuplicateAdsetStructureHandler().execute(client, payload)

    assert exc_info.value.created_ids == {
        "campaigns": ["1001"],
        "adsets": ["2001"],
        "ads": ["3001"],
    }
    assert not any(
        call["method"] == "POST" and (call.get("body_json") or {}).get("status") == "ACTIVE"
        for call in client.calls
    )


@pytest.mark.asyncio
async def test_failure_pauses_every_known_created_object_and_raises_partial() -> None:
    client = FakeGraphClient(fail_ad_create_number=2)
    payload = _payload(
        selected_ad_ids=["301", "302"],
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["copy"],
        adset_names=["copy adset"],
    )

    with pytest.raises(DuplicateAdsetStructurePartialError) as exc_info:
        await DuplicateAdsetStructureHandler().execute(client, payload)

    exc = exc_info.value
    assert exc.created_ids == {
        "campaigns": ["1001"],
        "adsets": ["2001"],
        "ads": ["3001"],
    }
    pause_ids = {
        c["endpoint"][1:]
        for c in client.calls
        if c["method"] == "POST" and (c.get("body_json") or {}).get("status") == "PAUSED"
    }
    assert {"1001", "2001", "3001"}.issubset(pause_ids)
    assert exc.failed_steps[0]["step"].startswith("create_ad")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stage", "step_prefix", "expected_created"),
    [
        (
            "create_campaign",
            "create_campaign",
            {"campaigns": [], "adsets": [], "ads": []},
        ),
        (
            "copy_adset",
            "copy_adset",
            {"campaigns": ["1001"], "adsets": [], "ads": []},
        ),
        (
            "configure_adset",
            "configure_adset",
            {"campaigns": ["1001"], "adsets": ["2001"], "ads": []},
        ),
        (
            "create_ad",
            "create_ad",
            {"campaigns": ["1001"], "adsets": ["2001"], "ads": []},
        ),
        (
            "verify",
            "verify_paused_structure",
            {"campaigns": ["1001"], "adsets": ["2001"], "ads": ["3001"]},
        ),
    ],
    ids=[
        "create-campaign-lost-response",
        "copy-adset",
        "configure-adset",
        "create-ad",
        "verify",
    ],
)
async def test_fault_matrix_pauses_all_known_ids_and_stops_creation(
    stage: str,
    step_prefix: str,
    expected_created: dict[str, list[str]],
) -> None:
    client = FaultInjectingGraphClient(stage)
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["fault campaign"],
        adset_names=["fault adset"],
    )

    with pytest.raises(DuplicateAdsetStructurePartialError) as exc_info:
        await DuplicateAdsetStructureHandler().execute(client, payload)

    exc = exc_info.value
    assert client.injected is True
    assert client.fault_call_index is not None
    assert exc.created_ids == expected_created
    assert exc.failed_steps[0]["step"].startswith(step_prefix)
    assert exc.cleanup_failures == []

    calls_after_fault = client.calls[client.fault_call_index + 1 :]
    known_ids = {object_id for object_ids in expected_created.values() for object_id in object_ids}
    cleanup_pause_ids = {
        call["endpoint"][1:]
        for call in calls_after_fault
        if call["method"] == "POST" and (call.get("body_json") or {}).get("status") == "PAUSED"
    }
    assert cleanup_pause_ids == known_ids


@pytest.mark.asyncio
async def test_progress_checkpoint_ends_at_verified_paused() -> None:
    client = FakeGraphClient()
    checkpoints: list[dict[str, Any]] = []
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["checkpoint campaign"],
        adset_names=["checkpoint adset"],
    )

    async def save_checkpoint(checkpoint: dict[str, Any]) -> None:
        checkpoints.append(checkpoint)

    await DuplicateAdsetStructureHandler().execute(
        client,
        payload,
        progress_callback=save_checkpoint,
    )

    steps = [checkpoint["step"] for checkpoint in checkpoints]
    assert steps[:3] == [
        "campaign_created[0]",
        "adset_created[0,0]",
        "ad_created[0,0,301]",
    ]
    assert steps[-2:] == [
        "verify_paused_structure",
        "verify_paused_structure_complete",
    ]
    assert checkpoints[-1]["created_ids"] == {
        "campaigns": ["1001"],
        "adsets": ["2001"],
        "ads": ["3001"],
    }
    assert checkpoints[-1]["phase"] == "verified_paused"
    assert checkpoints[-1]["checkpoint_version"] == 2
    assert "activated_ids" not in checkpoints[-1]


@pytest.mark.asyncio
async def test_cancelled_verification_runs_shielded_pause_and_persists_cleanup() -> None:
    class CancelAtVerificationClient(FakeGraphClient):
        cancelled_once = False

        async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
            if (
                not self.cancelled_once
                and kwargs["method"] == "GET"
                and kwargs["endpoint"] == "/1001"
                and kwargs.get("query_params", {}).get("fields") == "id,status,daily_budget"
            ):
                self.cancelled_once = True
                self.calls.append(kwargs)
                raise asyncio.CancelledError
            return await super().execute_graph_call(**kwargs)

    client = CancelAtVerificationClient()
    checkpoints: list[dict[str, Any]] = []
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["cancel campaign"],
        adset_names=["cancel adset"],
    )

    async def save_checkpoint(checkpoint: dict[str, Any]) -> None:
        checkpoints.append(checkpoint)

    with pytest.raises(asyncio.CancelledError):
        await DuplicateAdsetStructureHandler().execute(
            client,
            payload,
            progress_callback=save_checkpoint,
        )

    pause_ids = {
        call["endpoint"][1:]
        for call in client.calls
        if call["method"] == "POST" and (call.get("body_json") or {}).get("status") == "PAUSED"
    }
    assert {"1001", "2001", "3001"}.issubset(pause_ids)
    assert checkpoints[-1]["phase"] == "cancelled_cleanup"
    assert checkpoints[-1]["cleanup_failures"] == []


def test_start_time_must_be_in_the_future(monkeypatch) -> None:
    now = datetime(2099, 7, 16, 7, 0, tzinfo=UTC)
    monkeypatch.setattr(
        DuplicateAdsetStructureHandler,
        "_utcnow",
        staticmethod(lambda: now),
    )

    with pytest.raises(MutationValidationError, match="future"):
        DuplicateAdsetStructureHandler._validate_plan(
            _payload(start_time=now.isoformat().replace("+00:00", "Z"))
        )


def test_near_future_start_is_valid_for_paused_creation(monkeypatch) -> None:
    now = datetime(2099, 7, 16, 7, 0, tzinfo=UTC)
    monkeypatch.setattr(
        DuplicateAdsetStructureHandler,
        "_utcnow",
        staticmethod(lambda: now),
    )
    start_at = now + timedelta(seconds=1)

    plan = DuplicateAdsetStructureHandler._validate_plan(
        _payload(start_time=start_at.isoformat().replace("+00:00", "Z"))
    )
    assert plan.start_at == start_at


def test_service_producer_digest_is_accepted_by_executor() -> None:
    task_params = _params(
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["signed campaign"],
        adset_names=["signed adset"],
    )
    task_payload, plan_digest = _ready_task_payload(
        preview={"source": {"account": {"id": "act_999"}}},
        task_params=task_params,
    )

    plan = DuplicateAdsetStructureHandler._validate_plan(
        MetaMutationPayload.from_dict(task_payload)
    )

    assert task_payload["params"]["plan_digest"] == plan_digest.hex()
    assert plan.account_id == "act_999"
    assert plan.source_adset_id == "201"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "money",
        "start",
        "account",
        "name",
        "selection",
        "target",
        "digest_missing",
        "digest_malformed",
        "digest_mismatch",
    ],
)
async def test_plan_tamper_is_rejected_before_any_graph_call(case: str) -> None:
    payload = _payload(
        campaign_count=1,
        adsets_per_campaign=1,
        campaign_names=["signed campaign"],
        adset_names=["signed adset"],
    )
    client = FakeGraphClient()

    with pytest.raises(MutationValidationError, match="plan_digest"):
        await DuplicateAdsetStructureHandler().execute(
            client,
            _tamper_payload(payload, case),
        )

    assert client.calls == []


@pytest.mark.asyncio
async def test_live_hierarchy_mismatch_stops_before_writes() -> None:
    class WrongHierarchyClient(FakeGraphClient):
        async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
            response = await super().execute_graph_call(**kwargs)
            if kwargs["endpoint"] == "/301":
                response["adset_id"] = "999999"
            return response

    client = WrongHierarchyClient()

    with pytest.raises(MutationValidationError, match="does not belong"):
        await DuplicateAdsetStructureHandler().execute(client, _payload())

    assert not any(call["method"] == "POST" for call in client.calls)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"campaign_count": 6}, "campaign_count"),
        ({"adsets_per_campaign": 11}, "adsets_per_campaign"),
        ({"budget_level": "AUTO"}, "budget_level"),
        ({"daily_budget": "1.50", "currency": "JPY", "currency_exponent": 0}, "daily_budget"),
        ({"daily_budget": "1.500", "currency": "KWD", "currency_exponent": 2}, "daily_budget"),
        ({"daily_budget": "1.00", "currency": "XAU", "currency_exponent": 2}, "daily_budget"),
        ({"daily_budget": None}, "daily_budget"),
        ({"start_time": "2026-07-16T08:00:00+03:00"}, "UTC"),
        ({"selected_ad_ids": []}, "selected_ad_ids"),
        (
            {"selected_ad_ids": [str(300 + index) for index in range(1, 12)]},
            "at most 10",
        ),
        (
            {
                "campaign_count": 5,
                "adsets_per_campaign": 6,
                "selected_ad_ids": ["301", "302"],
                "campaign_names": ["c1", "c2", "c3", "c4", "c5"],
                "adset_names": [f"a{index}" for index in range(30)],
            },
            "must be <= 50",
        ),
        ({"start_time": "2020-01-01T00:00:00Z"}, "future"),
    ],
)
def test_invalid_contract_rejected_before_graph(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(MutationValidationError, match=match):
        DuplicateAdsetStructureHandler._validate_plan(_payload(**overrides))
