"""Integration coverage for unified campaign -> adset -> ad analytics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app

_PREFIX = "ANALYTICS_IT_"


def _make_app(*, engine, redis):
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_redis] = lambda: redis
    app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def analytics_chain(pg_engine):
    ids = {name: uuid.uuid4() for name in ("offer", "campaign", "adset", "ad")}
    now = datetime.now(UTC)
    history_from = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    history_to = history_from + timedelta(hours=23, minutes=59)
    fb_ad_id = f"79{uuid.uuid4().hex[:8]}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:id, :code, :name)"),
            {"id": ids["offer"], "code": f"{_PREFIX}OFFER", "name": "Analytics offer"},
        )
        await conn.execute(
            text(
                "INSERT INTO offer_rules (offer_id, cpa_threshold, stop_percent_of_rule) "
                "VALUES (:id, 10, 80)"
            ),
            {"id": ids["offer"]},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns "
                "(id, fb_campaign_id, ad_account_id, campaign_name, offer_id) "
                "VALUES (:id, :fb, '777', :name, :offer)"
            ),
            {
                "id": ids["campaign"],
                "fb": f"77{uuid.uuid4().hex[:8]}",
                "name": f"{_PREFIX}CAMPAIGN",
                "offer": ids["offer"],
            },
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, campaign_id, fb_adset_id, adset_name) "
                "VALUES (:id, :campaign, :fb, :name)"
            ),
            {
                "id": ids["adset"],
                "campaign": ids["campaign"],
                "fb": f"78{uuid.uuid4().hex[:8]}",
                "name": f"{_PREFIX}ADSET",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, delivery_status) "
                "VALUES (:id, :adset, :fb, :name, 'ACTIVE')"
            ),
            {
                "id": ids["ad"],
                "adset": ids["adset"],
                "fb": fb_ad_id,
                "name": f"{_PREFIX}AD",
            },
        )
        for cycle_ts, spend, clicks in (
            (history_from + timedelta(hours=10), Decimal("8.00"), 20),
            (history_from + timedelta(hours=11), Decimal("12.00"), 30),
            (now - timedelta(minutes=10), Decimal("12.00"), 30),
        ):
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics "
                    "(id, ad_id, cycle_ts, spend, impressions, clicks, leads) "
                    "VALUES (gen_random_uuid(), :ad, :ts, :spend, 1000, :clicks, 2)"
                ),
                {"ad": ids["ad"], "ts": cycle_ts, "spend": spend, "clicks": clicks},
            )
        for event_ts in (
            history_from + timedelta(hours=11, minutes=10),
            now - timedelta(minutes=5),
        ):
            await conn.execute(
                text(
                    "INSERT INTO tracker_click_state "
                    "(id, source, click_id, ad_id, fb_ad_id, attribution_status, "
                    " registration, ftd, confirmed_deposit, registration_at, ftd_at, "
                    " confirmed_deposit_at, ftd_revenue, last_event_at) "
                    "VALUES (gen_random_uuid(), 'adsetpro', :click, :ad, :fb, 'matched_direct', "
                    " true, true, true, :ts, :ts, :ts, 25, :ts)"
                ),
                {
                    "click": f"{_PREFIX}{uuid.uuid4().hex}",
                    "ad": ids["ad"],
                    "fb": fb_ad_id,
                    "ts": event_ts,
                },
            )
    ids["from"] = history_from
    ids["to"] = history_to
    yield ids
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM tracker_click_state WHERE ad_id IN "
                "(SELECT id FROM fb_ads WHERE ad_name LIKE :prefix)"
            ),
            {"prefix": f"{_PREFIX}%"},
        )
        await conn.execute(
            text(
                "DELETE FROM ad_metrics WHERE ad_id IN "
                "(SELECT id FROM fb_ads WHERE ad_name LIKE :prefix)"
            ),
            {"prefix": f"{_PREFIX}%"},
        )
        await conn.execute(
            text("DELETE FROM fb_ads WHERE ad_name LIKE :prefix"), {"prefix": f"{_PREFIX}%"}
        )
        await conn.execute(
            text("DELETE FROM fb_adsets WHERE adset_name LIKE :prefix"), {"prefix": f"{_PREFIX}%"}
        )
        await conn.execute(
            text("DELETE FROM fb_campaigns WHERE campaign_name LIKE :prefix"),
            {"prefix": f"{_PREFIX}%"},
        )
        await conn.execute(
            text("DELETE FROM offers WHERE code LIKE :prefix"), {"prefix": f"{_PREFIX}%"}
        )


@pytest.mark.asyncio
async def test_performance_uses_latest_meta_snapshot_and_exact_tracker_window(
    pg_engine, fake_redis_client, analytics_chain
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    params = {
        "period": "custom",
        "from_iso": analytics_chain["from"].isoformat(),
        "to_iso": analytics_chain["to"].isoformat(),
        "level": "campaign",
        "campaign_id": str(analytics_chain["campaign"]),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/analytics/performance", params=params)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["rows"][0]["spend"] == "12.00"
    assert body["rows"][0]["clicks"] == 30
    assert body["rows"][0]["registrations"] == 1
    assert body["rows"][0]["ftds"] == 1
    assert body["rows"][0]["confirmed_deposits"] == 1
    assert body["rows"][0]["live_budget"] is None
    assert body["rows"][0]["budget_unavailable_reason"].startswith("Budget delta доступен")


@pytest.mark.asyncio
async def test_live_performance_has_budget_and_drills_to_ad(
    pg_engine, fake_redis_client, analytics_chain
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        campaign_response = await client.get(
            "/api/analytics/performance",
            params={
                "period": "today",
                "level": "campaign",
                "campaign_id": str(analytics_chain["campaign"]),
            },
        )
        ad_response = await client.get(
            "/api/analytics/performance",
            params={
                "period": "today",
                "level": "ad",
                "parent_id": str(analytics_chain["adset"]),
            },
        )

    assert campaign_response.status_code == 200, campaign_response.text
    campaign = campaign_response.json()
    assert campaign["window"]["is_live"] is True
    assert campaign["rows"][0]["live_budget"]["stage"] == "mixed"
    assert campaign["rows"][0]["live_budget"]["base_budget"] == "10.00"
    assert campaign["rows"][0]["live_budget"]["base_delta"] == "2.00"

    assert ad_response.status_code == 200, ad_response.text
    assert ad_response.json()["rows"][0]["level"] == "ad"
    assert ad_response.json()["rows"][0]["id"] == str(analytics_chain["ad"])


@pytest.mark.asyncio
async def test_daypart_rejects_unknown_iana_timezone(pg_engine, fake_redis_client):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/daypart",
            params={"timezone": "Europe/Definitely-Not-A-Zone"},
        )
    assert response.status_code == 422
