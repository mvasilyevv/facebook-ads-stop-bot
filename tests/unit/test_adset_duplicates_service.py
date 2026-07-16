# -*- coding: utf-8 -*-
"""Unit: caps, budget, naming, schedule и serialization adset duplicate."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.adset_duplicates import (
    AdsetDuplicateLaunchIn,
    AdsetDuplicatePreviewIn,
)
from core.adset_duplicates.service import (
    AccountMetadata,
    AdsetDuplicateError,
    DuplicateSource,
    DuplicateTask,
    SourceAd,
    build_duplicate_preview,
    build_schedule,
    calculate_budget,
    fetch_account_metadata,
    generate_names,
    resolve_duplicate_source_hierarchy,
    serialize_duplicate_task,
    validate_structure_caps,
)


def _source() -> DuplicateSource:
    return DuplicateSource(
        account_id="act_100",
        campaign_id="200",
        campaign_name="Source Campaign",
        adset_id="300",
        adset_name="Source Adset",
        source_ad_id="401",
        source_ad_name="Origin",
        ads=(
            SourceAd("401", "Origin", "ACTIVE", "https://img/1"),
            SourceAd("402", "Sibling", "PAUSED", "https://img/2"),
            SourceAd("403", "Not selected", "ACTIVE", None),
        ),
        selected_ad_ids=("401", "402"),
        source_daily_budget_cents=1000,
    )


def test_structure_caps_and_total_objects() -> None:
    assert validate_structure_caps(3, 2, 1) == (6, 6, 15)
    with pytest.raises(AdsetDuplicateError, match="максимум 50"):
        validate_structure_caps(5, 10, 2)
    with pytest.raises(AdsetDuplicateError, match="максимум 10"):
        validate_structure_caps(1, 1, 11)


def test_budget_math_differs_for_abo_and_cbo() -> None:
    abo = calculate_budget(
        budget_level="ABO",
        daily_budget_cents=1500,
        campaign_count=3,
        total_adsets=6,
        currency="EUR",
    )
    cbo = calculate_budget(
        budget_level="CBO",
        daily_budget_cents=1500,
        campaign_count=3,
        total_adsets=6,
        currency="EUR",
    )
    assert abo["total_daily_budget_cents"] == 9000
    assert cbo["total_daily_budget_cents"] == 4500


def test_schedule_defaults_to_tomorrow_in_app_timezone() -> None:
    schedule = build_schedule(
        requested_start_date=None,
        timezone_name="Europe/Kaliningrad",
        timezone_offset_hours=2,
        now=datetime(2026, 7, 15, 22, 30, tzinfo=UTC),
    )
    # 22:30 UTC = уже 16 июля локально; default = локальное завтра, 17 июля.
    assert schedule == {
        "timezone_name": "Europe/Kaliningrad",
        "offset": "+02:00",
        "start_time_utc": "2026-07-16T22:00:00Z",
        "start_time_local": "2026-07-17T00:00:00+02:00",
    }


def test_schedule_uses_zoneinfo_offset_at_future_dst_date() -> None:
    schedule = build_schedule(
        requested_start_date=date(2026, 3, 9),
        timezone_name="America/New_York",
        # Current Meta offset before DST; target date is after transition.
        timezone_offset_hours=-5,
        now=datetime(2026, 3, 7, 12, tzinfo=UTC),
    )
    assert schedule["offset"] == "-04:00"
    assert schedule["start_time_local"] == "2026-03-09T00:00:00-04:00"
    assert schedule["start_time_utc"] == "2026-03-09T04:00:00Z"


def test_schedule_fallback_supports_half_hour_offset() -> None:
    schedule = build_schedule(
        requested_start_date=date(2026, 7, 17),
        timezone_name="",
        timezone_offset_hours=5.5,
        now=datetime(2026, 7, 15, 12, tzinfo=UTC),
    )
    assert schedule["timezone_name"] == "UTC+05:30"
    assert schedule["offset"] == "+05:30"
    assert schedule["start_time_local"] == "2026-07-17T00:00:00+05:30"
    assert schedule["start_time_utc"] == "2026-07-16T18:30:00Z"


def test_schedule_rejects_today_midnight_that_already_passed() -> None:
    with pytest.raises(AdsetDuplicateError, match="будущем"):
        build_schedule(
            requested_start_date=date(2026, 7, 15),
            timezone_name="Europe/Kaliningrad",
            timezone_offset_hours=2,
            now=datetime(2026, 7, 15, 10, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_account_metadata_requires_exact_graph_fields_and_keeps_float_offset() -> None:
    client = AsyncMock()
    client.execute_graph_call.return_value = {
        "id": "123",
        "name": "India account",
        "currency": "inr",
        "timezone_name": "Asia/Kolkata",
        "timezone_offset_hours_utc": 5.5,
    }
    metadata = await fetch_account_metadata(client, "act_123")
    assert metadata == AccountMetadata(
        id="act_123",
        name="India account",
        currency="INR",
        timezone_name="Asia/Kolkata",
        timezone_offset_hours=5.5,
    )
    client.execute_graph_call.assert_awaited_once_with(
        method="GET",
        endpoint="/act_123",
        query_params={"fields": "id,name,currency,timezone_name,timezone_offset_hours_utc"},
        ad_account_id="act_123",
    )


@pytest.mark.asyncio
async def test_missing_local_adset_id_is_hydrated_from_read_only_source_ad() -> None:
    source = replace(_source(), adset_id="")
    client = AsyncMock()
    client.execute_graph_call.return_value = {
        "id": source.source_ad_id,
        "account_id": "100",
        "campaign_id": "200",
        "adset_id": "300",
    }

    resolved = await resolve_duplicate_source_hierarchy(client, source)

    assert resolved == _source()
    client.execute_graph_call.assert_awaited_once_with(
        method="GET",
        endpoint="/401",
        query_params={"fields": "id,account_id,campaign_id,adset_id"},
        ad_account_id="100",
    )


@pytest.mark.asyncio
async def test_hierarchy_hydration_can_recover_all_missing_local_ids() -> None:
    source = replace(_source(), account_id="", campaign_id="", adset_id="")
    client = AsyncMock()
    client.execute_graph_call.return_value = {
        "id": source.source_ad_id,
        "account_id": "100",
        "campaign_id": "200",
        "adset_id": "300",
    }

    resolved = await resolve_duplicate_source_hierarchy(client, source)

    assert resolved == _source()
    assert client.execute_graph_call.await_args.kwargs["ad_account_id"] is None


@pytest.mark.asyncio
async def test_hierarchy_hydration_fails_closed_on_local_meta_conflict() -> None:
    source = replace(_source(), campaign_id="999", adset_id="")
    client = AsyncMock()
    client.execute_graph_call.return_value = {
        "id": source.source_ad_id,
        "account_id": "100",
        "campaign_id": "200",
        "adset_id": "300",
    }

    with pytest.raises(AdsetDuplicateError, match="кампании не совпадает") as exc_info:
        await resolve_duplicate_source_hierarchy(client, source)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_hierarchy_hydration_rejects_incomplete_graph_response() -> None:
    source = replace(_source(), adset_id="")
    client = AsyncMock()
    client.execute_graph_call.return_value = {
        "id": source.source_ad_id,
        "account_id": "100",
        "campaign_id": "200",
    }

    with pytest.raises(AdsetDuplicateError, match="Meta не вернула полный hierarchy"):
        await resolve_duplicate_source_hierarchy(client, source)


def test_name_generation_is_unique_and_bounded() -> None:
    names = generate_names(
        campaign_name_base="X" * 500,
        adset_name_base="Adset",
        campaign_count=2,
        adsets_per_campaign=2,
        start_date=date(2026, 7, 16),
    )
    assert len(set(names["campaigns"])) == 2
    assert len(set(names["adsets"])) == 4
    assert all(len(name) <= 400 for name in names["campaigns"] + names["adsets"])


def test_preview_uses_all_source_ads_but_worker_payload_only_selection() -> None:
    preview, params = build_duplicate_preview(
        source=_source(),
        account=AccountMetadata(
            id="act_100",
            name="Account",
            currency="EUR",
            timezone_name="Europe/Kaliningrad",
            timezone_offset_hours=2,
        ),
        campaign_count=2,
        adsets_per_campaign=2,
        budget_level="ABO",
        daily_budget_cents=1500,
        requested_start_date=date(2026, 7, 17),
        campaign_name_base=None,
        adset_name_base=None,
        now=datetime(2026, 7, 15, 10, tzinfo=UTC),
    )
    assert preview["format_code"] == "2-2-2"
    assert preview["counts"] == {"campaigns": 2, "adsets": 4, "ads": 8, "total_objects": 14}
    assert [ad["fb_ad_id"] for ad in preview["source"]["ads"]] == ["401", "402", "403"]
    assert params["selected_ad_ids"] == ["401", "402"]
    assert params["start_time"] == "2026-07-16T22:00:00Z"
    assert len(params["campaign_names"]) == 2
    assert len(params["adset_names"]) == 4


def test_preview_appends_first_owner_tag_to_generated_campaign_names() -> None:
    preview, params = build_duplicate_preview(
        source=_source(),
        account=AccountMetadata(
            id="act_100",
            name="Account",
            currency="EUR",
            timezone_name="Europe/Kaliningrad",
            timezone_offset_hours=2,
        ),
        campaign_count=2,
        adsets_per_campaign=1,
        budget_level="ABO",
        daily_budget_cents=1500,
        requested_start_date=date(2026, 7, 17),
        campaign_name_base="Editable campaign",
        adset_name_base=None,
        owner_tag="MV, ABC",
        now=datetime(2026, 7, 15, 10, tzinfo=UTC),
    )

    assert all("Editable campaign | MV | DUP" in name for name in params["campaign_names"])
    assert any("Owner-tag 'MV' добавлен" in warning for warning in preview["warnings"])


def test_preview_keeps_existing_owner_tag_without_append_warning() -> None:
    preview, params = build_duplicate_preview(
        source=_source(),
        account=AccountMetadata(
            id="act_100",
            name="Account",
            currency="EUR",
            timezone_name="Europe/Kaliningrad",
            timezone_offset_hours=2,
        ),
        campaign_count=1,
        adsets_per_campaign=1,
        budget_level="ABO",
        daily_budget_cents=1500,
        requested_start_date=date(2026, 7, 17),
        campaign_name_base="MV | Editable campaign",
        adset_name_base=None,
        owner_tag="MV, ABC",
        now=datetime(2026, 7, 15, 10, tzinfo=UTC),
    )

    assert params["campaign_names"] == ["MV | Editable campaign | DUP 17.07 C1"]
    assert not any("Owner-tag" in warning for warning in preview["warnings"])


def test_request_schema_caps_and_draft_body_are_strict() -> None:
    base = {
        "source_ad_id": "1",
        "selected_ad_ids": [str(i) for i in range(10, 21)],
        "campaign_count": 1,
        "adsets_per_campaign": 1,
        "budget_level": "ABO",
        "daily_budget_cents": 100,
        "idempotency_token": "token-1",
    }
    with pytest.raises(ValidationError):
        AdsetDuplicatePreviewIn.model_validate(base)
    with pytest.raises(ValidationError):
        AdsetDuplicateLaunchIn.model_validate(
            {"preview_token": "x" * 32, "requested_by": "spoofed"}
        )


def test_status_serialization_keeps_lowercase_and_created_ids() -> None:
    task = DuplicateTask(
        id=77,
        status="succeeded",
        payload={"params": {"counts": {"total_objects": 15}}},
        result={"created_ids": {"campaigns": ["900"]}},
        attempt_count=1,
        max_attempts=1,
        last_error=None,
        created_at=datetime(2026, 7, 15, tzinfo=UTC),
        updated_at=datetime(2026, 7, 15, tzinfo=UTC),
        completed_at=datetime(2026, 7, 15, tzinfo=UTC),
    )
    out = serialize_duplicate_task(task)
    assert out["status"] == "succeeded"
    assert out["progress"]["completed"] == 15
    assert out["created_meta_ids"] == {"campaigns": ["900"]}
    assert out["error"] is None


@pytest.mark.parametrize("status", ["running", "retrying", "failed"])
def test_status_serialization_exposes_checkpoint_phase_and_created_count(status: str) -> None:
    task = DuplicateTask(
        id=78,
        status=status,
        payload={"params": {"counts": {"total_objects": 7}}},
        result={
            "checkpoint_type": "duplicate_adset_structure",
            "phase": "recovery_pending" if status != "failed" else "recovery_paused",
            "recovery_requested": True,
            "created_ids": {
                "campaigns": ["900"],
                "adsets": ["901", "902"],
                "ads": ["903", "904"],
            },
        },
        attempt_count=0,
        max_attempts=1,
        last_error="worker crashed" if status == "failed" else None,
        created_at=datetime(2026, 7, 15, tzinfo=UTC),
        updated_at=datetime(2026, 7, 15, tzinfo=UTC),
        completed_at=datetime(2026, 7, 15, tzinfo=UTC) if status == "failed" else None,
    )

    out = serialize_duplicate_task(task)

    assert out["progress"]["phase"] == task.result["phase"]
    assert out["progress"]["completed"] == 5
    assert out["progress"]["total"] == 7
    assert out["created_meta_ids"]["adsets"] == ["901", "902"]
    assert "Crash-recovery" in out["progress"]["message"] or status == "failed"
