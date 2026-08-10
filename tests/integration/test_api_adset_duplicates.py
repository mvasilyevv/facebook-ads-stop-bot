# -*- coding: utf-8 -*-
"""Integration: preview → explicit web launch → task status for adset duplication."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import uuid
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import apps.api.routers.v1.adset_duplicates as router_module
import apps.meta_api_worker.main as meta_worker
import core.adset_duplicates.service as duplicate_service
from apps.api.deps import get_engine
from apps.api.main import create_app
from core.adset_duplicates.execution_guard import authorize_duplicate_execution_boundary
from core.adset_duplicates.plan_integrity import duplicate_execution_plan_digest
from core.adset_duplicates.service import (
    AccountMetadata,
    AdsetDuplicateError,
    create_duplicate_task,
    load_stored_preview,
)
from core.meta_api.ownership import OwnershipDecision
from core.tasks.queue import Task, _row_to_task

SOURCE_AD_ID = "990001000001"
SIBLING_AD_ID = "990001000002"
THIRD_AD_ID = "990001000003"
FOREIGN_AD_ID = "990001000099"
SOURCE_ADSET_ID = "990002000001"
CAMPAIGN_ID = "990003000001"
ACCOUNT_ID = "990004000001"


@pytest_asyncio.fixture
async def duplicate_catalog(pg_engine):
    offer_id = uuid.uuid4()
    campaign_pk = uuid.uuid4()
    source_adset_pk = uuid.uuid4()
    foreign_adset_pk = uuid.uuid4()
    ids = [SOURCE_AD_ID, SIBLING_AD_ID, THIRD_AD_ID, FOREIGN_AD_ID]
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM task_queue
                WHERE task_type = 'meta_api_mutation'
                  AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                  AND payload->'params'->>'source_ad_id' = :source_ad_id
                """
            ),
            {"source_ad_id": SOURCE_AD_ID},
        )
        await conn.execute(
            text(
                """
                DELETE FROM adset_duplicate_previews
                WHERE task_payload->'params'->>'source_ad_id' = :source_ad_id
                """
            ),
            {"source_ad_id": SOURCE_AD_ID},
        )
        await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id = ANY(:ids)"), {"ids": ids})
        await conn.execute(
            text("DELETE FROM fb_campaigns WHERE fb_campaign_id = :id"),
            {"id": CAMPAIGN_ID},
        )
        await conn.execute(
            text("DELETE FROM offers WHERE code = 'DUP_API_TEST'"),
        )
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name) VALUES (:id, 'DUP_API_TEST', 'Duplicate API')"
            ),
            {"id": offer_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO fb_campaigns
                    (id, fb_campaign_id, ad_account_id, campaign_name, offer_id)
                VALUES (:id, :fb_id, :account, 'MV | Source Campaign', :offer_id)
                """
            ),
            {
                "id": campaign_pk,
                "fb_id": CAMPAIGN_ID,
                "account": ACCOUNT_ID,
                "offer_id": offer_id,
            },
        )
        await conn.execute(
            text(
                """
                INSERT INTO fb_adsets (id, campaign_id, fb_adset_id, adset_name, daily_budget)
                VALUES
                    (:source, :campaign, :source_fb, 'Source Adset', '1000'),
                    (:foreign, :campaign, :foreign_fb, 'Other Adset', '1000')
                """
            ),
            {
                "source": source_adset_pk,
                "foreign": foreign_adset_pk,
                "campaign": campaign_pk,
                "source_fb": SOURCE_ADSET_ID,
                "foreign_fb": "990002000099",
            },
        )
        await conn.execute(
            text(
                """
                INSERT INTO fb_ads
                    (adset_id, fb_ad_id, ad_name, delivery_status, creative_thumb_url)
                VALUES
                    (:source, :ad1, 'A one', 'ACTIVE', 'https://img/1'),
                    (:source, :ad2, 'B two', 'PAUSED', 'https://img/2'),
                    (:source, :ad3, 'C three', 'ACTIVE', NULL),
                    (:foreign, :foreign_ad, 'Foreign', 'ACTIVE', NULL)
                """
            ),
            {
                "source": source_adset_pk,
                "foreign": foreign_adset_pk,
                "ad1": SOURCE_AD_ID,
                "ad2": SIBLING_AD_ID,
                "ad3": THIRD_AD_ID,
                "foreign_ad": FOREIGN_AD_ID,
            },
        )
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM task_queue
                WHERE task_type = 'meta_api_mutation'
                  AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                  AND payload->'params'->>'source_ad_id' = :source_ad_id
                """
            ),
            {"source_ad_id": SOURCE_AD_ID},
        )
        await conn.execute(
            text(
                """
                DELETE FROM adset_duplicate_previews
                WHERE task_payload->'params'->>'source_ad_id' = :source_ad_id
                """
            ),
            {"source_ad_id": SOURCE_AD_ID},
        )
        await conn.execute(
            text("DELETE FROM fb_campaigns WHERE fb_campaign_id = :id"),
            {"id": CAMPAIGN_ID},
        )
        await conn.execute(text("DELETE FROM offers WHERE code = 'DUP_API_TEST'"))


