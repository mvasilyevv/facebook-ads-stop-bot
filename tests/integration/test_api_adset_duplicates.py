# -*- coding: utf-8 -*-
"""Integration: preview → explicit web launch → task status for adset duplication."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import fakeredis.aioredis as fakeredis_aio  # type: ignore[import-not-found]
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import apps.api.routers.v1.adset_duplicates as router_module
from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app
from core.adset_duplicates.service import (
    AccountMetadata,
    create_duplicate_draft,
    load_stored_preview,
)
from core.meta_api.ownership import OwnershipDecision

SOURCE_AD_ID = "990001000001"
SIBLING_AD_ID = "990001000002"
THIRD_AD_ID = "990001000003"
FOREIGN_AD_ID = "990001000099"
SOURCE_ADSET_ID = "990002000001"
CAMPAIGN_ID = "990003000001"
ACCOUNT_ID = "act_990004000001"


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
                "DELETE FROM task_queue WHERE idempotency_key LIKE 'meta:duplicate-adset:testdup-%'"
            )
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
                "account": ACCOUNT_ID.removeprefix("act_"),
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
                "DELETE FROM task_queue WHERE idempotency_key LIKE 'meta:duplicate-adset:testdup-%'"
            )
        )
        await conn.execute(
            text("DELETE FROM fb_campaigns WHERE fb_campaign_id = :id"),
            {"id": CAMPAIGN_ID},
        )
        await conn.execute(text("DELETE FROM offers WHERE code = 'DUP_API_TEST'"))


@pytest_asyncio.fixture
async def adset_duplicate_client(pg_engine, duplicate_catalog, monkeypatch):
    redis = fakeredis_aio.FakeRedis()
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: pg_engine
    app.dependency_overrides[get_redis] = lambda: redis
    app.state.redis = redis
    monkeypatch.setattr(router_module, "load_owner_tag", AsyncMock(return_value=None))

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
        yield client, redis
    await redis.aclose()


def _preview_body(*, token: str, selected: list[str] | None = None) -> dict:
    return {
        "source_ad_id": SOURCE_AD_ID,
        "selected_ad_ids": selected or [SOURCE_AD_ID],
        "campaign_count": 2,
        "adsets_per_campaign": 2,
        "budget_level": "ABO",
        "daily_budget_cents": 1500,
        "start_date": (datetime.now(UTC).date() + timedelta(days=2)).isoformat(),
        "idempotency_token": token,
    }


@pytest.mark.asyncio
async def test_preview_returns_canonical_shape_all_source_ads_and_exact_budget(
    adset_duplicate_client,
) -> None:
    client, redis = adset_duplicate_client
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
        "unit_daily_budget_cents": 1500,
        "total_daily_budget_cents": 6000,
        "currency": "EUR",
    }
    assert data["schedule"]["timezone_name"] == "Europe/Kaliningrad"
    assert data["schedule"]["offset"] == "+02:00"
    assert len(data["generated_names"]["campaigns"]) == 2
    assert len(data["generated_names"]["adsets"]) == 4
    assert 0 < await redis.ttl(f"adset_duplicate:preview:{data['preview_token']}") <= 900


@pytest.mark.asyncio
async def test_preview_hydrates_missing_local_fb_adset_id(
    adset_duplicate_client,
    pg_engine,
) -> None:
    client, redis = adset_duplicate_client
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
        stored = await load_stored_preview(redis, preview["preview_token"])
        assert stored.task_params["source_adset_id"] == SOURCE_ADSET_ID
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
    client, _redis = adset_duplicate_client
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
    adset_duplicate_client, monkeypatch
) -> None:
    client, redis = adset_duplicate_client
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
    assert await redis.dbsize() == 0


@pytest.mark.asyncio
async def test_web_launch_is_idempotent_approved_and_status_serialized(
    adset_duplicate_client, pg_engine
) -> None:
    client, redis = adset_duplicate_client
    preview_resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-draft", selected=[SOURCE_AD_ID, SIBLING_AD_ID]),
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
    assert row[3] == "web:adset_duplicate"

    status = await client.get(f"/api/tools/adset-duplicates/{task_id}")
    assert status.status_code == 200
    assert status.json()["status"] == "pending"
    assert status.json()["progress"]["phase"] == "queued"
    stored = await redis.get(f"adset_duplicate:preview:{preview['preview_token']}")
    assert f'"consumed_task_id":{task_id}' in stored.decode()


@pytest.mark.asyncio
async def test_second_preview_reuses_the_same_pending_task(
    adset_duplicate_client, pg_engine
) -> None:
    client, _redis = adset_duplicate_client
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
async def test_existing_safe_draft_is_approved_from_web(adset_duplicate_client, pg_engine) -> None:
    client, redis = adset_duplicate_client
    preview_resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-existing-notify-fail"),
    )
    token = preview_resp.json()["preview_token"]
    stored = await load_stored_preview(redis, token)
    task_id, created = await create_duplicate_draft(pg_engine, stored=stored)
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
    client, _redis = adset_duplicate_client
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
async def test_foreign_selected_ad_and_expired_preview_are_rejected(
    adset_duplicate_client,
) -> None:
    client, redis = adset_duplicate_client
    foreign = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-wrong-adset", selected=[FOREIGN_AD_ID]),
    )
    assert foreign.status_code == 422

    preview_resp = await client.post(
        "/api/tools/adset-duplicates/preview",
        json=_preview_body(token="testdup-expired"),
    )
    token = preview_resp.json()["preview_token"]
    await redis.delete(f"adset_duplicate:preview:{token}")
    expired = await client.post(
        "/api/tools/adset-duplicates/launch",
        json={"preview_token": token},
    )
    assert expired.status_code == 410
