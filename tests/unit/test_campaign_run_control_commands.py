from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.commands.campaign_runs import (
    campaign_run_controls,
    campaign_task_state,
    resume_unavailable_reason,
)
from core.tasks.irreversible_control import CreatorTaskControlAbort
from core.tasks.queue import Task


def _internal_config(upload_id: str) -> dict:
    return {
        "account": {
            "act_id": "123",
            "page_id": "100",
            "pixel_id": "200",
            "timezone_name": "Europe/Kaliningrad",
            "currency": "EUR",
            "currency_exponent": 2,
            "account_context_observed_at": "2026-07-29T12:00:00+00:00",
        },
        "offer_code": "GH_CR",
        "destination_link": "https://example.com",
        "start_date": "2026-07-30",
        "budget": {
            "level": "campaign",
            "currency": "EUR",
            "daily_amount": "50.00",
            "bid_strategy": "COST_CAP",
            "bid_amount": "1.50",
        },
        "targeting": {"countries": ["DE"]},
        "campaigns": [
            {
                "key": "static",
                "name": "{offer}",
                "adsets": [{"name": "s1", "dir": "static/a1", "glob": "*"}],
                "concept_refs": ["a.jpg"],
            }
        ],
        "creo_root": upload_id,
    }


def _terminal_task(
    *,
    status: str = "cancelled",
    reason: str = "cancel_requested_before_external_call",
    external_started_at: datetime | None = None,
    outcome: str = "REJECTED",
) -> dict:
    return {
        "task_status": status,
        "task_result": {"outcome": outcome, "reason": reason},
        "external_started_at": external_started_at,
        "cancel_requested_at": datetime.now(UTC),
    }


def test_resume_requires_exact_rejected_pre_external_checkpoint(tmp_path, monkeypatch) -> None:
    upload_dir = tmp_path / "upload-1"
    upload_dir.mkdir()
    (upload_dir / "a.jpg").write_bytes(b"image")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    reason = resume_unavailable_reason(
        run_status="cancelled",
        run_config=_internal_config("upload-1"),
        created_meta_ids={"campaigns": [], "adsets": [], "ads": []},
        task=_terminal_task(),
    )

    assert reason is None


@pytest.mark.parametrize(
    ("task", "created_ids", "expected"),
    [
        (
            _terminal_task(external_started_at=datetime.now(UTC)),
            {},
            "external_boundary_crossed",
        ),
        (
            _terminal_task(outcome="UNKNOWN"),
            {},
            "terminal_outcome_not_rejected",
        ),
        (
            _terminal_task(reason="permanent_pre_external_failure"),
            {},
            "checkpoint_reason_not_resumable",
        ),
        (
            _terminal_task(),
            {"campaigns": ["2385000001"]},
            "created_meta_objects_present",
        ),
    ],
)
def test_resume_fails_closed_for_ambiguous_or_created_state(
    tmp_path,
    monkeypatch,
    task,
    created_ids,
    expected,
) -> None:
    upload_dir = tmp_path / "upload-2"
    upload_dir.mkdir()
    (upload_dir / "a.jpg").write_bytes(b"image")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    assert (
        resume_unavailable_reason(
            run_status="failed",
            run_config=_internal_config("upload-2"),
            created_meta_ids=created_ids,
            task=task,
        )
        == expected
    )


def test_resume_reports_missing_exact_media_checkpoint(tmp_path, monkeypatch) -> None:
    upload_dir = tmp_path / "upload-3"
    upload_dir.mkdir()
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    controls = campaign_run_controls(
        run_status="cancelled",
        run_config=_internal_config("upload-3"),
        created_meta_ids={},
        task=_terminal_task(),
    )

    assert controls.resume.available is False
    assert controls.resume.reason == "media_checkpoint_incomplete"


def test_active_run_exposes_abort_but_never_resume() -> None:
    controls = campaign_run_controls(
        run_status="uploading",
        run_config={},
        created_meta_ids={},
        task={
            "task_status": "running",
            "task_result": {},
            "external_started_at": datetime.now(UTC),
            "cancel_requested_at": None,
        },
    )

    assert controls.abort.available is True
    assert controls.abort.reason == "abort_available"
    assert controls.resume.available is False
    assert controls.resume.reason == "run_not_terminal"


def test_task_state_never_maps_unknown_to_confirmed() -> None:
    assert (
        campaign_task_state(
            status="failed",
            result={"outcome": "UNKNOWN", "reconcile_required": True},
        )
        == "unknown"
    )
    assert campaign_task_state(status="succeeded", result={"outcome": "CONFIRMED"}) == "confirmed"
    assert campaign_task_state(status="succeeded", result={}) == "unknown"
    assert campaign_task_state(status="succeeded", result={"outcome": "REJECTED"}) == "unknown"


