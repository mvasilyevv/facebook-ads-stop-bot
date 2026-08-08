from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(*, engine, redis):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_redis] = lambda: redis
    app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def operator_events_fixture(pg_engine):
    suffix = uuid.uuid4().hex[:8]
    offer_id, campaign_id, adset_id, ad_id = (uuid.uuid4() for _ in range(4))
    fb_ad_id = f"op_event_{suffix}"
    task_keys = [f"op_event_done_{suffix}", f"op_event_pending_{suffix}"]
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:id, :code, :name)"),
            {"id": offer_id, "code": f"OPE_{suffix}", "name": f"Operator events {suffix}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, campaign_name, offer_id, ad_account_id) "
                "VALUES (:id, :name, :offer_id, '123')"
            ),
            {"id": campaign_id, "name": f"OPE campaign {suffix}", "offer_id": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:id, :cid, :name)"),
            {"id": adset_id, "cid": campaign_id, "name": f"OPE adset {suffix}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                "VALUES (:id, :adset_id, :fb_ad_id, :name, NOW())"
            ),
            {"id": ad_id, "adset_id": adset_id, "fb_ad_id": fb_ad_id, "name": "OPE ad"},
        )
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at) "
                "VALUES (gen_random_uuid(), :ad_id, 'stop', 'stop_sent', "
                "CAST('[\"cpl_stop\"]' AS jsonb), CAST('{}' AS jsonb), NOW() - INTERVAL '2 minutes')"
            ),
            {"ad_id": ad_id},
        )
        for key, status in zip(task_keys, ("succeeded", "pending"), strict=True):
            await conn.execute(
                text(
                    "INSERT INTO task_queue "
                    "(task_type, lane, status, idempotency_key, payload, requested_by, "
                    "created_at, updated_at) "
                    "VALUES ('meta_api_mutation', 'money', :status, :key, CAST(:payload AS jsonb), "
                    "'operator-test', NOW() - INTERVAL '1 minute', NOW() - INTERVAL '1 minute')"
                ),
                {
                    "status": status,
                    "key": key,
                    "payload": (
                        '{"mutation_kind":"pause_ad","target_id":"'
                        f'{fb_ad_id}","ad_account_id":"123"}}'
                    ),
                },
            )

    yield {"campaign_id": campaign_id, "fb_ad_id": fb_ad_id}

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key = ANY(:keys)"),
            {"keys": task_keys},
        )
        await conn.execute(text("DELETE FROM offers WHERE id = :id"), {"id": offer_id})


@pytest.mark.asyncio
async def test_operator_events_unifies_alerts_and_terminal_actions(
    pg_engine, fake_redis_client, operator_events_fixture
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/operator/events",
            params={"campaign_id": str(operator_events_fixture["campaign_id"])},
        )

    assert response.status_code == 200, response.text
    events = response.json()
    assert {event["event_type"] for event in events} == {"alert", "task"}
    assert [event["ts"] for event in events] == sorted(
        (event["ts"] for event in events), reverse=True
    )
    assert all(event["fb_ad_id"] == operator_events_fixture["fb_ad_id"] for event in events)
    task = next(event for event in events if event["event_type"] == "task")
    assert task["task_status"] == "SUCCEEDED"


@pytest.mark.asyncio
async def test_operator_events_filters_before_limit(
    pg_engine, fake_redis_client, operator_events_fixture
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        alert_response = await client.get(
            "/api/operator/events",
            params={
                "campaign_id": str(operator_events_fixture["campaign_id"]),
                "stage": "stop",
                "limit": 1,
            },
        )
        task_response = await client.get(
            "/api/operator/events",
            params={
                "campaign_id": str(operator_events_fixture["campaign_id"]),
                "task_status": "SUCCEEDED",
                "search": operator_events_fixture["fb_ad_id"],
                "limit": 1,
            },
        )

    assert alert_response.status_code == 200
    assert [event["event_type"] for event in alert_response.json()] == ["alert"]
    assert task_response.status_code == 200
    assert [event["event_type"] for event in task_response.json()] == ["task"]


@pytest.mark.asyncio
async def test_operator_events_rejects_invalid_window(fake_redis_client) -> None:
    # Window validation is contract-level and must not depend on PostgreSQL.
    app = _make_app(engine=object(), redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/operator/events",
            params={
                "period": "custom",
                "from_date": "2026-06-02",
                "to_date": "2026-06-01",
            },
        )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_events_window"
