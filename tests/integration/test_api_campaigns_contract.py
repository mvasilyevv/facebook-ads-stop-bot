# -*- coding: utf-8 -*-
"""Контракт-тесты роутера campaigns_create — РОВНО та форма, что шлёт фронт.

CRIT-2: фронты (web `campaignWizard.buildConfig`, mini-визард) шлют ПЛОСКИЙ конфиг
(`act_id`/`daily_budget_cents`/`countries` на верхнем уровне), а доменный CampaignConfig
вложенный. До фикса каждый validate/launch падал 422. Тут проверяем, что плоская форма
проходит 200 и корректно конвертируется во вложенный CampaignConfig.

HIGH-4: два launch с одним idempotency_key → один run_id (ON CONFLICT DO NOTHING),
НЕ 500 и НЕ дубль залива (=без двойного открута бюджета).

НЕ гонять на боевой :5433 — нужен изолированный <POSTGRES_DB>_test (фикстура pg_engine).

ВНИМАНИЕ (cross-stream): launch требует task_queue.task_type CHECK с 'campaign_create'
(миграция стрима campaign_creator_worker) — иначе launch упадёт на CHECK-constraint.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(*, engine=None, redis=None):
    """Собирает FastAPI с явными override engine/redis."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_campaigns(pg_engine, tmp_path, monkeypatch):
    """Чистит БД и поднимает реальный upload-набор плоского frontend-конфига."""

    upload_dir = tmp_path / "abc123"
    upload_dir.mkdir()
    (upload_dir / "a.jpg").write_bytes(b"a")
    (upload_dir / "b.jpg").write_bytes(b"b")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))
            await conn.execute(text("DELETE FROM campaign_run"))

    await _truncate()
    yield
    await _truncate()


def _flat_config() -> dict:
    """Плоский конфиг — РОВНО форма фронта (web buildConfig / mini-визард).

    Источник истины: frontend/src/stores/campaignWizard.ts::buildConfig.
    """
    return {
        "act_id": "123",
        "page_id": "100",
        "pixel_id": "200",
        "tz_offset": -7,
        "offer_code": "GH_CR",
        "byer_tag": "MV",
        "destination_link": "https://example.com",
        "start_date": "2026-07-01",
        "budget_level": "campaign",
        "daily_budget_cents": 20000,
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "countries": ["DE"],
        "age_min": 18,
        "age_max": 65,
        "advantage_audience": True,
        "click_through_days": 1,
        "view_through_days": 1,
        "ad_text": {"mode": "text", "primary": "играй"},
        "campaigns": [
            {"key": "static", "kind": "image", "adset_count": 2, "concept_refs": ["a.jpg", "b.jpg"]}
        ],
        "copies_per_concept": None,
        "creo_root": "abc123",
        "launch_state": "campaign_paused",
        "url_tags": "sub2=MV",
    }


# ─────────────────────────── validate (плоская форма) ───────────────────────────


# CRIT-2: validate принимает ПЛОСКУЮ форму фронта → 200 (не 422), план верный.
@pytest.mark.asyncio
async def test_validate_accepts_flat_frontend_config(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/validate", json={"config": _flat_config()})
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["offer_code"] == "GH_CR"
    assert plan["campaign_count"] == 1
    assert plan["adset_count"] == 2
    # concept_counts=2 (a.jpg,b.jpg) × 2 adset = 4 ads (раскладка K×N).
    assert plan["ad_count"] == 4
    assert plan["launch_state"] == "campaign_paused"
    # Нейминг кампании несёт оффер.
    assert "GH_CR" in plan["campaigns"][0]["name"]
    # validate не создал ни одного run.
    async with pg_engine.connect() as conn:
        cnt = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert cnt == 0


# Плоский конфиг с бюджетом выше hard-cap → 422, run не создан (money-safe).
@pytest.mark.asyncio
async def test_validate_flat_budget_over_cap_422(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    cfg = _flat_config()
    cfg["daily_budget_cents"] = 100_000_00 + 1
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/validate", json={"config": cfg})
    assert resp.status_code == 422


# Старый браузерный черновик может смешать новый upload_id со ссылкой на файл из
# предыдущего набора. validate и launch обязаны синхронно отклонить его до run/task.
@pytest.mark.asyncio
async def test_stale_concept_ref_rejected_before_enqueue(
    pg_engine, fake_redis_client, clean_campaigns
):
    cfg = _flat_config()
    cfg["campaigns"][0]["concept_refs"] = ["from-another-upload.jpg"]
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        preview = await ac.post("/api/tools/campaigns/validate", json={"config": cfg})
        launch = await ac.post("/api/tools/campaigns/launch", json={"config": cfg})

    assert preview.status_code == 422
    assert launch.status_code == 422
    assert "шаг 5" in preview.json()["detail"]
    assert "шаг 5" in launch.json()["detail"]
    async with pg_engine.connect() as conn:
        runs = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
        tasks = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'campaign_create'")
            )
        ).scalar()
    assert runs == 0
    assert tasks == 0


