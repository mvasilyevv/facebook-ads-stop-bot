"""Integration coverage for unified campaign -> adset -> ad analytics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app
from core.analytics.performance import fetch_live_budget_points
from core.dashboard.cabinet_spend import cabinet_day_start_utc

_PREFIX = "ANALYTICS_IT_"
_ACCOUNT_ID = "777"
_ACCOUNT_OFFSET_HOURS = 8.0


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
    # A fully completed UTC day, separated from the live cabinet-day sample even
    # when the suite starts just after UTC midnight.
    history_from = (now - timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    history_to = history_from + timedelta(hours=23, minutes=59)
    history_date = history_from.date()
    cabinet_zone = ZoneInfo("Asia/Singapore")
    custom_from = datetime.combine(
        history_date,
        datetime.min.time(),
        tzinfo=cabinet_zone,
    ).astimezone(UTC)
    custom_to = custom_from + timedelta(days=1) - timedelta(microseconds=1)
    cabinet_start = cabinet_day_start_utc(_ACCOUNT_OFFSET_HOURS, now)
    live_ts = cabinet_start + (now - cabinet_start) / 2
    fb_ad_id = f"79{uuid.uuid4().hex[:8]}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:id, :code, :name)"),
            {"id": ids["offer"], "code": f"{_PREFIX}OFFER", "name": "Analytics offer"},
        )
        await conn.execute(
            text(
                "INSERT INTO meta_account_snapshot "
                "(account_id, timezone_name, currency, currency_observed_at) "
                "VALUES (:account_id, 'Asia/Singapore', 'USD', NOW()) "
                "ON CONFLICT (account_id) DO UPDATE SET "
                "timezone_name = EXCLUDED.timezone_name, "
                "currency = EXCLUDED.currency, "
                "currency_observed_at = EXCLUDED.currency_observed_at"
            ),
            {"account_id": _ACCOUNT_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO system_config (key, value, description) "
                "VALUES ("
                "'tracker_provider_reconciliation', "
                "jsonb_build_object("
                "'status', 'ok', "
                "'checked_at', CAST(:checked_at AS text), "
                "'window_start', CAST(:window_start AS text), "
                "'window_end', CAST(:window_end AS text), "
                "'drift_after', 0, "
                "'skipped', 0"
                "), "
                "'Analytics integration provider audit'"
                ") "
                "ON CONFLICT (key) DO UPDATE SET "
                "value = EXCLUDED.value, updated_at = NOW()"
            ),
            {
                "checked_at": now.isoformat(),
                "window_start": custom_from.isoformat(),
                "window_end": now.isoformat(),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO offer_rules "
                "(offer_id, cpa_threshold, currency, stop_percent_of_rule) "
                "VALUES (:id, 10, 'USD', 80)"
            ),
            {"id": ids["offer"]},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns "
                "(id, fb_campaign_id, ad_account_id, campaign_name, offer_id) "
                "VALUES (:id, :fb, :account_id, :name, :offer)"
            ),
            {
                "id": ids["campaign"],
                "fb": f"77{uuid.uuid4().hex[:8]}",
                "name": f"{_PREFIX}CAMPAIGN",
                "offer": ids["offer"],
                "account_id": _ACCOUNT_ID,
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
                "INSERT INTO fb_ads "
                "(id, adset_id, fb_ad_id, ad_name, delivery_status, first_seen_at, last_seen_at) "
                "VALUES (:id, :adset, :fb, :name, 'ACTIVE', :first_seen_at, :last_seen_at)"
            ),
            {
                "id": ids["ad"],
                "adset": ids["adset"],
                "fb": fb_ad_id,
                "name": f"{_PREFIX}AD",
                "first_seen_at": history_from,
                "last_seen_at": now,
            },
        )
        for cycle_ts, spend, clicks in (
            (history_from + timedelta(hours=10), Decimal("8.00"), 20),
            (history_from + timedelta(hours=11), Decimal("12.00"), 30),
            (live_ts, Decimal("12.00"), 30),
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
            live_ts,
        ):
            await conn.execute(
                text(
                    "INSERT INTO tracker_click_state "
                    "(id, source, click_id, ad_id, fb_ad_id, attribution_status, "
                    " registration, ftd, confirmed_deposit, registration_at, ftd_at, "
                    " confirmed_deposit_at, last_event_at) "
                    "VALUES (gen_random_uuid(), 'adsetpro', :click, :ad, :fb, 'matched_direct', "
                    " true, true, true, :ts, :ts, :ts, :ts)"
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
    ids["from_date"] = history_date.isoformat()
    ids["to_date"] = history_date.isoformat()
    ids["custom_from"] = custom_from
    ids["custom_to"] = custom_to
    ids["live_ts"] = live_ts
    ids["cabinet_start"] = cabinet_start
    ids["account_offset"] = _ACCOUNT_OFFSET_HOURS
    yield ids
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE :prefix"),
            {"prefix": f"{_PREFIX}%"},
        )
        await conn.execute(
            text(
                "DELETE FROM adsetpro_postback_events WHERE fb_ad_fk IN "
                "(SELECT id FROM fb_ads WHERE ad_name LIKE :prefix)"
            ),
            {"prefix": f"{_PREFIX}%"},
        )
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
        await conn.execute(
            text("DELETE FROM meta_account_snapshot WHERE account_id = :account_id"),
            {"account_id": _ACCOUNT_ID},
        )
        await conn.execute(
            text("DELETE FROM system_config WHERE key = 'tracker_provider_reconciliation'")
        )


@pytest.mark.asyncio
async def test_performance_uses_latest_meta_snapshot_and_exact_tracker_window(
    pg_engine, fake_redis_client, analytics_chain
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    params = {
        "period": "custom",
        "from_date": analytics_chain["from_date"],
        "to_date": analytics_chain["to_date"],
        "level": "campaign",
        "campaign_id": str(analytics_chain["campaign"]),
        "account_id": _ACCOUNT_ID,
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
    # A fresh durable worker watermark confirms that absent canonical events are
    # evidence-backed zeroes rather than merely missing rows.
    assert body["rows"][0]["redeposits"] == 0
    assert body["rows"][0]["revenue"] == "0.00"
    assert body["rows"][0]["state"] == "ready"
    assert body["state"] in {"partial", "stale"}
    assert "as_of" in body
    assert "freshness_seconds" in body
    assert body["rows"][0]["live_budget"] is None
    assert body["rows"][0]["budget_unavailable_reason"].startswith("Budget delta доступен")
    assert body["window"]["timezone"] == "Asia/Singapore"
    assert body["window"]["timezone_known"] is True
    assert body["window"]["issues"] == []
    assert body["rows"][0]["timezone_known"] is True


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
                "account_id": _ACCOUNT_ID,
            },
        )
        ad_response = await client.get(
            "/api/analytics/performance",
            params={
                "period": "today",
                "level": "ad",
                "parent_id": str(analytics_chain["adset"]),
                "account_id": _ACCOUNT_ID,
            },
        )
        budget_response = await client.get(
            "/api/analytics/live-budget",
            params={
                "campaign_id": str(analytics_chain["campaign"]),
                "account_id": _ACCOUNT_ID,
            },
        )

    assert campaign_response.status_code == 200, campaign_response.text
    campaign = campaign_response.json()
    assert campaign["window"]["is_live"] is True
    assert campaign["window"]["timezone_known"] is True
    assert campaign["rows"][0]["live_budget"]["stage"] == "mixed"
    assert campaign["rows"][0]["live_budget"]["base_budget"] == "10.00"
    assert campaign["rows"][0]["live_budget"]["base_delta"] == "2.00"

    assert ad_response.status_code == 200, ad_response.text
    assert ad_response.json()["rows"][0]["level"] == "ad"
    assert ad_response.json()["rows"][0]["id"] == str(analytics_chain["ad"])

    assert budget_response.status_code == 200, budget_response.text
    budget = budget_response.json()
    assert budget["state"] in {"ready", "partial", "stale"}
    assert budget["sources"]["tracker"]["status"] == "good"
    evidenced_point = next(point for point in budget["points"] if point["actual"] == "12.00")
    assert evidenced_point["base"] == "10.00"
    assert evidenced_point["stop"] == "8.00"


@pytest.mark.asyncio
async def test_live_budget_missing_scoped_ad_invalidates_hour(
    pg_engine, fake_redis_client, analytics_chain
):
    missing_ad_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO fb_ads "
                "(id, adset_id, fb_ad_id, ad_name, delivery_status, first_seen_at, last_seen_at) "
                "VALUES (:id, :adset, :fb, :name, 'ACTIVE', :first_seen_at, :last_seen_at)"
            ),
            {
                "id": missing_ad_id,
                "adset": analytics_chain["adset"],
                "fb": f"79{uuid.uuid4().hex[:8]}",
                "name": f"{_PREFIX}MISSING",
                "first_seen_at": analytics_chain["live_ts"],
                "last_seen_at": datetime.now(UTC),
            },
        )

    to_dt = datetime.now(UTC)
    points = await fetch_live_budget_points(
        pg_engine,
        from_dt=analytics_chain["cabinet_start"],
        to_dt=to_dt,
        account_id=_ACCOUNT_ID,
        offer_id=None,
        campaign_id=analytics_chain["campaign"],
        cabinet_boundaries={_ACCOUNT_ID: analytics_chain["cabinet_start"]},
    )
    live_bucket = analytics_chain["live_ts"].replace(minute=0, second=0, microsecond=0)
    live_point = next(point for point in points if point["ts"] == live_bucket)
    assert live_point["actual"] is None
    assert live_point["base"] is None
    assert live_point["stop"] is None
    assert live_point["available_ads"] == 1
    assert live_point["unavailable_ads"] == 1

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/live-budget",
            params={
                "campaign_id": str(analytics_chain["campaign"]),
                "account_id": _ACCOUNT_ID,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] in {"partial", "stale"}
    assert all(point["actual"] is None for point in body["points"])
    assert any(point["unavailable_ads"] >= 1 for point in body["points"])
    assert "Не все объявления подтверждены в каждом почасовом Meta-снимке" in body["issues"]


@pytest.mark.asyncio
async def test_unknown_cabinet_timezone_is_explicitly_degraded(
    pg_engine, fake_redis_client, analytics_chain
):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE meta_account_snapshot SET timezone_name = 'Definitely/Not-A-Zone' "
                "WHERE account_id = :account_id"
            ),
            {"account_id": _ACCOUNT_ID},
        )
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/performance",
            params={
                "period": "custom",
                "from_date": analytics_chain["from_date"],
                "to_date": analytics_chain["to_date"],
                "level": "campaign",
                "campaign_id": str(analytics_chain["campaign"]),
                "account_id": _ACCOUNT_ID,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["window"]["timezone"] is None
    assert body["window"]["timezone_known"] is False
    assert body["window"]["issues"]
    assert body["sources"]["meta"]["status"] == "degraded"
    assert body["sources"]["meta"]["missing_timezone_account_ids"] == [_ACCOUNT_ID]
    assert body["rows"][0]["cabinet_timezone"] is None
    assert body["rows"][0]["timezone_known"] is False
    assert body["rows"][0]["timezone_state"] == "unknown"


@pytest.mark.asyncio
async def test_nullable_meta_snapshot_stays_unknown_in_performance(
    pg_engine, fake_redis_client, analytics_chain
):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_metrics SET clicks = NULL WHERE ad_id = :ad_id AND cycle_ts = :cycle_ts"
            ),
            {
                "ad_id": analytics_chain["ad"],
                "cycle_ts": analytics_chain["from"] + timedelta(hours=11),
            },
        )
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/performance",
            params={
                "period": "custom",
                "from_date": analytics_chain["from_date"],
                "to_date": analytics_chain["to_date"],
                "level": "campaign",
                "campaign_id": str(analytics_chain["campaign"]),
                "account_id": _ACCOUNT_ID,
            },
        )

    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["spend"] == "12.00"
    assert row["clicks"] is None
    assert row["cpc"] is None
    assert row["state"] == "partial"


@pytest.mark.asyncio
async def test_event_revenue_null_is_not_counted_as_zero_roas(
    pg_engine,
    fake_redis_client,
    analytics_chain,
):
    event_at = analytics_chain["from"] + timedelta(hours=12)
    async with pg_engine.begin() as conn:
        event_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO adsetpro_postback_events (
                        received_at, occurred_at, click_id, fb_ad_fk, event_type,
                        revenue, currency, raw_json, signature_valid, attribution_status
                    )
                    VALUES (
                        :event_at, :event_at, :click_id, :ad_id, 'ftd',
                        NULL, 'USD', '{}'::jsonb, true, 'matched_direct'
                    )
                    RETURNING id
                    """
                ),
                {
                    "event_at": event_at,
                    "click_id": f"{_PREFIX}unknown-revenue-{uuid.uuid4().hex}",
                    "ad_id": analytics_chain["ad"],
                },
            )
        ).scalar_one()

    params = {
        "period": "custom",
        "from_date": analytics_chain["from_date"],
        "to_date": analytics_chain["to_date"],
        "level": "campaign",
        "campaign_id": str(analytics_chain["campaign"]),
        "account_id": _ACCOUNT_ID,
    }
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        unknown_response = await client.get("/api/analytics/performance", params=params)

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE adsetpro_postback_events SET revenue = 0 WHERE id = :event_id"),
                {"event_id": event_id},
            )

        known_zero_response = await client.get("/api/analytics/performance", params=params)

    assert unknown_response.status_code == 200, unknown_response.text
    unknown = unknown_response.json()
    unknown_row = unknown["rows"][0]
    assert unknown_row["state"] == "partial"
    assert unknown_row["revenue"] is None
    assert unknown_row["roas"] is None
    assert unknown_row["roi_pct"] is None
    assert unknown["totals"]["revenue"] is None
    assert unknown["totals"]["roas"] is None

    assert known_zero_response.status_code == 200, known_zero_response.text
    known_zero = known_zero_response.json()
    known_zero_row = known_zero["rows"][0]
    assert known_zero_row["state"] == "ready"
    assert known_zero_row["revenue"] == "0.00"
    assert known_zero_row["roas"] == "0.0000"
    assert known_zero_row["roi_pct"] == "-100.00"