@pytest_asyncio.fixture
async def adset_duplicate_client(pg_engine, duplicate_catalog, monkeypatch):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    monkeypatch.setattr(router_module, "load_owner_tag", AsyncMock(return_value=None))

    @app.middleware("http")
    async def bind_test_operator_principal(request, call_next):
        request.state.operator_principal = request.headers.get(
            "x-test-operator-principal",
            "operator:test",
        )
        return await call_next(request)

    async def fake_meta_context(_engine, source):
        return (
            replace(
                source,
                account_id=source.account_id or ACCOUNT_ID,
                campaign_id=source.campaign_id or CAMPAIGN_ID,
                adset_id=source.adset_id or SOURCE_ADSET_ID,
            ),
            AccountMetadata(
                id=ACCOUNT_ID,
                name="Test Account",
                currency="EUR",
                currency_exponent=2,
                timezone_name="Europe/Kaliningrad",
                timezone_offset_hours=2,
            ),
        )

    monkeypatch.setattr(
        router_module,
        "_load_meta_context",
        AsyncMock(side_effect=fake_meta_context),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


def _token_digest(token: str) -> bytes:
    raw = base64.urlsafe_b64decode(token + "=")
    return hashlib.sha256(raw).digest()


def _preview_body(*, token: str, selected: list[str] | None = None) -> dict:
    return {
        "source_ad_id": SOURCE_AD_ID,
        "selected_ad_ids": selected or [SOURCE_AD_ID],
        "campaign_count": 2,
        "adsets_per_campaign": 2,
        "budget_level": "ABO",
        "daily_budget": "15.00",
        "start_date": (datetime.now(UTC).date() + timedelta(days=2)).isoformat(),
        "idempotency_token": token,
    }


async def _claim_duplicate_task(pg_engine, task_id: int) -> Task:
    worker_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'running',
                        lease_owner = :worker_id,
                        lease_token = lease_token + 1,
                        lease_expires_at = clock_timestamp() + INTERVAL '5 minutes',
                        deadline_at = clock_timestamp() + INTERVAL '2 minutes',
                        updated_at = clock_timestamp()
                    WHERE id = :task_id
                    RETURNING id, task_type, status, idempotency_key, payload,
                              attempt_count, max_attempts, requested_by, last_error,
                              created_at, external_started_at, result,
                              lane, priority, available_at, deadline_at, lease_owner,
                              lease_token, lease_expires_at, cancel_requested_at,
                              cancel_reason, correlation_id
                    """
                ),
                {"task_id": task_id, "worker_id": worker_id},
            )
        ).one()
    return _row_to_task(row)


async def _external_started_at(pg_engine, task_id: int) -> datetime | None:
    async with pg_engine.connect() as conn:
        return await conn.scalar(
            text("SELECT external_started_at FROM task_queue WHERE id = :task_id"),
            {"task_id": task_id},
        )


async def _wait_until_after(expires_at: str) -> None:
    expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    remaining = (expiry - datetime.now(UTC)).total_seconds()
    await asyncio.sleep(max(0.0, remaining) + 0.05)


@pytest.mark.asyncio
async def test_preview_returns_canonical_shape_all_source_ads_and_exact_budget(
    adset_duplicate_client,
    pg_engine,
) -> None:
    client = adset_duplicate_client
    resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-preview"),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert set(data) == {
        "preview_token",
        "source",
        "format_code",
        "counts",
        "budget",
        "schedule",
        "generated_names",
        "warnings",
        "expires_at",
    }
    assert data["source"]["account"] == {
        "id": ACCOUNT_ID,
        "name": "Test Account",
        "currency": "EUR",
        "currency_exponent": 2,
    }
    assert [ad["fb_ad_id"] for ad in data["source"]["ads"]] == [
        SOURCE_AD_ID,
        SIBLING_AD_ID,
        THIRD_AD_ID,
    ]
    assert data["format_code"] == "2-2-1"
    assert data["counts"] == {"campaigns": 2, "adsets": 4, "ads": 4, "total_objects": 10}
    assert data["budget"] == {
        "level": "ABO",
        "unit_daily_budget": "15.00",
        "total_daily_budget": "60.00",
        "currency": "EUR",
        "currency_exponent": 2,
    }
    assert data["schedule"]["timezone_name"] == "Europe/Kaliningrad"
    assert data["schedule"]["offset"] == "+02:00"
    assert len(data["generated_names"]["campaigns"]) == 2
    assert len(data["generated_names"]["adsets"]) == 4
    assert len(data["preview_token"]) == 43

    async with pg_engine.connect() as conn:
        stored = (
            await conn.execute(
                text(
                    """
                    SELECT principal, token_digest, task_payload, plan_digest,
                           idempotency_key,
                           EXTRACT(EPOCH FROM expires_at - created_at)
                    FROM adset_duplicate_previews
                    WHERE token_digest = :token_digest
                    """
                ),
                {"token_digest": _token_digest(data["preview_token"])},
            )
        ).one()
    assert stored.principal == "operator:test"
    assert bytes(stored.token_digest) == _token_digest(data["preview_token"])
    assert len(bytes(stored.plan_digest)) == 32
    assert stored.task_payload["ad_account_id"] == ACCOUNT_ID
    assert stored.task_payload["params"]["plan_digest"] == bytes(stored.plan_digest).hex()
    assert stored.idempotency_key.startswith("meta:duplicate-adset:")
    assert "testdup-preview" not in stored.idempotency_key
    assert 899 <= float(stored[5]) <= 901
    assert data["preview_token"] not in str(stored.task_payload)


@pytest.mark.asyncio
async def test_preview_rejects_untrusted_timezone_before_persisting_plan_or_task(
    adset_duplicate_client,
    monkeypatch,
    pg_engine,
) -> None:
    client = adset_duplicate_client

    async def invalid_timezone_context(_engine, source):
        return source, AccountMetadata(
            id=source.account_id,
            name="Invalid timezone account",
            currency="EUR",
            currency_exponent=2,
            timezone_name="",
            timezone_offset_hours=2,
        )

    monkeypatch.setattr(
        router_module,
        "_load_meta_context",
        AsyncMock(side_effect=invalid_timezone_context),
    )
    async with pg_engine.connect() as conn:
        before = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*)
                           FROM adset_duplicate_previews
                          WHERE task_payload->'params'->>'source_ad_id' = :source_ad_id),
                        (SELECT COUNT(*)
                           FROM task_queue
                          WHERE task_type = 'meta_api_mutation'
                            AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                            AND payload->'params'->>'source_ad_id' = :source_ad_id)
                    """
                ),
                {"source_ad_id": SOURCE_AD_ID},
            )
        ).one()

    response = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-invalid-timezone"),
    )

    assert response.status_code == 503
    assert response.json()["code"] == "service_unavailable"
    assert response.json()["message"] == "Сервис временно недоступен"
    async with pg_engine.connect() as conn:
        after = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*)
                           FROM adset_duplicate_previews
                          WHERE task_payload->'params'->>'source_ad_id' = :source_ad_id),
                        (SELECT COUNT(*)
                           FROM task_queue
                          WHERE task_type = 'meta_api_mutation'
                            AND payload->>'mutation_kind' = 'duplicate_adset_structure'
                            AND payload->'params'->>'source_ad_id' = :source_ad_id)
                    """
                ),
                {"source_ad_id": SOURCE_AD_ID},
            )
        ).one()
    assert after == before


@pytest.mark.asyncio
async def test_preview_hydrates_missing_local_fb_adset_id(
    adset_duplicate_client,
    pg_engine,
) -> None:
    client = adset_duplicate_client
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE fb_adsets SET fb_adset_id = NULL WHERE fb_adset_id = :adset_id"),
            {"adset_id": SOURCE_ADSET_ID},
        )
    try:
        response = await client.post(
            "/api/tools/adset-duplicates/preview",
            json=_preview_body(token="testdup-missing-adset"),
        )
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["source"]["adset"]["id"] == SOURCE_ADSET_ID
        stored = await load_stored_preview(
            pg_engine,
            preview["preview_token"],
            principal="operator:test",
        )
        assert stored.task_payload["params"]["source_adset_id"] == SOURCE_ADSET_ID
        assert "preview_token" not in stored.preview
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE fb_adsets SET fb_adset_id = :adset_id "
                    "WHERE campaign_id = ("
                    "SELECT id FROM fb_campaigns WHERE fb_campaign_id = :campaign_id"
                    ") AND adset_name = 'Source Adset'"
                ),
                {"adset_id": SOURCE_ADSET_ID, "campaign_id": CAMPAIGN_ID},
            )


@pytest.mark.asyncio
async def test_preview_preserves_owner_tag_in_editable_campaign_base(
    adset_duplicate_client,
    monkeypatch,
) -> None:
    client = adset_duplicate_client
    monkeypatch.setattr(router_module, "load_owner_tag", AsyncMock(return_value="MV, ABC"))
    body = _preview_body(token="testdup-owner-name")
    body["campaign_name_base"] = "Editable duplicate"

    response = await client.post("/api/tools/adset-duplicates/preview", json=body)

    assert response.status_code == 200, response.text
    data = response.json()
    assert all(
        "Editable duplicate | MV | DUP" in name for name in data["generated_names"]["campaigns"]
    )
    assert any("Owner-tag 'MV' добавлен" in warning for warning in data["warnings"])


@pytest.mark.asyncio
async def test_preview_rejects_foreign_owner_before_meta_or_token(
    adset_duplicate_client, monkeypatch, pg_engine
) -> None:
    client = adset_duplicate_client
    meta_lookup = AsyncMock()
    monkeypatch.setattr(router_module, "load_owner_tag", AsyncMock(return_value="OWNER"))
    monkeypatch.setattr(
        router_module,
        "check_ad_ownership",
        AsyncMock(
            return_value=OwnershipDecision(
                allowed=False,
                reason="ad belongs to foreign campaign",
                foreign_ids=(SOURCE_AD_ID,),
            )
        ),
    )
    monkeypatch.setattr(router_module, "_load_meta_context", meta_lookup)

    resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-foreign"),
    )

    assert resp.status_code == 403
    meta_lookup.assert_not_awaited()
    async with pg_engine.connect() as conn:
        count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM adset_duplicate_previews
                WHERE task_payload->'params'->>'source_ad_id' = :source_ad_id
                """
            ),
            {"source_ad_id": SOURCE_AD_ID},
        )
    assert count == 0