# ─────────────────────────── launch (плоская форма) ───────────────────────────


# CRIT-2: launch принимает плоскую форму → 201; в БД config-снимок корректен.
@pytest.mark.asyncio
async def test_launch_accepts_flat_and_converts(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/launch", json={"config": _flat_config()})
    assert resp.status_code == 201, resp.text
    out = resp.json()
    run_id = out["run_id"]
    assert out["status"] == "queued"
    assert out["task_id"] is not None

    # config-снимок в БД — уже доменный вложенный (account/budget/targeting).
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT config FROM campaign_run WHERE id = :rid"),
                {"rid": uuid.UUID(run_id)},
            )
        ).first()
    cfg = row.config
    assert cfg["account"]["act_id"] == "123"
    assert cfg["account"]["tz_offset"] == "-07:00"
    assert cfg["budget"]["daily_cents"] == 20000
    # +AQ применяется через computed geo_countries (исполнитель добавит AQ к Meta);
    # в снимке countries сырой, проверяем что add_antarctica включён и DE на месте.
    assert cfg["targeting"]["countries"] == ["DE"]
    assert cfg["targeting"]["add_antarctica"] is True
    assert cfg["offer_code"] == "GH_CR"


# HIGH-4: два launch с одним конфигом (один idempotency_key) → один run_id, без 500.
@pytest.mark.asyncio
async def test_launch_idempotent_same_key_one_run(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.post("/api/tools/campaigns/launch", json={"config": _flat_config()})
        second = await ac.post("/api/tools/campaigns/launch", json={"config": _flat_config()})
    assert first.status_code == 201, first.text
    # Повтор не падает 500 — возвращает существующий run (201-shape).
    assert second.status_code == 201, second.text
    a, b = first.json(), second.json()
    assert a["run_id"] == b["run_id"]
    assert a["idempotency_key"] == b["idempotency_key"]
    assert a["task_id"] == b["task_id"]

    async with pg_engine.connect() as conn:
        runs = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
        tasks = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'campaign_create'")
            )
        ).scalar()
    # Money-инвариант: ровно один run и одна задача — без дубля залива.
    assert runs == 1
    assert tasks == 1


# launch принимает явный concept_counts в теле (симметрия с validate) → 201, один run.
# Проводка K в launch не ломает залив: спека сверяется с той же раскладкой, что показал validate.
@pytest.mark.asyncio
async def test_launch_accepts_concept_counts_in_body(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {"config": _flat_config(), "concept_counts": {"static": 3}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/launch", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "queued"
    async with pg_engine.connect() as conn:
        runs = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert runs == 1


# HIGH-4: явный одинаковый idempotency_key с РАЗНЫМ конфигом → тот же run (ключ — истина).
@pytest.mark.asyncio
async def test_launch_explicit_key_dedup(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    cfg1 = _flat_config()
    cfg2 = _flat_config()
    cfg2["daily_budget_cents"] = 30000  # другой конфиг, но тот же явный ключ
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": cfg1, "idempotency_key": "manual:fixed:key"},
        )
        second = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": cfg2, "idempotency_key": "manual:fixed:key"},
        )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["run_id"] == second.json()["run_id"]

    async with pg_engine.connect() as conn:
        runs = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert runs == 1