@pytest.mark.asyncio
async def test_tracker_provider_audit_distinguishes_known_zero_from_unknown(
    pg_engine, fake_redis_client, analytics_chain
):
    no_conversion_ad = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO fb_ads "
                "(id, adset_id, fb_ad_id, ad_name, delivery_status, first_seen_at, last_seen_at) "
                "VALUES (:id, :adset, :fb, :name, 'ACTIVE', :first_seen_at, :last_seen_at)"
            ),
            {
                "id": no_conversion_ad,
                "adset": analytics_chain["adset"],
                "fb": f"79{uuid.uuid4().hex[:8]}",
                "name": f"{_PREFIX}ZERO",
                "first_seen_at": analytics_chain["from"],
                "last_seen_at": datetime.now(UTC),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO ad_metrics "
                "(id, ad_id, cycle_ts, spend, impressions, clicks, leads) "
                "VALUES (gen_random_uuid(), :ad, :ts, 5, 500, 5, 0)"
            ),
            {
                "ad": no_conversion_ad,
                "ts": analytics_chain["from"] + timedelta(hours=11),
            },
        )

    params = {
        "period": "custom",
        "from_date": analytics_chain["from_date"],
        "to_date": analytics_chain["to_date"],
        "level": "campaign",
        "campaign_id": str(analytics_chain["campaign"]),
        "account_id": _ACCOUNT_ID,
    }
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        healthy_response = await client.get("/api/analytics/performance", params=params)

        async with pg_engine.begin() as conn:
            pending_event = (
                await conn.execute(
                    text(
                        "INSERT INTO adsetpro_postback_events "
                        "(received_at, occurred_at, click_id, fb_ad_id, fb_ad_fk, event_type, "
                        "raw_json, signature_valid, attribution_status) "
                        "VALUES (:ts, :ts, :click_id, :fb_ad_id, :ad_id, 'registration', "
                        "'{}'::jsonb, true, 'matched_direct') "
                        "RETURNING id, received_at"
                    ),
                    {
                        "ts": analytics_chain["from"] + timedelta(hours=12),
                        "click_id": f"{_PREFIX}pending-{uuid.uuid4().hex}",
                        "fb_ad_id": f"pending-{uuid.uuid4().hex}",
                        "ad_id": no_conversion_ad,
                    },
                )
            ).one()
            await conn.execute(
                text(
                    "INSERT INTO task_queue "
                    "(task_type, status, idempotency_key, payload, requested_by, lane) "
                    "VALUES ('tracker_event_process', 'pending', :key, "
                    "jsonb_build_object('event_id', CAST(:event_id AS BIGINT), "
                    "'received_at', CAST(:received_at AS text)), 'test', 'background')"
                ),
                {
                    "key": f"{_PREFIX}pending-{uuid.uuid4().hex}",
                    "event_id": pending_event.id,
                    "received_at": pending_event.received_at.isoformat(),
                },
            )

        pending_response = await client.get("/api/analytics/performance", params=params)

        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key LIKE :prefix"),
                {"prefix": f"{_PREFIX}%"},
            )
            await conn.execute(
                text("DELETE FROM adsetpro_postback_events WHERE id = :event_id"),
                {"event_id": pending_event.id},
            )
            await conn.execute(
                text("DELETE FROM system_config WHERE key = 'tracker_provider_reconciliation'")
            )

        unknown_response = await client.get("/api/analytics/performance", params=params)

        failed_audit_at = datetime.now(UTC)
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO system_config (key, value, description) "
                    "VALUES ('tracker_provider_reconciliation', jsonb_build_object("
                    "'status', 'error', "
                    "'checked_at', CAST(:checked_at AS text), "
                    "'window_start', CAST(:window_start AS text), "
                    "'window_end', CAST(:window_end AS text), "
                    "'drift_after', 0, "
                    "'skipped', 0"
                    "), 'Analytics integration provider audit') "
                    "ON CONFLICT (key) DO UPDATE SET "
                    "value = EXCLUDED.value, updated_at = NOW()"
                ),
                {
                    "checked_at": failed_audit_at.isoformat(),
                    "window_start": analytics_chain["from"].isoformat(),
                    "window_end": failed_audit_at.isoformat(),
                },
            )

        degraded_response = await client.get("/api/analytics/performance", params=params)

    assert healthy_response.status_code == 200, healthy_response.text
    healthy = healthy_response.json()
    assert healthy["sources"]["tracker"]["status"] == "good"
    assert healthy["rows"][0]["registrations"] == 1
    assert healthy["rows"][0]["redeposits"] == 0
    assert healthy["rows"][0]["revenue"] == "0.00"

    assert pending_response.status_code == 200, pending_response.text
    pending = pending_response.json()
    assert pending["sources"]["tracker"]["status"] == "degraded"
    assert any("Ожидают применения Tracker-события: 1" in issue for issue in pending["issues"])
    assert pending["rows"][0]["registrations"] is None

    assert unknown_response.status_code == 200, unknown_response.text
    unknown = unknown_response.json()
    assert unknown["sources"]["tracker"]["status"] == "unknown"
    assert unknown["rows"][0]["registrations"] is None
    assert unknown["rows"][0]["redeposits"] is None
    assert unknown["rows"][0]["revenue"] is None
    assert any("provider audit" in issue for issue in unknown["issues"])

    assert degraded_response.status_code == 200, degraded_response.text
    degraded = degraded_response.json()
    assert degraded["sources"]["tracker"]["status"] == "degraded"
    assert degraded["rows"][0]["registrations"] is None
    assert degraded["rows"][0]["redeposits"] is None
    assert degraded["rows"][0]["revenue"] is None
    assert any("error" in issue for issue in degraded["issues"])