@pytest.mark.asyncio
async def test_web_launch_queues_one_idempotent_task_and_serializes_status(
    adset_duplicate_client, pg_engine
) -> None:
    client = adset_duplicate_client
    preview_resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-launch", selected=[SOURCE_AD_ID, SIBLING_AD_ID]),
    )
    preview = preview_resp.json()

    first = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": preview["preview_token"]},
    )
    second = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": preview["preview_token"]},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["task_id"] == second.json()["task_id"]
    assert first.json()["status"] == "pending"

    task_id = first.json()["task_id"]
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT status, max_attempts, payload, requested_by
                    FROM task_queue WHERE id = :id
                    """
                ),
                {"id": task_id},
            )
        ).one()
    assert row[0] == "pending"
    assert row[1] == 1
    assert row[2]["mutation_kind"] == "duplicate_adset_structure"
    assert row[2]["ad_account_id"] == ACCOUNT_ID
    assert row[2]["params"]["selected_ad_ids"] == [SOURCE_AD_ID, SIBLING_AD_ID]
    assert row[2]["params"]["start_time"].endswith("Z")
    assert row[3] == "operator:test"

    status = await client.get(f"/api/tools/adset-duplicates/{task_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "pending"
    assert status.json()["progress"]["phase"] == "queued"
    assert "expires_at" not in status.json()
    foreign_status = await client.get(
        f"/api/tools/adset-duplicates/{task_id}",
        headers={"x-test-operator-principal": "operator:other"},
    )
    assert foreign_status.status_code == 404
    async with pg_engine.connect() as conn:
        stored = (
            await conn.execute(
                text(
                    """
                    SELECT task_id, consumed_at, task_payload
                    FROM adset_duplicate_previews
                    WHERE token_digest = :token_digest
                    """
                ),
                {"token_digest": _token_digest(preview["preview_token"])},
            )
        ).one()
    assert stored.task_id == task_id
    assert stored.consumed_at is not None
    assert stored.task_payload == row[2]


@pytest.mark.asyncio
async def test_second_preview_reuses_the_same_pending_task(
    adset_duplicate_client, pg_engine
) -> None:
    client = adset_duplicate_client
    body = _preview_body(token="testdup-task-marker")
    first_preview = await client.post("/api/tools/adset-duplicates/preview", json=body)
    second_preview = await client.post("/api/tools/adset-duplicates/preview", json=body)
    first_token = first_preview.json()["preview_token"]
    second_token = second_preview.json()["preview_token"]
    assert first_token != second_token

    first = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": first_token},
    )
    second = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": second_token},
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]
    async with pg_engine.connect() as conn:
        status = await conn.scalar(
            text("SELECT status FROM task_queue WHERE id = :id"),
            {"id": first.json()["task_id"]},
        )
    assert status == "pending"


@pytest.mark.asyncio
async def test_existing_pending_task_is_reused_from_web(adset_duplicate_client, pg_engine) -> None:
    client = adset_duplicate_client
    preview_resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-existing-notify-fail"),
    )
    token = preview_resp.json()["preview_token"]
    task_id, created = await create_duplicate_task(
        pg_engine,
        preview_token=token,
        principal="operator:test",
    )
    assert created
    resp = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": token},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["task_id"] == task_id
    assert resp.json()["status"] == "pending"
    async with pg_engine.connect() as conn:
        status = await conn.scalar(
            text("SELECT status FROM task_queue WHERE id = :id"),
            {"id": task_id},
        )
    assert status == "pending"


@pytest.mark.asyncio
async def test_concurrent_double_submit_launches_one_task(adset_duplicate_client) -> None:
    client = adset_duplicate_client
    preview_resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-concurrent"),
    )
    token = preview_resp.json()["preview_token"]

    first, second = await asyncio.gather(
        client.post("/api/tools/adset-duplicates/launch", json={"preview_token": token}),
        client.post("/api/tools/adset-duplicates/launch", json={"preview_token": token}),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]
    assert first.json()["status"] == second.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_same_idempotency_token_with_different_plan_returns_conflict(
    adset_duplicate_client,
    pg_engine,
) -> None:
    client = adset_duplicate_client
    first_body = _preview_body(token="testdup-plan-conflict")
    second_body = {**first_body, "daily_budget": "16.00"}
    first_preview = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=first_body,
    )
    second_preview = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=second_body,
    )

    first = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": first_preview.json()["preview_token"]},
    )
    second = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": second_preview.json()["preview_token"]},
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert "другого плана" in second.json()["message"]
    async with pg_engine.connect() as conn:
        task_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM task_queue
                WHERE idempotency_key = (
                    SELECT idempotency_key
                    FROM adset_duplicate_previews
                    WHERE token_digest = :token_digest
                )
                """
            ),
            {"token_digest": _token_digest(first_preview.json()["preview_token"])},
        )
        second_task_id = await conn.scalar(
            text(
                """
                SELECT task_id
                FROM adset_duplicate_previews
                WHERE token_digest = :token_digest
                """
            ),
            {"token_digest": _token_digest(second_preview.json()["preview_token"])},
        )
    assert task_count == 1
    assert second_task_id is None


