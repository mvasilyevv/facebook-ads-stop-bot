# -*- coding: utf-8 -*-
"""Контракт-тесты роутера campaigns_create — РОВНО та форма, что шлёт фронт.

CRIT-2: фронты (web `campaignWizard.buildConfig`, mini-визард) шлют ПЛОСКИЙ конфиг
(`act_id`/`daily_budget`/`countries` на верхнем уровне), а доменный CampaignConfig
вложенный. До фикса каждый validate/launch падал 422. Тут проверяем, что плоская форма
проходит 200 и корректно конвертируется во вложенный CampaignConfig.

HIGH-4: два launch с одним серверным idempotency_key → один run_id (ON CONFLICT DO NOTHING),
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
from core.ad_account_catalog import ad_account_catalog


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

    async def _truncate(*, seed_account_context: bool):
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))
            await conn.execute(text("DELETE FROM campaign_creative"))
            await conn.execute(text("DELETE FROM offer_creative_seq"))
            await conn.execute(text("DELETE FROM campaign_run"))
            await conn.execute(
                text(
                    """
                    DELETE FROM offer_rules
                    WHERE offer_id IN (
                        SELECT id FROM offers WHERE code LIKE 'CTX_%'
                    )
                    """
                )
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'CTX_%'"))
            await conn.execute(
                text("DELETE FROM meta_account_snapshot WHERE account_id IN ('123', '456')")
            )
            if seed_account_context:
                await conn.execute(
                    # Статус кабинета — часть подтверждённого контекста с
                    # миграции 0008: без него предполёт отвечает
                    # `campaign_account_status_unknown`, и позитивные тесты
                    # модуля видели бы 422 вместо самой проверки. Значение 1 —
                    # ACCOUNT_STATUS_ACTIVE, единственный подтверждённо
                    # активный кабинет; всё прочее — неизвестность.
                    text(
                        """
                        INSERT INTO meta_account_snapshot(
                            account_id,
                            timezone_name,
                            currency,
                            currency_observed_at,
                            account_status,
                            account_status_observed_at
                        )
                        VALUES ('123', 'America/New_York', 'USD', clock_timestamp(),
                                1, clock_timestamp())
                        """
                    )
                )

    await _truncate(seed_account_context=True)
    yield
    await _truncate(seed_account_context=False)


async def _campaign_write_counts(pg_engine) -> tuple[int, int, int, int]:
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM campaign_run) AS runs,
                        (
                            SELECT COUNT(*)
                            FROM task_queue
                            WHERE task_type = 'campaign_create'
                        ) AS tasks,
                        (SELECT COUNT(*) FROM campaign_creative) AS creatives,
                        (SELECT COUNT(*) FROM offer_creative_seq) AS ledgers
                    """
                )
            )
        ).one()
    return row.runs, row.tasks, row.creatives, row.ledgers


def _flat_config() -> dict:
    """Плоский конфиг — РОВНО форма фронта (web buildConfig / mini-визард).

    Источник истины: frontend/src/stores/campaignWizard.ts::buildConfig.
    """
    return {
        "act_id": "123",
        "page_id": "100",
        "pixel_id": "200",
        "offer_code": "GH_CR",
        "byer_tag": "MV",
        "destination_link": "https://example.com",
        "start_date": "2099-07-01",
        "budget_level": "campaign",
        "daily_budget": "200.00",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "countries": ["DE"],
        "age_min": 18,
        "age_max": 65,
        "advantage_audience": True,
        "click_through_days": 1,
        "view_through_days": 1,
        "ad_text": {"mode": "text", "primary": "играй"},
        "campaigns": [{"key": "static", "adset_count": 2, "concept_refs": ["a.jpg", "b.jpg"]}],
        "copies_per_concept": None,
        "creo_root": "abc123",
        "url_tags": "sub2=MV",
    }