@pytest.mark.asyncio
async def test_performance_excludes_ads_without_catalog_overlap(
    pg_engine, fake_redis_client, analytics_chain
):
    old_ad = uuid.uuid4()
    future_ad = uuid.uuid4()
    async with pg_engine.begin() as conn:
        for ad_id, suffix, is_active, first_seen_at, last_seen_at in (
            (
                old_ad,
                "OLD",
                False,
                analytics_chain["custom_from"] - timedelta(days=10),
                analytics_chain["custom_from"] - timedelta(seconds=1),
            ),
            (
                future_ad,
                "FUTURE",
                True,
                analytics_chain["custom_to"] + timedelta(seconds=1),
                datetime.now(UTC),
            ),
        ):
            await conn.execute(
                text(
                    "INSERT INTO fb_ads "
                    "(id, adset_id, fb_ad_id, ad_name, delivery_status, is_active, "
                    " first_seen_at, last_seen_at) "
                    "VALUES (:id, :adset, :fb, :name, 'PAUSED', :is_active, "
                    " :first_seen_at, :last_seen_at)"
                ),
                {
                    "id": ad_id,
                    "adset": analytics_chain["adset"],
                    "fb": f"79{uuid.uuid4().hex[:8]}",
                    "name": f"{_PREFIX}{suffix}",
                    "is_active": is_active,
                    "first_seen_at": first_seen_at,
                    "last_seen_at": last_seen_at,
                },
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/performance",
            params={
                "period": "custom",
                "from_date": analytics_chain["from_date"],
                "to_date": analytics_chain["to_date"],
                "level": "ad",
                "parent_id": str(analytics_chain["adset"]),
                "account_id": _ACCOUNT_ID,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["rows"][0]["id"] == str(analytics_chain["ad"])
    assert body["rows"][0]["spend"] == "12.00"


@pytest.mark.asyncio
async def test_daypart_timezone_is_server_owned(pg_engine, fake_redis_client):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/daypart",
            params={"timezone": "Europe/Definitely-Not-A-Zone"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timezone"] != "Europe/Definitely-Not-A-Zone"
    assert body["scope"]["display_timezone"] == body["timezone"]


@pytest.mark.asyncio
async def test_daypart_is_sparse_and_keeps_missing_source_metrics_null(
    pg_engine, fake_redis_client, analytics_chain
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/daypart",
            params={
                "from_iso": analytics_chain["from"].isoformat(),
                "to_iso": analytics_chain["to"].isoformat(),
                "campaign_id": str(analytics_chain["campaign"]),
                "account_id": _ACCOUNT_ID,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] in {"partial", "stale"}
    assert body["as_of"] is not None
    assert body["freshness_seconds"] is not None
    assert 0 < len(body["cells"]) < 168
    assert len({(cell["weekday"], cell["hour"]) for cell in body["cells"]}) == len(body["cells"])
    assert any(cell["clicks"] == 10 for cell in body["cells"])
    assert any(cell["clicks"] is None for cell in body["cells"])
    assert all(cell["registrations"] is not None for cell in body["cells"])
    assert all(cell["ftds"] is not None for cell in body["cells"])
    fully_missing_ts = analytics_chain["from"] + timedelta(hours=12)
    fully_missing_local = fully_missing_ts.astimezone(ZoneInfo(body["timezone"]))
    fully_missing = next(
        cell
        for cell in body["cells"]
        if cell["weekday"] == fully_missing_local.isoweekday()
        and cell["hour"] == fully_missing_local.hour
    )
    assert fully_missing["clicks"] is None


@pytest.mark.asyncio
async def test_daypart_missing_expected_ad_snapshot_invalidates_hour(
    pg_engine, fake_redis_client, analytics_chain
):
    missing_ad_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO fb_ads "
                "(id, adset_id, fb_ad_id, ad_name, delivery_status, first_seen_at, last_seen_at) "
                "VALUES (:id, :adset, :fb, :name, 'ACTIVE', :first_seen_at, :last_seen_at)"
            ),
            {
                "id": missing_ad_id,
                "adset": analytics_chain["adset"],
                "fb": f"79{uuid.uuid4().hex[:8]}",
                "name": f"{_PREFIX}DAYPART_MISSING",
                "first_seen_at": analytics_chain["from"],
                "last_seen_at": datetime.now(UTC),
            },
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/daypart",
            params={
                "from_iso": analytics_chain["from"].isoformat(),
                "to_iso": analytics_chain["to"].isoformat(),
                "campaign_id": str(analytics_chain["campaign"]),
                "account_id": _ACCOUNT_ID,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    target_ts = analytics_chain["from"] + timedelta(hours=11)
    target_local = target_ts.astimezone(ZoneInfo(body["timezone"]))
    target = next(
        cell
        for cell in body["cells"]
        if cell["weekday"] == target_local.isoweekday() and cell["hour"] == target_local.hour
    )
    assert target["clicks"] is None
    assert target["registrations"] == 1
    assert target["ftds"] == 1
    assert body["state"] in {"partial", "stale"}


@pytest.mark.asyncio
async def test_daypart_does_not_move_clicks_across_gaps_and_resets_at_cabinet_day(
    pg_engine, fake_redis_client, analytics_chain
):
    gap_ts = analytics_chain["from"] + timedelta(hours=13)
    before_reset_ts = analytics_chain["from"] + timedelta(hours=15)
    reset_ts = analytics_chain["from"] + timedelta(hours=16)
    async with pg_engine.begin() as conn:
        for cycle_ts, spend, clicks in (
            (gap_ts, Decimal("15.00"), 45),
            (before_reset_ts, Decimal("20.00"), 100),
            (reset_ts, Decimal("1.00"), 3),
        ):
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics "
                    "(id, ad_id, cycle_ts, spend, impressions, clicks, leads) "
                    "VALUES (gen_random_uuid(), :ad, :ts, :spend, 1000, :clicks, 2)"
                ),
                {
                    "ad": analytics_chain["ad"],
                    "ts": cycle_ts,
                    "spend": spend,
                    "clicks": clicks,
                },
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/analytics/daypart",
            params={
                "from_iso": analytics_chain["from"].isoformat(),
                "to_iso": analytics_chain["to"].isoformat(),
                "campaign_id": str(analytics_chain["campaign"]),
                "account_id": _ACCOUNT_ID,
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    cells = {(cell["weekday"], cell["hour"]): cell for cell in body["cells"]}
    timezone = ZoneInfo(body["timezone"])
    gap_local = gap_ts.astimezone(timezone)
    reset_local = reset_ts.astimezone(timezone)
    gap_cell = cells[(gap_local.isoweekday(), gap_local.hour)]
    reset_cell = cells[(reset_local.isoweekday(), reset_local.hour)]

    assert gap_cell["clicks"] is None
    assert reset_cell["clicks"] == 3
    assert body["state"] in {"partial", "stale"}
    assert "Не все почасовые интервалы подтверждены обоими источниками" in body["issues"]
