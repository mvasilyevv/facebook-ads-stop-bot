from __future__ import annotations

import asyncio
from pathlib import Path

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


def _state(*, step: int = 2) -> dict[str, object]:
    return {
        "current_step": step,
        "start": {"mode": "new", "preset_id": None},
        "identity": {
            "act_id": "123",
            "page_id": "100",
            "pixel_id": "200",
            "account_context_state": "ready",
            "timezone_name": "America/New_York",
            "currency": "USD",
            "currency_exponent": 2,
            "account_context_observed_at": "2026-08-09T10:00:00Z",
            "account_context_issue": None,
            "offer_code": "GH_CR",
            "byer_tag": "",
        },
        "goal": {
            "objective": "OUTCOME_SALES",
            "optimization_goal": "OFFSITE_CONVERSIONS",
            "custom_event_type": "PURCHASE",
            "destination_link": "https://example.com",
            "cta": "PLAY_GAME",
            "text_optimizations": "OPT_OUT",
            "start_date": "2099-07-01",
            "budget_level": "campaign",
            "daily_budget": "50.00",
            "bid_amount": "1.50",
            "bid_strategy": "COST_CAP",
            "countries": ["DE"],
            "age_min": 21,
            "age_max": 65,
            "advantage_audience": True,
            "click_through_days": 1,
            "view_through_days": 1,
            "ad_text_mode": "none",
            "ad_text_primary": "",
        },
        "structure": {"campaigns": [{"key": "static", "label": None, "adset_count": 2}]},
        "creatives": {
            "upload_id": "valid-upload",
            "concepts": [
                {
                    "ref": "a.jpg",
                    "original_name": "a.jpg",
                    "size_bytes": 1,
                    "content_type": "image/jpeg",
                    "campaign_keys": ["static"],
                }
            ],
            "copies_per_concept": None,
        },
    }


def _config() -> dict[str, object]:
    return {
        "act_id": "123",
        "page_id": "100",
        "pixel_id": "200",
        "offer_code": "GH_CR",
        "destination_link": "https://example.com",
        "start_date": "2099-07-01",
        "countries": ["DE"],
        "budget_level": "campaign",
        "daily_budget": "50.00",
        "bid_strategy": "COST_CAP",
        "bid_amount": "1.50",
        "creo_root": "valid-upload",
        "campaigns": [{"key": "static", "adset_count": 2, "concept_refs": ["a.jpg"]}],
    }


@pytest_asyncio.fixture
async def clean_campaign_draft(pg_engine, tmp_path: Path, monkeypatch):
    upload_dir = tmp_path / "valid-upload"
    upload_dir.mkdir()
    (upload_dir / "a.jpg").write_bytes(b"a")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    async def clean(*, seed: bool) -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))
            await conn.execute(text("DELETE FROM campaign_run"))
            await conn.execute(text("DELETE FROM campaign_draft"))
            await conn.execute(text("DELETE FROM meta_account_snapshot WHERE account_id = '123'"))
            if seed:
                await conn.execute(
                    text(
                        """
                        INSERT INTO meta_account_snapshot(
                            account_id, timezone_name, currency, currency_observed_at
                        )
                        VALUES ('123', 'America/New_York', 'USD', clock_timestamp())
                        """
                    )
                )

    await clean(seed=True)
    yield
    await clean(seed=False)


@pytest.mark.asyncio
async def test_draft_get_put_delete_and_no_task_side_effect(
    pg_engine,
    fake_redis_client,
    clean_campaign_draft,
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        empty = await client.get("/api/tools/campaigns/draft")
        assert empty.status_code == 200
        assert empty.json() == {"draft": None}
        assert empty.headers["cache-control"] == "no-store"

        created = await client.put(
            "/api/tools/campaigns/draft",
            json={"expected_revision": 0, "state": _state()},
        )
        assert created.status_code == 200
        assert created.json()["revision"] == 1

        updated = await client.put(
            "/api/tools/campaigns/draft",
            json={"expected_revision": 1, "state": _state(step=3)},
        )
        assert updated.status_code == 200
        assert updated.json()["revision"] == 2

        deleted = await client.delete(
            "/api/tools/campaigns/draft",
            params={"expected_revision": 2},
        )
        assert deleted.status_code == 204

    async with pg_engine.connect() as conn:
        task_count = (
            await conn.execute(
                text("SELECT count(*) FROM task_queue WHERE task_type = 'campaign_create'")
            )
        ).scalar_one()
    assert task_count == 0


@pytest.mark.asyncio
async def test_draft_cas_allows_one_concurrent_writer(
    pg_engine,
    fake_redis_client,
    clean_campaign_draft,
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.put(
            "/api/tools/campaigns/draft",
            json={"expected_revision": 0, "state": _state()},
        )
        first, second = await asyncio.gather(
            client.put(
                "/api/tools/campaigns/draft",
                json={"expected_revision": 1, "state": _state(step=3)},
            ),
            client.put(
                "/api/tools/campaigns/draft",
                json={"expected_revision": 1, "state": _state(step=4)},
            ),
        )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    async with pg_engine.connect() as conn:
        revision = (await conn.execute(text("SELECT revision FROM campaign_draft"))).scalar_one()
    assert revision == 2


@pytest.mark.asyncio
async def test_launch_queues_immutable_run_then_clears_exact_draft_revision(
    pg_engine,
    fake_redis_client,
    clean_campaign_draft,
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.put(
            "/api/tools/campaigns/draft",
            json={"expected_revision": 0, "state": _state(step=7)},
        )
        launched = await client.post(
            "/api/tools/campaigns/launch",
            json={"config": _config(), "draft_revision": created.json()["revision"]},
        )

    assert launched.status_code == 202
    assert launched.json()["status"] == "queued"
    assert launched.json()["draft_cleared"] is True
    async with pg_engine.connect() as conn:
        draft_count = (await conn.execute(text("SELECT count(*) FROM campaign_draft"))).scalar_one()
        run = (await conn.execute(text("SELECT status, config FROM campaign_run"))).one()
    assert draft_count == 0
    assert run.status == "queued"
    assert run.config["account"]["currency"] == "USD"


@pytest.mark.asyncio
async def test_draft_schema_rejects_raw_runtime_or_secret_keys(
    pg_engine,
    fake_redis_client,
    clean_campaign_draft,
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    payload = _state()
    payload["api_token"] = "do-not-store"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(
            "/api/tools/campaigns/draft",
            json={"expected_revision": 0, "state": payload},
        )

    assert response.status_code == 422
    assert "do-not-store" not in response.text