@pytest.mark.asyncio
async def test_preview_is_bound_to_principal_and_digest(
    adset_duplicate_client,
    pg_engine,
) -> None:
    client = adset_duplicate_client
    principal_preview = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-principal"),
    )
    principal_token = principal_preview.json()["preview_token"]
    with pytest.raises(AdsetDuplicateError) as principal_error:
        await create_duplicate_task(
            pg_engine,
            preview_token=principal_token,
            principal="operator:web:other",
        )
    assert principal_error.value.status_code == 403

    immutable_preview = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-corrupt"),
    )
    immutable_token = immutable_preview.json()["preview_token"]
    with pytest.raises(DBAPIError):
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE adset_duplicate_previews
                    SET plan_digest = decode(repeat('00', 32), 'hex')
                    WHERE token_digest = :token_digest
                    """
                ),
                {"token_digest": _token_digest(immutable_token)},
            )
    launch = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": immutable_token},
    )
    assert launch.status_code == 200, launch.text


@pytest.mark.asyncio
async def test_atomic_execution_guard_sets_boundary_for_exact_claim(
    adset_duplicate_client,
    pg_engine,
) -> None:
    client = adset_duplicate_client
    preview = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-authoritative-boundary"),
    )
    launch = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": preview.json()["preview_token"]},
    )
    task = await _claim_duplicate_task(pg_engine, launch.json()["task_id"])

    authorized = await authorize_duplicate_execution_boundary(
        pg_engine,
        task_id=task.id,
        task_payload=task.payload,
        requested_by=task.requested_by,
        target_lock_key=str(task.payload["target_id"]),
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )

    assert authorized is True
    assert await _external_started_at(pg_engine, task.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["missing_receipt", "task_payload", "conflicting_receipt"])
async def test_worker_rejects_receipt_integrity_races_before_external_start(
    adset_duplicate_client,
    pg_engine,
    monkeypatch,
    tamper: str,
) -> None:
    client = adset_duplicate_client
    preview = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token=f"testdup-execution-{tamper}"),
    )
    launch = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": preview.json()["preview_token"]},
    )
    task = await _claim_duplicate_task(pg_engine, launch.json()["task_id"])
    tampered_payload = deepcopy(task.payload)
    tampered_payload["params"]["daily_budget"] = "99.00"
    tampered_digest = duplicate_execution_plan_digest(
        mutation_kind=tampered_payload["mutation_kind"],
        target_id=tampered_payload["target_id"],
        params=tampered_payload["params"],
        ad_account_id=tampered_payload["ad_account_id"],
    )
    tampered_payload["params"]["plan_digest"] = tampered_digest.hex()

    async with pg_engine.begin() as conn:
        if tamper == "missing_receipt":
            await conn.execute(
                text("DELETE FROM adset_duplicate_previews WHERE task_id = :task_id"),
                {"task_id": task.id},
            )
        elif tamper == "task_payload":
            await conn.execute(
                text("UPDATE task_queue SET payload = CAST(:payload AS JSONB) WHERE id = :task_id"),
                {
                    "task_id": task.id,
                    "payload": json.dumps(tampered_payload),
                },
            )
        else:
            token_digest = hashlib.sha256(f"conflict:{task.id}".encode()).digest()
            idempotency_digest = hashlib.sha256(
                f"conflict-idempotency:{task.id}".encode()
            ).hexdigest()
            await conn.execute(
                text(
                    """
                    INSERT INTO adset_duplicate_previews (
                        token_digest, principal, preview, task_payload,
                        plan_digest, idempotency_key, task_id,
                        created_at, expires_at, consumed_at
                    ) VALUES (
                        :token_digest, :principal, '{}'::jsonb,
                        CAST(:task_payload AS JSONB), :plan_digest,
                        :idempotency_key, :task_id, clock_timestamp(),
                        clock_timestamp() + INTERVAL '15 minutes',
                        clock_timestamp()
                    )
                    """
                ),
                {
                    "token_digest": token_digest,
                    "principal": task.requested_by,
                    "task_payload": json.dumps(tampered_payload),
                    "plan_digest": tampered_digest,
                    "idempotency_key": f"meta:duplicate-adset:{idempotency_digest}",
                    "task_id": task.id,
                },
            )

    monkeypatch.setattr(meta_worker, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta_worker, "load_owner_tag", AsyncMock(return_value=None))
    graph_client = AsyncMock()

    await meta_worker.process_one_task(pg_engine, task, client=graph_client)

    async with pg_engine.connect() as conn:
        persisted = (
            await conn.execute(
                text(
                    "SELECT status, result, external_started_at FROM task_queue WHERE id = :task_id"
                ),
                {"task_id": task.id},
            )
        ).one()
    assert persisted.status == "failed"
    assert persisted.result == {
        "outcome": "REJECTED",
        "reason": "duplicate_plan_integrity",
    }
    assert persisted.external_started_at is None
    graph_client.execute_graph_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_receipt_loss_preserves_boundary_and_opens_critical_incident(
    adset_duplicate_client,
    pg_engine,
    monkeypatch,
) -> None:
    client = adset_duplicate_client
    preview = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-recovery-receipt-loss"),
    )
    launch = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": preview.json()["preview_token"]},
    )
    claimed = await _claim_duplicate_task(pg_engine, launch.json()["task_id"])
    checkpoint = {
        "checkpoint_type": "duplicate_adset_structure",
        "checkpoint_version": 2,
        "phase": "recovery_retrying",
        "created_ids": {"campaigns": ["501"], "adsets": ["502"], "ads": []},
        "cleanup_failures": [{"id": "502", "error": "timeout"}],
        "recovery_requested": True,
    }
    async with pg_engine.begin() as conn:
        task_row = (
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET external_started_at = clock_timestamp(),
                        result = CAST(:checkpoint AS JSONB),
                        updated_at = clock_timestamp()
                    WHERE id = :task_id
                    RETURNING id, task_type, status, idempotency_key, payload,
                              attempt_count, max_attempts, requested_by, last_error,
                              created_at, external_started_at, result,
                              lane, priority, available_at, deadline_at, lease_owner,
                              lease_token, lease_expires_at, cancel_requested_at,
                              cancel_reason, correlation_id
                    """
                ),
                {"task_id": claimed.id, "checkpoint": json.dumps(checkpoint)},
            )
        ).one()
        await conn.execute(
            text("DELETE FROM adset_duplicate_previews WHERE task_id = :task_id"),
            {"task_id": claimed.id},
        )
    task = _row_to_task(task_row)
    boundary_before = task.external_started_at
    incident_key = f"task:duplicate-adset:{task.id}"
    monkeypatch.setattr(meta_worker, "load_scanning_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(meta_worker, "load_owner_tag", AsyncMock(return_value=None))
    graph_client = AsyncMock()

    try:
        await meta_worker.process_one_task(pg_engine, task, client=graph_client)

        async with pg_engine.connect() as conn:
            persisted = (
                await conn.execute(
                    text(
                        "SELECT status, result, external_started_at "
                        "FROM task_queue WHERE id = :task_id"
                    ),
                    {"task_id": task.id},
                )
            ).one()
            incident = (
                await conn.execute(
                    text(
                        "SELECT severity, status FROM incidents WHERE incident_key = :incident_key"
                    ),
                    {"incident_key": incident_key},
                )
            ).one()
        assert persisted.status == "failed"
        assert persisted.external_started_at == boundary_before
        assert persisted.result["created_ids"] == checkpoint["created_ids"]
        assert persisted.result["outcome"] == "UNKNOWN"
        assert persisted.result["manual_review_required"] is True
        assert persisted.result["phase"] == "recovery_checkpoint_invalid"
        assert incident.severity == "critical"
        assert incident.status == "open"
        graph_client.execute_graph_call.assert_not_awaited()
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM notification_events WHERE incident_id IN ("
                    "SELECT id FROM incidents WHERE incident_key = :incident_key)"
                ),
                {"incident_key": incident_key},
            )
            await conn.execute(
                text("DELETE FROM incidents WHERE incident_key = :incident_key"),
                {"incident_key": incident_key},
            )


