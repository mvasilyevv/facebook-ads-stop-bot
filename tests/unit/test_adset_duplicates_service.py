# -*- coding: utf-8 -*-
"""Unit: caps, budget, naming, schedule и serialization adset duplicate."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from apps.api.routers.v1.schemas.adset_duplicates import (
    AdsetDuplicateLaunchIn,
    AdsetDuplicateLaunchOut,
    AdsetDuplicatePreviewIn,
    AdsetDuplicateStatusOut,
)
from core.adset_duplicates.service import (
    AccountMetadata,
    AdsetDuplicateError,
    DuplicateSource,
    DuplicateTask,
    SourceAd,
    _new_preview_token,
    _preview_token_digest,
    _ready_task_payload,
    build_duplicate_preview,
    build_schedule,
    calculate_budget,
    fetch_account_metadata,
    generate_names,
    resolve_duplicate_source_hierarchy,
    serialize_duplicate_task,
    validate_structure_caps,
)
from core.models.tasks import AdsetDuplicatePreview


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
        source_daily_budget_minor_units=1000,
    )


def test_structure_caps_and_total_objects() -> None:
    assert validate_structure_caps(3, 2, 1) == (6, 6, 15)
    with pytest.raises(AdsetDuplicateError, match="максимум 50"):
        validate_structure_caps(5, 10, 2)
    with pytest.raises(AdsetDuplicateError, match="максимум 10"):
        validate_structure_caps(1, 1, 11)


def test_budget_math_differs_for_abo_and_cbo() -> None:
    abo, abo_minor_units = calculate_budget(
        budget_level="ABO",
        daily_budget="15.00",
        campaign_count=3,
        total_adsets=6,
        currency="EUR",
        currency_exponent=2,
    )
    cbo, cbo_minor_units = calculate_budget(
        budget_level="CBO",
        daily_budget="15.00",
        campaign_count=3,
        total_adsets=6,
        currency="EUR",
        currency_exponent=2,
    )
    assert abo["total_daily_budget"] == "90.00"
    assert cbo["total_daily_budget"] == "45.00"
    assert abo_minor_units == cbo_minor_units == 1500


@pytest.mark.parametrize(
    ("currency", "currency_exponent", "daily_budget", "expected_display", "expected_minor"),
    [
        ("JPY", 0, "1500", "1500", 1500),
        ("USD", 2, "15.00", "15.00", 1500),
        ("KWD", 3, "1.500", "1.500", 1500),
    ],
)
def test_budget_uses_reviewed_currency_exponent(
    currency: str,
    currency_exponent: int,
    daily_budget: str,
    expected_display: str,
    expected_minor: int,
) -> None:
    budget, minor_units = calculate_budget(
        budget_level="ABO",
        daily_budget=daily_budget,
        campaign_count=1,
        total_adsets=1,
        currency=currency,
        currency_exponent=currency_exponent,
    )

    assert budget["unit_daily_budget"] == expected_display
    assert budget["currency"] == currency
    assert budget["currency_exponent"] == currency_exponent
    assert minor_units == expected_minor


@pytest.mark.parametrize(
    ("currency", "currency_exponent", "daily_budget"),
    [
        ("JPY", 0, "1.50"),
        ("KWD", 2, "1.500"),
        ("XAU", 2, "1.00"),
    ],
)
def test_budget_rejects_unreviewed_or_mismatched_money_identity(
    currency: str,
    currency_exponent: int,
    daily_budget: str,
) -> None:
    with pytest.raises(AdsetDuplicateError):
        calculate_budget(
            budget_level="ABO",
            daily_budget=daily_budget,
            campaign_count=1,
            total_adsets=1,
            currency=currency,
            currency_exponent=currency_exponent,
        )


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


def test_schedule_uses_iana_zone_for_half_hour_offset() -> None:
    schedule = build_schedule(
        requested_start_date=date(2026, 7, 17),
        timezone_name="Asia/Kolkata",
        timezone_offset_hours=5.5,
        now=datetime(2026, 7, 15, 12, tzinfo=UTC),
    )
    assert schedule["timezone_name"] == "Asia/Kolkata"
    assert schedule["offset"] == "+05:30"
    assert schedule["start_time_local"] == "2026-07-17T00:00:00+05:30"
    assert schedule["start_time_utc"] == "2026-07-16T18:30:00Z"


@pytest.mark.parametrize("timezone_name", ["", "Mars/Olympus"])
def test_schedule_rejects_missing_or_invalid_iana_timezone(timezone_name: str) -> None:
    with pytest.raises(AdsetDuplicateError, match="валидный IANA") as error:
        build_schedule(
            requested_start_date=date(2026, 7, 17),
            timezone_name=timezone_name,
            timezone_offset_hours=5.5,
            now=datetime(2026, 7, 15, 12, tzinfo=UTC),
        )

    assert error.value.status_code == 503


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
        currency_exponent=2,
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
@pytest.mark.parametrize("timezone_name", [None, "", "Mars/Olympus"])
async def test_account_metadata_rejects_missing_or_invalid_iana_timezone(
    timezone_name: str | None,
) -> None:
    client = AsyncMock()
    client.execute_graph_call.return_value = {
        "id": "123",
        "name": "Untrusted timezone account",
        "currency": "EUR",
        "timezone_name": timezone_name,
        "timezone_offset_hours_utc": 5.5,
    }

    with pytest.raises(AdsetDuplicateError, match="валидный IANA") as error:
        await fetch_account_metadata(client, "act_123")

    assert error.value.status_code == 503


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
async def test_hierarchy_hydration_rejects_missing_local_account_before_graph() -> None:
    source = replace(_source(), account_id="", campaign_id="", adset_id="")
    client = AsyncMock()
    client.execute_graph_call.return_value = {
        "id": source.source_ad_id,
        "account_id": "100",
        "campaign_id": "200",
        "adset_id": "300",
    }

    with pytest.raises(AdsetDuplicateError, match="explicit ad_account_id") as exc_info:
        await resolve_duplicate_source_hierarchy(client, source)

    assert exc_info.value.status_code == 409
    client.execute_graph_call.assert_not_awaited()


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
            currency_exponent=2,
            timezone_name="Europe/Kaliningrad",
            timezone_offset_hours=2,
        ),
        campaign_count=2,
        adsets_per_campaign=2,
        budget_level="ABO",
        daily_budget="15.00",
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
            currency_exponent=2,
            timezone_name="Europe/Kaliningrad",
            timezone_offset_hours=2,
        ),
        campaign_count=2,
        adsets_per_campaign=1,
        budget_level="ABO",
        daily_budget="15.00",
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
            currency_exponent=2,
            timezone_name="Europe/Kaliningrad",
            timezone_offset_hours=2,
        ),
        campaign_count=1,
        adsets_per_campaign=1,
        budget_level="ABO",
        daily_budget="15.00",
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
        "daily_budget": "1.00",
        "idempotency_token": "token-1",
    }
    with pytest.raises(ValidationError):
        AdsetDuplicatePreviewIn.model_validate(base)
    with pytest.raises(ValidationError):
        AdsetDuplicateLaunchIn.model_validate(
            {"preview_token": "x" * 32, "requested_by": "spoofed"}
        )


def test_preview_token_is_canonical_32_byte_base64url_and_only_digest_is_stable() -> None:
    token, generated_digest = _new_preview_token()
    decoded = base64.urlsafe_b64decode(token + "=")

    assert len(token) == 43
    assert len(decoded) == 32
    assert generated_digest == hashlib.sha256(decoded).digest()
    assert _preview_token_digest(token) == generated_digest
    with pytest.raises(AdsetDuplicateError) as error:
        _preview_token_digest("x" * 43)
    assert error.value.status_code == 410


def test_plan_digest_covers_full_canonical_execution_payload() -> None:
    preview = {"source": {"account": {"id": "act_100"}}}
    params = {
        "source_adset_id": "300",
        "selected_ad_ids": ["401"],
        "daily_budget": "15.00",
    }
    payload, digest = _ready_task_payload(preview=preview, task_params=params)
    other_payload, other_digest = _ready_task_payload(
        preview={"source": {"account": {"id": "act_101"}}},
        task_params=params,
    )

    assert payload["ad_account_id"] == "100"
    assert payload["params"]["plan_digest"] == digest.hex()
    assert other_payload["ad_account_id"] == "101"
    assert digest != other_digest


def test_duplicate_preview_postgres_contract_has_no_redis_or_dead_expiry_fields() -> None:
    root = Path(__file__).resolve().parents[2]
    service_source = (root / "core/adset_duplicates/service.py").read_text(encoding="utf-8")
    router_source = (root / "apps/api/routers/v1/adset_duplicates.py").read_text(encoding="utf-8")
    combined = service_source + router_source

    assert "DepRedis" not in combined
    assert "adset_duplicate:preview:" not in combined
    assert "mark_preview_consumed" not in combined
    assert "best_effort" not in combined
    assert "connection=conn" in service_source
    assert "FOR UPDATE" in service_source
    assert '"expired"' not in router_source
    assert "expires_at" not in AdsetDuplicateLaunchOut.model_fields
    assert "expires_at" not in AdsetDuplicateStatusOut.model_fields


def test_duplicate_preview_model_has_explicit_integrity_and_cleanup_indexes() -> None:
    table = AdsetDuplicatePreview.__table__
    assert set(table.columns.keys()) == {
        "token_digest",
        "principal",
        "preview",
        "task_payload",
        "plan_digest",
        "idempotency_key",
        "task_id",
        "created_at",
        "expires_at",
        "consumed_at",
    }
    assert table.primary_key.columns.keys() == ["token_digest"]
    assert {constraint.name for constraint in table.constraints if constraint.name is not None} >= {
        "ck_adset_duplicate_previews_token_digest_sha256",
        "ck_adset_duplicate_previews_plan_digest_sha256",
        "ck_adset_duplicate_previews_consumption_coherent",
        "fk_adset_duplicate_previews_task_id_task_queue",
    }
    indexes = {index.name: index for index in table.indexes}
    assert set(indexes) == {
        "ix_adset_duplicate_previews_expires_at",
        "ix_adset_duplicate_previews_task_id",
    }
    assert (
        indexes["ix_adset_duplicate_previews_expires_at"].dialect_options["postgresql"]["where"]
        is not None
    )


def test_fresh_baseline_contains_only_postgres_duplicate_preview_authority() -> None:
    baseline = (
        Path(__file__).resolve().parents[2] / "migrations/versions/0001_safety_first_baseline.sql"
    ).read_text(encoding="utf-8")
    table = baseline.split(
        "CREATE TABLE public.adset_duplicate_previews",
        1,
    )[1].split(");", 1)[0]

    assert "token_digest bytea NOT NULL" in table
    assert "task_payload jsonb NOT NULL" in table
    assert "task_params jsonb" not in table
    assert "octet_length(token_digest) = 32" in table
    assert "octet_length(plan_digest) = 32" in table
    assert (
        "CREATE INDEX ix_adset_duplicate_previews_expires_at "
        "ON public.adset_duplicate_previews USING btree (expires_at) "
        "WHERE (task_id IS NULL);"
    ) in baseline
    assert ("FOREIGN KEY (task_id) REFERENCES public.task_queue(id) ON DELETE CASCADE") in baseline


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
