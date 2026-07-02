# -*- coding: utf-8 -*-
"""Integration: ad_account_id прокинут во все 4 «кабинетных» эндпоинта (аудит, п.2).

Поле пишется в БД с миграции 0019, но не отдавалось фронту. Проверяем:
- GET /api/observer/scan-runs   → ScanRunRow.ad_account_id
- GET /api/dashboard/ads        → AdSnapshotOut.ad_account_id
- GET /api/dashboard/alerts     → AlertEventOut.ad_account_id
- GET /api/history/ads          → HistoryAdOut.ad_account_id
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app

PFX = "MCF"


def _make_app(engine=None, redis=None):
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def seeded_cabinet_catalog(pg_engine):
    """Каталог offer→campaign(каб 555)→adset→ad + alert_event + scan_run(каб 555)."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM alert_events WHERE created_at >= NOW() - INTERVAL '1 day'")
            )
            await conn.execute(text("DELETE FROM scan_runs WHERE ad_account_id = '555'"))
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE :p"), {"p": f"{PFX}%"})
            await conn.execute(
                text("DELETE FROM fb_adsets WHERE adset_name LIKE :p"), {"p": f"{PFX}%"}
            )
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE :p"), {"p": f"{PFX}%"}
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE :p"), {"p": f"{PFX}%"})

    await _cleanup()
    now = datetime.now(UTC)
    offer_id, campaign_id, adset_id, ad_id = (uuid.uuid4() for _ in range(4))
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active, ad_account_ids) "
                "VALUES (:i, :c, :c, TRUE, ARRAY['555'])"
            ),
            {"i": offer_id, "c": f"{PFX}_OF"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns "
                "(id, fb_campaign_id, campaign_name, offer_id, ad_account_id) "
                "VALUES (:i, '555001', :n, :o, '555')"
            ),
            {"i": campaign_id, "n": f"{PFX} campaign", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"{PFX} adset"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active) "
                "VALUES (:i, :s, :fbid, :n, TRUE)"
            ),
            {"i": ad_id, "s": adset_id, "fbid": f"{PFX}555111", "n": f"{PFX} ad"},
        )
        await conn.execute(
            text(
                """
                INSERT INTO alert_events
                    (id, ad_id, stage, state, matched_rule_codes, metrics_json,
                     created_at, open_state_token, scan_id)
                VALUES (:i, :a, 'warning', 'warning_sent', CAST(:codes AS jsonb),
                        CAST(:mj AS jsonb), :ts, :tok, 990001)
                """
            ),
            {
                "i": uuid.uuid4(),
                "a": ad_id,
                "codes": json.dumps(["cpa_stop"]),
                "mj": json.dumps({}),
                "ts": now,
                "tok": uuid.uuid4(),
            },
        )
        await conn.execute(
            text(
                """
                WITH next_id AS (SELECT nextval('scan_runs_id_seq') AS sid)
                INSERT INTO scan_runs (id, scan_id, started_at, outcome, ad_account_id)
                SELECT sid, sid, :ts, 'success', '555' FROM next_id
                """
            ),
            {"ts": now},
        )
    yield
    await _cleanup()


# Все 4 эндпоинта отдают ad_account_id='555' для засеянных данных кабинета 555.
@pytest.mark.asyncio
async def test_ad_account_id_in_four_endpoints(
    pg_engine, fake_redis_client, seeded_cabinet_catalog
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # /observer/scan-runs
        runs = (await ac.get("/api/observer/scan-runs")).json()
        run_rows = [r for r in runs["runs"] if r.get("ad_account_id") == "555"]
        assert run_rows, "scan-run кабинета 555 не найден в ответе"

        # /dashboard/ads
        ads = (await ac.get("/api/dashboard/ads")).json()
        ad = next(a for a in ads if a["fb_ad_id"] == f"{PFX}555111")
        assert ad["ad_account_id"] == "555"

        # /dashboard/alerts
        alerts = (await ac.get("/api/dashboard/alerts")).json()
        al = next(a for a in alerts if a["fb_ad_id"] == f"{PFX}555111")
        assert al["ad_account_id"] == "555"

        # /history/ads
        hist = (await ac.get("/api/history/ads")).json()
        ha = next(a for a in hist if a["fb_ad_id"] == f"{PFX}555111")
        assert ha["ad_account_id"] == "555"