@pytest.mark.asyncio
async def test_consumed_terminal_preview_replays_same_task_until_task_retention(
    adset_duplicate_client,
    pg_engine,
    monkeypatch,
) -> None:
    client = adset_duplicate_client
    monkeypatch.setattr(duplicate_service, "PREVIEW_TTL_SECONDS", 1)
    preview = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-terminal-replay"),
    )
    token = preview.json()["preview_token"]
    first = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": token},
    )
    task_id = first.json()["task_id"]
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'failed',
                    completed_at = clock_timestamp(),
                    last_error = 'synthetic terminal state'
                WHERE id = :task_id
                """
            ),
            {"task_id": task_id},
        )
    await _wait_until_after(preview.json()["expires_at"])

    replay = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": token},
    )

    assert replay.status_code == 200, replay.text
    assert replay.json() == {"task_id": task_id, "status": "failed"}


@pytest.mark.asyncio
async def test_foreign_selected_ad_and_expired_preview_are_rejected(
    adset_duplicate_client,
    pg_engine,
    monkeypatch,
) -> None:
    client = adset_duplicate_client
    foreign = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-wrong-adset", selected=[FOREIGN_AD_ID]),
    )
    assert foreign.status_code == 422

    monkeypatch.setattr(duplicate_service, "PREVIEW_TTL_SECONDS", 2)
    preview_resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-expired"),
    )
    token = preview_resp.json()["preview_token"]
    async with pg_engine.connect() as lock_conn:
        async with lock_conn.begin():
            await lock_conn.execute(
                text(
                    "SELECT 1 FROM adset_duplicate_previews "
                    "WHERE token_digest = :token_digest FOR UPDATE"
                ),
                {"token_digest": _token_digest(token)},
            )
            launch_task = asyncio.create_task(
                client.post(
                    "/api/tools/adset-duplicates/launch",
                    json={"preview_token": token},
                )
            )
            await asyncio.sleep(0.1)
            assert launch_task.done() is False
            await _wait_until_after(preview_resp.json()["expires_at"])
    expired = await launch_task
    assert expired.status_code == 410
    async with pg_engine.connect() as conn:
        receipt_task_id = await conn.scalar(
            text("SELECT task_id FROM adset_duplicate_previews WHERE token_digest = :token_digest"),
            {"token_digest": _token_digest(token)},
        )
    assert receipt_task_id is None