def test_abort_confirmation_requires_rejected_terminal_evidence() -> None:
    from core.commands import campaign_runs

    assert (
        campaign_runs._abort_state(  # noqa: SLF001 - fail-closed lifecycle regression
            status="cancelled",
            result={"outcome": "REJECTED"},
        )
        == "confirmed"
    )
    assert campaign_runs._abort_state(status="cancelled", result={}) == "unknown"  # noqa: SLF001
    assert (  # noqa: SLF001
        campaign_runs._abort_state(
            status="cancelled",
            result={"outcome": "UNKNOWN", "reconcile_required": True},
        )
        == "unknown"
    )


def test_openapi_exposes_typed_abort_resume_lifecycle() -> None:
    from apps.api.main import create_app

    paths = create_app().openapi()["paths"]
    assert "/api/tools/campaigns/runs/{run_id}/cancel" not in paths
    for action in ("abort", "resume"):
        operation = paths[f"/api/tools/campaigns/runs/{{run_id}}/{action}"]["post"]
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        assert parameters["Idempotency-Key"]["required"] is True
        assert (
            operation["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/RunCommandOut"
        )
        assert (
            operation["responses"]["409"]["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/ApiProblem"
        )


def _task() -> Task:
    now = datetime.now(UTC)
    return Task(
        id=9001,
        task_type="campaign_create",
        status="running",
        idempotency_key="campaign-9001",
        payload={"run_id": "run-9001"},
        attempt_count=0,
        max_attempts=5,
        requested_by="test",
        last_error=None,
        created_at=now,
        external_started_at=None,
        result=None,
        lane="bulk",
        priority=20,
        available_at=now,
        deadline_at=now + timedelta(minutes=30),
        lease_owner=uuid.uuid4(),
        lease_token=9,
        lease_expires_at=now + timedelta(minutes=30),
        cancel_requested_at=None,
        cancel_reason=None,
        correlation_id=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_progress_rejection_rechecks_cancel_instead_of_stranding_task(
    monkeypatch,
) -> None:
    import apps.campaign_creator_worker.main as worker

    class _CancelledControl:
        external_started = False

        async def check(self) -> None:
            raise CreatorTaskControlAbort(
                "cancel_requested",
                external_started=False,
            )

    async def _execute(*_args, on_progress, **_kwargs):
        await on_progress({"stage": "uniquifying"})

    async def _direct(_control, operation_factory):
        return await operation_factory()

    task = _task()
    monkeypatch.setattr(
        worker,
        "parse_run_config",
        lambda _config: SimpleNamespace(creo_root="upload-9001"),
    )
    monkeypatch.setattr(worker, "resolve_concepts_from_config", lambda _cfg: {})
    monkeypatch.setattr(worker, "build_campaign_spec", lambda _cfg: object())
    monkeypatch.setattr(
        worker,
        "set_run_status",
        AsyncMock(side_effect=[True, False]),
    )
    monkeypatch.setattr(worker, "execute_campaign_spec", _execute)
    monkeypatch.setattr(worker, "run_with_task_control", _direct)
    cancelled = AsyncMock(return_value=True)
    failed = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "finalize_run_cancelled", cancelled)
    monkeypatch.setattr(worker, "finalize_run_failed", failed)

    await worker._execute_run(
        object(),
        task,
        run_id="run-9001",
        config={},
        client=AsyncMock(),
        uploader=AsyncMock(),
        control=_CancelledControl(),
    )

    cancelled.assert_awaited_once()
    failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_abort_after_external_boundary_finalizes_unknown_not_cancelled(
    monkeypatch,
) -> None:
    import apps.campaign_creator_worker.main as worker

    control = SimpleNamespace(external_started=True)
    failed = AsyncMock(return_value=True)
    cancelled = AsyncMock(return_value=True)
    monkeypatch.setattr(worker, "finalize_run_failed", failed)
    monkeypatch.setattr(worker, "finalize_run_cancelled", cancelled)

    await worker._finalize_campaign_control_abort(
        object(),
        _task(),
        control,
        run_id="run-9001",
        exc=CreatorTaskControlAbort(
            "cancel_requested",
            external_started=True,
        ),
    )

    cancelled.assert_not_awaited()
    result = failed.await_args.kwargs["task_result"]
    assert result["outcome"] == "UNKNOWN"
    assert result["reconcile_required"] is True
    assert failed.await_args.kwargs["progress"]["outcome"] == "UNKNOWN"