async def _seed_multi_account_offer(pg_engine, *, seed_second_context: bool) -> str:
    """Create an offer whose only admissible launch targets are 123 and 456."""

    code = f"CTX_MULTI_{uuid.uuid4().hex[:8].upper()}"
    async with pg_engine.begin() as conn:
        offer_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO offers(code, name, is_active)
                    VALUES (:code, 'Multi-account contract', TRUE)
                    RETURNING id
                    """
                ),
                {"code": code},
            )
        ).scalar_one()
        await ad_account_catalog.replace_offer_accounts(
            conn,
            offer_id=offer_id,
            account_ids=["123", "456"],
        )
        if seed_second_context:
            await conn.execute(
                text(
                    """
                    INSERT INTO meta_account_snapshot(
                        account_id,
                        timezone_name,
                        currency,
                        currency_observed_at,
                        account_status,
                        account_status_observed_at
                    )
                    VALUES ('456', 'America/New_York', 'USD', clock_timestamp(),
                            1, clock_timestamp())
                    """
                )
            )
    return code


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
    # Два фактических concept_refs × 2 adset = 4 ads (раскладка K×N).
    assert plan["ad_count"] == 4
    assert plan["creation_policy"] == "all_paused"
    assert all(campaign["status"] == "PAUSED" for campaign in plan["campaigns"])
    assert all(
        adset["status"] == "PAUSED"
        for campaign in plan["campaigns"]
        for adset in campaign["adsets"]
    )
    # Нейминг кампании несёт оффер.
    assert "GH_CR" in plan["campaigns"][0]["name"]
    assert plan["start_time"] == "2099-07-01T00:00:00-04:00"
    assert plan["timezone_name"] == "America/New_York"
    assert plan["currency"] == "USD"
    # validate не создал ни одного run.
    async with pg_engine.connect() as conn:
        cnt = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert cnt == 0


# Плоский конфиг с бюджетом выше hard-cap → 422, run не создан (money-safe).
@pytest.mark.asyncio
async def test_validate_flat_budget_over_cap_422(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    cfg = _flat_config()
    cfg["daily_budget"] = "100000.01"
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
    assert preview.json()["message"] == "Невалидный конфиг кампании"
    assert launch.json()["message"] == "Невалидный конфиг кампании"
    async with pg_engine.connect() as conn:
        runs = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
        tasks = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'campaign_create'")
            )
        ).scalar()
    assert runs == 0
    assert tasks == 0


@pytest.mark.asyncio
async def test_missing_account_context_rejected_before_any_campaign_write(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM meta_account_snapshot WHERE account_id = '123'"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": _flat_config()},
        )

    assert response.status_code == 422
    assert await _campaign_write_counts(pg_engine) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_stale_account_context_rejected_before_any_campaign_write(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE meta_account_snapshot
                SET currency_observed_at = clock_timestamp() - interval '25 hours'
                WHERE account_id = '123'
                """
            )
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": _flat_config()},
        )

    assert response.status_code == 409
    assert await _campaign_write_counts(pg_engine) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_client_cannot_forge_account_timezone_or_currency(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    config = _flat_config()
    config.update(
        {
            "timezone_name": "Pacific/Kiritimati",
            "currency": "JPY",
            "currency_exponent": 0,
            "account_context_observed_at": "2099-01-01T00:00:00Z",
        }
    )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": config},
        )

    assert response.status_code == 422
    assert await _campaign_write_counts(pg_engine) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_active_offer_without_account_link_fails_closed_before_campaign_write(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers(code, name, is_active) "
                "VALUES ('CTX_EMPTY', 'No account membership', TRUE)"
            )
        )

    config = _flat_config()
    config["offer_code"] = "CTX_EMPTY"
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": config},
        )

    assert response.status_code == 409
    assert response.json()["message"] == "Выбранный кабинет не привязан к офферу"
    assert await _campaign_write_counts(pg_engine) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_offer_currency_mismatch_rejected_before_any_campaign_write(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    async with pg_engine.begin() as conn:
        offer_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO offers(code, name)
                    VALUES ('CTX_MISMATCH', 'Context mismatch test')
                    RETURNING id
                    """
                )
            )
        ).scalar_one()
        await ad_account_catalog.replace_offer_accounts(
            conn,
            offer_id=offer_id,
            account_ids=["123"],
        )
        await conn.execute(
            text(
                """
                INSERT INTO offer_rules(offer_id, cpa_threshold, currency)
                VALUES (:offer_id, 3.00, 'EUR')
                """
            ),
            {"offer_id": offer_id},
        )

    config = _flat_config()
    config["offer_code"] = "CTX_MISMATCH"
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": config},
        )

    assert response.status_code == 409
    assert await _campaign_write_counts(pg_engine) == (0, 0, 0, 0)


# ─────────────────────────── launch (плоская форма) ───────────────────────────


# CRIT-2: launch принимает плоскую форму → 202 queued; в БД config-снимок корректен.
@pytest.mark.asyncio
async def test_launch_accepts_flat_and_converts(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/launch", json={"config": _flat_config()})
    assert resp.status_code == 202, resp.text
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
    assert cfg["account"]["timezone_name"] == "America/New_York"
    assert cfg["account"]["currency"] == "USD"
    assert cfg["account"]["account_context_observed_at"] is not None
    assert cfg["budget"]["daily_amount"] == "200.00"
    assert cfg["budget"]["currency"] == "USD"
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
    assert first.status_code == 202, first.text
    # Повтор не падает 500 — возвращает существующий queued run (202-shape).
    assert second.status_code == 202, second.text
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


@pytest.mark.asyncio
async def test_multi_launch_keeps_success_when_sibling_preflight_fails(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    offer_code = await _seed_multi_account_offer(
        pg_engine,
        seed_second_context=False,
    )
    config = _flat_config()
    config["offer_code"] = offer_code
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": config, "ad_account_ids": ["123", "456"]},
        )

    assert response.status_code == 202, response.text
    receipt = response.json()
    assert receipt["request_state"] == "partial"
    by_account = {item["account_id"]: item for item in receipt["accounts"]}
    assert by_account["123"]["status"] == "queued"
    assert by_account["123"]["run_id"] is not None
    assert by_account["456"]["status"] == "rejected"
    assert by_account["456"]["run_id"] is None
    assert await _campaign_write_counts(pg_engine) == (1, 1, 0, 1)


@pytest.mark.asyncio
async def test_multi_launch_replay_reuses_each_account_run_without_duplicates(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    offer_code = await _seed_multi_account_offer(
        pg_engine,
        seed_second_context=True,
    )
    config = _flat_config()
    config["offer_code"] = offer_code
    body = {"config": config, "ad_account_ids": ["123", "456"]}
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.post("/api/tools/campaigns/launch", json=body)
        replay = await ac.post("/api/tools/campaigns/launch", json=body)

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    first_by_account = {item["account_id"]: item for item in first.json()["accounts"]}
    replay_by_account = {item["account_id"]: item for item in replay.json()["accounts"]}
    assert set(first_by_account) == {"123", "456"}
    assert {account_id: item["run_id"] for account_id, item in first_by_account.items()} == {
        account_id: item["run_id"] for account_id, item in replay_by_account.items()
    }
    assert all(item["replayed"] is True for item in replay_by_account.values())
    assert await _campaign_write_counts(pg_engine) == (2, 2, 0, 1)


@pytest.mark.asyncio
async def test_multi_launch_unknown_replay_returns_same_run_without_new_task(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    offer_code = await _seed_multi_account_offer(
        pg_engine,
        seed_second_context=False,
    )
    config = _flat_config()
    config["offer_code"] = offer_code
    body = {"config": config, "ad_account_ids": ["123"]}
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.post("/api/tools/campaigns/launch", json=body)
        launched = first.json()["accounts"][0]
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'failed',
                        external_started_at = NOW(),
                        completed_at = NOW(),
                        -- JSON уходит параметром: двоеточие перед словом в
                        -- литерале SQLAlchemy принимает за имя bind-параметра
                        -- и требует для него значение. Комментарии он тоже
                        -- разбирает, поэтому пример сюда не вписан.
                        result = CAST(:result AS jsonb)
                    WHERE id = :task_id
                    """
                ),
                {
                    "task_id": launched["task_id"],
                    "result": '{"outcome":"UNKNOWN","reconcile_required":true}',
                },
            )
            await conn.execute(
                text(
                    """
                    UPDATE campaign_run
                    SET status = 'failed',
                        progress = '{"stage":"failed","outcome":"UNKNOWN"}'::jsonb
                    WHERE id = :run_id
                    """
                ),
                {"run_id": uuid.UUID(launched["run_id"])},
            )
        replay = await ac.post("/api/tools/campaigns/launch", json=body)

    assert first.status_code == 202, first.text
    assert replay.status_code == 202, replay.text
    replayed = replay.json()["accounts"][0]
    assert replayed["run_id"] == launched["run_id"]
    assert replayed["status"] == "failed"
    assert replayed["replayed"] is True
    assert await _campaign_write_counts(pg_engine) == (1, 1, 0, 1)


# Второй источник количества концептов запрещён: только concept_refs определяет план.
@pytest.mark.asyncio
async def test_launch_rejects_concept_counts_in_body(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {"config": _flat_config(), "concept_counts": {"static": 3}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/launch", json=body)
    assert resp.status_code == 422, resp.text
    async with pg_engine.connect() as conn:
        runs = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert runs == 0


# Клиент не может навязать idempotency_key и склеить разные money-конфиги.
@pytest.mark.asyncio
async def test_launch_rejects_client_idempotency_key(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    cfg1 = _flat_config()
    cfg2 = _flat_config()
    cfg2["daily_budget"] = "300.00"  # другой конфиг, но тот же явный ключ
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": cfg1, "idempotency_key": "manual:fixed:key"},
        )
        second = await ac.post(
            "/api/tools/campaigns/launch",
            json={"config": cfg2, "idempotency_key": "manual:fixed:key"},
        )
    assert first.status_code == 422
    assert second.status_code == 422

    async with pg_engine.connect() as conn:
        runs = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert runs == 0
