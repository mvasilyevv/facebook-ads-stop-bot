# -*- coding: utf-8 -*-
"""Интеграционные тесты роутера campaigns_create — реальная БД.

Паттерн: app = _make_app(engine=pg_engine, redis=fake_redis), AsyncClient +
ASGITransport (без живого HTTP-сервера). Каждый тест изолирован clean-фикстурой.

НЕ гонять на боевой :5433 — нужен изолированный <POSTGRES_DB>_test (фикстура pg_engine).

ВНИМАНИЕ (cross-stream): тест launch требует, чтобы task_queue.task_type CHECK
включал 'campaign_create' (ORM core/models/tasks/task_queue.py + миграция —
стрим campaign_creator_worker). До этого launch-тест упадёт на CHECK-constraint.
"""

from __future__ import annotations

import asyncio
import json
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
    """Чистит campaign_run/campaign_preset/task_queue до и после теста.

    Также сбрасывает реестр креативов (campaign_creative) и счётчики
    нумерации (offer_creative_seq) для полной изоляции тестов.
    """

    upload_dir = tmp_path / "valid-upload"
    upload_dir.mkdir()
    (upload_dir / "a.jpg").write_bytes(b"a")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    async def _truncate(*, seed_account_context: bool):
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))
            await conn.execute(text("DELETE FROM campaign_creative"))
            await conn.execute(text("DELETE FROM offer_creative_seq"))
            await conn.execute(text("DELETE FROM campaign_run"))
            await conn.execute(text("DELETE FROM campaign_preset"))
            await conn.execute(text("DELETE FROM meta_account_snapshot WHERE account_id = '123'"))
            if seed_account_context:
                await conn.execute(
                    text(
                        """
                        INSERT INTO meta_account_snapshot(
                            account_id,
                            timezone_name,
                            currency,
                            currency_observed_at
                        )
                        VALUES ('123', 'America/New_York', 'USD', clock_timestamp())
                        """
                    )
                )

    await _truncate(seed_account_context=True)
    yield
    await _truncate(seed_account_context=False)


def _valid_config() -> dict:
    """Канонический плоский frontend config (1 concept × 2 adset)."""
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
        "campaigns": [
            {
                "key": "static",
                "adset_count": 2,
                "concept_refs": ["a.jpg"],
            }
        ],
    }


def _preset_body(name: str = "GH base") -> dict:
    return {"name": name, "act_id": "act_1", "page_id": "100", "pixel_id": "200"}


# ─────────────────────────── presets CRUD ───────────────────────────


# Пустая БД → пустой список пресетов, не ошибка.
@pytest.mark.asyncio
async def test_list_presets_empty(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/tools/campaigns/presets")
    assert resp.status_code == 200
    assert resp.json() == []


# Создание пресета применяет SOP-дефолты и возвращает id.
@pytest.mark.asyncio
async def test_create_preset_applies_defaults(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/presets", json=_preset_body())
    assert resp.status_code == 201
    data = resp.json()
    assert data["objective"] == "OUTCOME_SALES"
    assert data["custom_event_type"] == "PURCHASE"
    assert data["special_ad_categories"] == ["NONE"]
    assert uuid.UUID(data["id"])


# Дубль имени пресета → 409.
@pytest.mark.asyncio
async def test_create_preset_duplicate_name_conflict(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/tools/campaigns/presets", json=_preset_body("dup"))
        resp = await ac.post("/api/tools/campaigns/presets", json=_preset_body("dup"))
    assert resp.status_code == 409


# PUT обновляет пресет; 404 для несуществующего id.
@pytest.mark.asyncio
async def test_update_preset(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        created = (await ac.post("/api/tools/campaigns/presets", json=_preset_body())).json()
        body = _preset_body()
        body["cta"] = "DOWNLOAD"
        resp = await ac.put(f"/api/tools/campaigns/presets/{created['id']}", json=body)
        assert resp.status_code == 200
        assert resp.json()["cta"] == "DOWNLOAD"

        missing = await ac.put(f"/api/tools/campaigns/presets/{uuid.uuid4()}", json=body)
        assert missing.status_code == 404


# DELETE удаляет пресет; 404 для несуществующего.
@pytest.mark.asyncio
async def test_delete_preset(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        created = (await ac.post("/api/tools/campaigns/presets", json=_preset_body())).json()
        resp = await ac.delete(f"/api/tools/campaigns/presets/{created['id']}")
        assert resp.status_code == 204
        again = await ac.delete(f"/api/tools/campaigns/presets/{created['id']}")
        assert again.status_code == 404


# ─────────────────────────── validate ───────────────────────────


# validate возвращает план: число кампаний/adset/ads + нейминг, без создания run.
@pytest.mark.asyncio
async def test_validate_returns_plan(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/validate", json={"config": _valid_config()})
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["campaign_count"] == 1
    assert plan["adset_count"] == 2
    # Один реальный concept_ref → 2 adset × 1 = 2 ads.
    assert plan["ad_count"] == 2
    assert plan["creation_policy"] == "all_paused"
    assert plan["campaigns"][0]["status"] == "PAUSED"
    assert all(adset["status"] == "PAUSED" for adset in plan["campaigns"][0]["adsets"])
    assert "GH_CR" in plan["campaigns"][0]["name"]
    assert plan["start_date"] == "2099-07-01"
    assert plan["start_time"] == "2099-07-01T00:00:00-04:00"
    assert plan["timezone_name"] == "America/New_York"
    assert plan["currency"] == "USD"
    assert plan["account_context_observed_at"] is not None

    # validate не создал ни одного run.
    async with pg_engine.connect() as conn:
        cnt = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert cnt == 0


# Невалидный конфиг (концепт с неизвестным расширением) → 422 (валидатор CampaignBlock,
# orphan-защита: уникализатор знает только image/video).
@pytest.mark.asyncio
async def test_validate_invalid_config_422(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    cfg = _valid_config()
    cfg["campaigns"][0]["concept_refs"] = ["broken.txt"]  # неизвестное расширение → reject
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/validate", json={"config": cfg})
    assert resp.status_code == 422


# ─────────────────────────── launch ───────────────────────────


# launch создаёт campaign_run(queued) + task_queue(campaign_create) атомарно.
@pytest.mark.asyncio
async def test_launch_creates_run_and_task(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
    assert resp.status_code == 201
    out = resp.json()
    run_id = out["run_id"]
    assert out["status"] == "queued"
    assert out["task_id"] is not None
    assert out["idempotency_key"]

    async with pg_engine.connect() as conn:
        run = (
            await conn.execute(
                text("SELECT status, idempotency_key, config FROM campaign_run WHERE id = :rid"),
                {"rid": uuid.UUID(run_id)},
            )
        ).first()
        task = (
            await conn.execute(
                text(
                    "SELECT task_type, status, payload->>'run_id' AS run_id, "
                    "payload->>'account_id' AS account_id, "
                    "payload->>'currency' AS currency, "
                    "payload->>'currency_exponent' AS currency_exponent, "
                    "payload->>'cabinet_timezone' AS cabinet_timezone, "
                    "payload->>'account_context_observed_at' AS context_observed_at, "
                    "idempotency_key, "
                    "lane, priority, available_at, deadline_at, lease_token, correlation_id "
                    "FROM task_queue WHERE id = :tid"
                ),
                {"tid": out["task_id"]},
            )
        ).first()
    assert run.status == "queued"
    assert task.task_type == "campaign_create"
    assert task.status == "pending"
    assert task.run_id == run_id
    assert task.account_id == "123"
    assert task.currency == "USD"
    assert task.currency_exponent == "2"
    assert task.cabinet_timezone == "America/New_York"
    assert task.context_observed_at is not None
    assert task.lane == "bulk"
    assert task.priority == 20
    assert task.available_at is not None
    assert task.deadline_at is not None
    assert task.deadline_at > task.available_at
    assert task.lease_token == 0
    assert task.correlation_id is not None
    # Money-safety: один idempotency_key у run и задачи.
    assert task.idempotency_key == run.idempotency_key == out["idempotency_key"]
    assert run.config["account"] == {
        "act_id": "123",
        "account_context_observed_at": task.context_observed_at,
        "currency": "USD",
        "currency_exponent": 2,
        "page_id": "100",
        "pixel_id": "200",
        "timezone_name": "America/New_York",
    }
    assert run.config["budget"]["currency"] == "USD"
    assert run.config["budget"]["daily_amount"] == "50.00"


# Повторный launch того же конфига идемпотентен: тот же run, без дубля задачи.
@pytest.mark.asyncio
async def test_launch_idempotent(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        second = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
    assert first["run_id"] == second["run_id"]
    assert first["idempotency_key"] == second["idempotency_key"]

    async with pg_engine.connect() as conn:
        runs = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
        tasks = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'campaign_create'")
            )
        ).scalar()
    assert runs == 1
    assert tasks == 1


# launch с невалидным бюджетом (выше hard-cap) → 422, run не создан.
@pytest.mark.asyncio
async def test_launch_rejects_budget_over_cap(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    cfg = _valid_config()
    cfg["daily_budget"] = "100000.01"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/launch", json={"config": cfg})
    assert resp.status_code == 422
    async with pg_engine.connect() as conn:
        cnt = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert cnt == 0


# Плоский конфиг с блоком без концептов → 422 ДО создания run (fail-fast против
# обречённого залива: воркер всё равно упал бы на resolve_concepts, но без мусора в истории).
@pytest.mark.asyncio
async def test_launch_rejects_block_without_concepts(pg_engine, fake_redis_client, clean_campaigns):
    flat = {
        "act_id": "123",
        "page_id": "100",
        "pixel_id": "200",
        "offer_code": "GH_CR",
        "destination_link": "https://example.com",
        "daily_budget": "200.00",
        "bid_amount": "1.50",
        "countries": ["DE"],
        "campaigns": [{"key": "video", "adset_count": 2, "concept_refs": []}],
    }
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/launch", json={"config": flat})
    assert resp.status_code == 422
    assert resp.json()["message"] == "Параметры запроса не прошли проверку"
    async with pg_engine.connect() as conn:
        cnt = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert cnt == 0


# ─────────────────────────── upload ───────────────────────────

# Magic-байты валидных файлов (head >= 12 для сниффера).
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 64
_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
_PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 64


# upload стримит файлы на диск и возвращает refs с размерами (суммарный total_bytes).
@pytest.mark.asyncio
async def test_upload_streams_and_returns_refs(pg_engine, fake_redis_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    files = [
        ("files", ("a.jpg", _JPEG, "image/jpeg")),
        ("files", ("b.mp4", _MP4, "video/mp4")),
    ]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/upload", files=files)
    assert resp.status_code == 200
    data = resp.json()
    assert data["upload_id"]
    refs = {c["ref"]: c for c in data["concepts"]}
    assert set(refs) == {"a.jpg", "b.mp4"}
    assert refs["a.jpg"]["size_bytes"] == len(_JPEG)
    assert refs["b.mp4"]["size_bytes"] == len(_MP4)
    assert data["total_bytes"] == len(_JPEG) + len(_MP4)
    # Файлы реально на диске в папке upload_id.
    assert (tmp_path / data["upload_id"] / "a.jpg").read_bytes() == _JPEG


# Повторная загрузка с upload_id дополняет тот же набор: refs из первой загрузки
# остаются физически рядом с новыми и config.creo_root не меняется.
@pytest.mark.asyncio
async def test_upload_appends_to_existing_batch(
    pg_engine, fake_redis_client, tmp_path, monkeypatch
):
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.post(
            "/api/tools/campaigns/upload",
            files=[("files", ("a.jpg", _JPEG, "image/jpeg"))],
        )
        upload_id = first.json()["upload_id"]
        second = await ac.post(
            "/api/tools/campaigns/upload",
            data={"upload_id": upload_id},
            files=[("files", ("b.mp4", _MP4, "video/mp4"))],
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["upload_id"] == upload_id
    assert second.json()["total_bytes"] == len(_JPEG) + len(_MP4)
    assert second.json()["added_refs"] == ["b.mp4"]
    assert {concept["ref"] for concept in second.json()["concepts"]} == {"a.jpg", "b.mp4"}
    assert (tmp_path / upload_id / "a.jpg").read_bytes() == _JPEG
    assert (tmp_path / upload_id / "b.mp4").read_bytes() == _MP4


# Переименованный файл (PNG с расширением .mp4) → 422 по magic-сниффу, temp-папка снесена.
@pytest.mark.asyncio
async def test_upload_rejects_renamed_file_magic_mismatch(
    pg_engine, fake_redis_client, tmp_path, monkeypatch
):
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    files = [("files", ("renamed.mp4", _PNG, "video/mp4"))]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/upload", files=files)
    assert resp.status_code == 422
    assert "содержимое" in resp.json()["message"]
    # Никаких осиротевших temp-папок после отказа (cleanup на ошибке).
    assert list(tmp_path.iterdir()) == []


# ─────────────────────────────── runs / cancel ───────────────────────────────


# runs возвращает список с offer_code из снимка config + X-Total-Count.
@pytest.mark.asyncio
async def test_list_runs(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        resp = await ac.get("/api/tools/campaigns/runs")
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "1"
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["offer_code"] == "GH_CR"
    assert rows[0]["status"] == "queued"
    assert "error" not in rows[0]


# get_run отдаёт детали со снимком config; 404 для несуществующего.
@pytest.mark.asyncio
async def test_get_run_detail(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        resp = await ac.get(f"/api/tools/campaigns/runs/{launched['run_id']}")
        assert resp.status_code == 200
        detail = resp.json()
        assert detail["config"]["offer_code"] == "GH_CR"
        assert detail["progress"] == {
            "stage": "queued",
            "completed": None,
            "total": None,
        }
        assert detail["created_meta_ids"] == {}
        assert detail["failure_class"] is None
        assert "error" not in detail
        assert "result" not in detail["task"]
        assert "correlation_id" not in detail["task"]

        missing = await ac.get(f"/api/tools/campaigns/runs/{uuid.uuid4()}")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_run_detail_projects_real_worker_progress_counts(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE campaign_run
                    SET status = 'creating',
                        progress = CAST(:progress AS jsonb)
                    WHERE id = :run_id
                    """
                ),
                {
                    "run_id": uuid.UUID(launched["run_id"]),
                    "progress": json.dumps(
                        {
                            "stage": "creating",
                            "campaigns_done": 1,
                            "adsets_done": 2,
                            "uploads_done": 4,
                            "creatives_done": 3,
                            "ads_done": 2,
                            "total_ads": 6,
                        }
                    ),
                },
            )

        detail = (await ac.get(f"/api/tools/campaigns/runs/{launched['run_id']}")).json()

    assert detail["progress"] == {"stage": "creating", "completed": 2, "total": 6}


@pytest.mark.asyncio
async def test_run_detail_projects_raw_failure_to_bounded_operator_evidence(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    secret = "Traceback token=top-secret 8b8d0c93-15dc-46b4-8fe0-8da6bec3667f"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE campaign_run
                    SET status = 'failed',
                        error = :secret,
                        progress = CAST(:progress AS jsonb)
                    WHERE id = :run_id
                    """
                ),
                {
                    "run_id": uuid.UUID(launched["run_id"]),
                    "secret": secret,
                    "progress": json.dumps(
                        {
                            "stage": "failed",
                            "ads_done": 2,
                            "total_ads": 3,
                            "reason": "partial_or_ack_lost",
                            "internal_trace": secret,
                        }
                    ),
                },
            )
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'failed',
                        result = CAST(:result AS jsonb),
                        last_error = :secret
                    WHERE id = :task_id
                    """
                ),
                {
                    "task_id": launched["task_id"],
                    "secret": secret,
                    "result": json.dumps(
                        {
                            "outcome": "UNKNOWN",
                            "reconcile_required": True,
                            "reason": "partial_or_ack_lost",
                            "exception": secret,
                        }
                    ),
                },
            )

        detail = (await ac.get(f"/api/tools/campaigns/runs/{launched['run_id']}")).json()

    assert detail["failure_class"] == "manual_review"
    assert detail["progress"] == {"stage": "failed", "completed": 2, "total": 3}
    serialized = json.dumps(detail)
    assert secret not in serialized
    assert "partial_or_ack_lost" not in serialized
    assert "exception" not in serialized


@pytest.mark.asyncio
async def test_run_detail_exposes_task_lifecycle_and_safe_controls(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        detail = (await ac.get(f"/api/tools/campaigns/runs/{launched['run_id']}")).json()

    assert detail["task"]["id"] == launched["task_id"]
    assert detail["task"]["state"] == "queued"
    assert detail["task"]["queue_status"] == "pending"
    assert detail["task"]["outcome"] is None
    assert detail["task"]["external_started"] is False
    assert detail["controls"]["abort"] == {
        "available": True,
        "reason": "abort_available",
    }
    assert detail["controls"]["resume"] == {
        "available": False,
        "reason": "run_not_terminal",
    }


@pytest.mark.asyncio
async def test_abort_queued_is_atomic_confirmed_and_idempotent(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    command_key = f"test:campaign-abort:{uuid.uuid4()}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        first = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/abort",
            headers={"Idempotency-Key": command_key},
        )
        replay = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/abort",
            headers={"Idempotency-Key": command_key},
        )
        detail = await ac.get(f"/api/tools/campaigns/runs/{launched['run_id']}")

    assert first.status_code == 200
    assert first.json()["state"] == "confirmed"
    assert first.json()["run_status"] == "cancelled"
    assert first.json()["created"] is True
    assert replay.status_code == 200
    assert replay.json()["task_id"] == first.json()["task_id"]
    assert replay.json()["created"] is False
    assert detail.json()["task"]["queue_status"] == "cancelled"
    assert detail.json()["task"]["outcome"] == "REJECTED"
    assert detail.json()["controls"]["resume"] == {
        "available": True,
        "reason": "pre_external_checkpoint_available",
    }


@pytest.mark.asyncio
async def test_new_abort_key_never_confirms_an_unproven_cancelled_task(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'cancelled',
                        completed_at = NOW(),
                        result = '{}'::jsonb
                    WHERE id = :task_id
                    """
                ),
                {"task_id": launched["task_id"]},
            )
            await conn.execute(
                text("UPDATE campaign_run SET status = 'cancelled' WHERE id = :run_id"),
                {"run_id": uuid.UUID(launched["run_id"])},
            )
        response = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/abort",
            headers={"Idempotency-Key": f"test:unproven-abort:{uuid.uuid4()}"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "run_already_cancelled"
    async with pg_engine.connect() as conn:
        receipts = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM command_idempotency_receipts "
                    "WHERE target_id = :run_id "
                    "AND action_kind = 'abort_campaign_run'"
                ),
                {"run_id": launched["run_id"]},
            )
        ).scalar()
    assert receipts == 0


@pytest.mark.asyncio
async def test_abort_running_sets_cooperative_request_without_false_success(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'running',
                        lease_owner = :owner,
                        lease_token = 1,
                        lease_expires_at = NOW() + INTERVAL '30 minutes'
                    WHERE id = :task_id
                    """
                ),
                {"task_id": launched["task_id"], "owner": uuid.uuid4()},
            )
            await conn.execute(
                text("UPDATE campaign_run SET status = 'uniquifying' WHERE id = :run_id"),
                {"run_id": uuid.UUID(launched["run_id"])},
            )
        response = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/abort",
            headers={"Idempotency-Key": f"test:running-abort:{uuid.uuid4()}"},
        )

    assert response.status_code == 202
    assert response.json()["state"] == "running"
    assert response.json()["run_status"] == "uniquifying"
    assert response.json()["reason"] == "cooperative_abort_requested"
    async with pg_engine.connect() as conn:
        task = (
            await conn.execute(
                text(
                    "SELECT status, cancel_requested_at, external_started_at "
                    "FROM task_queue WHERE id = :task_id"
                ),
                {"task_id": launched["task_id"]},
            )
        ).first()
        run_status = (
            await conn.execute(
                text("SELECT status FROM campaign_run WHERE id = :run_id"),
                {"run_id": uuid.UUID(launched["run_id"])},
            )
        ).scalar()
    assert task.status == "running"
    assert task.cancel_requested_at is not None
    assert task.external_started_at is None
    assert run_status == "uniquifying"


@pytest.mark.asyncio
async def test_resume_creates_one_lineage_task_from_pre_external_checkpoint(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    abort_key = f"test:resume-abort:{uuid.uuid4()}"
    resume_key = f"test:resume:{uuid.uuid4()}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        aborted = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/abort",
            headers={"Idempotency-Key": abort_key},
        )
        assert aborted.status_code == 200
        resumed = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/resume",
            headers={"Idempotency-Key": resume_key},
        )
        replay = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/resume",
            headers={"Idempotency-Key": resume_key},
        )

    assert resumed.status_code == 202
    assert resumed.json()["state"] == "queued"
    assert resumed.json()["created"] is True
    assert resumed.json()["task_id"] != launched["task_id"]
    assert replay.status_code == 202
    assert replay.json()["task_id"] == resumed.json()["task_id"]
    assert replay.json()["created"] is False

    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT id, status, payload
                    FROM task_queue
                    WHERE task_type = 'campaign_create'
                      AND payload->>'run_id' = :run_id
                    ORDER BY id
                    """
                ),
                {"run_id": launched["run_id"]},
            )
        ).fetchall()
        run = (
            await conn.execute(
                text("SELECT status, progress, error FROM campaign_run WHERE id = :run_id"),
                {"run_id": uuid.UUID(launched["run_id"])},
            )
        ).first()
    assert len(rows) == 2
    assert rows[0].status == "cancelled"
    assert rows[1].status == "pending"
    assert rows[1].payload["resume_of_task_id"] == launched["task_id"]
    assert rows[1].payload["resume_generation"] == 1
    assert run.status == "queued"
    assert run.progress["checkpoint"] == "pre_external"
    assert run.error is None


@pytest.mark.asyncio
async def test_resume_rejects_unknown_post_boundary_checkpoint(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET status = 'failed',
                        external_started_at = NOW(),
                        completed_at = NOW(),
                        result = CAST(:result AS JSONB)
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
        response = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/resume",
            headers={"Idempotency-Key": f"test:unsafe-resume:{uuid.uuid4()}"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "external_boundary_crossed"
    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM task_queue "
                    "WHERE task_type = 'campaign_create' "
                    "AND payload->>'run_id' = :run_id"
                ),
                {"run_id": launched["run_id"]},
            )
        ).scalar()
    assert count == 1


@pytest.mark.asyncio
async def test_two_distinct_resume_commands_have_one_cas_winner(
    pg_engine,
    fake_redis_client,
    clean_campaigns,
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        aborted = await ac.post(
            f"/api/tools/campaigns/runs/{launched['run_id']}/abort",
            headers={"Idempotency-Key": f"test:cas-abort:{uuid.uuid4()}"},
        )
        assert aborted.status_code == 200

        async def resume_once() -> object:
            return await ac.post(
                f"/api/tools/campaigns/runs/{launched['run_id']}/resume",
                headers={"Idempotency-Key": f"test:cas-resume:{uuid.uuid4()}"},
            )

        first, second = await asyncio.gather(resume_once(), resume_once())

    assert sorted([first.status_code, second.status_code]) == [202, 409]
    conflict = first if first.status_code == 409 else second
    assert conflict.json()["code"] == "run_not_terminal"
    async with pg_engine.connect() as conn:
        task_count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM task_queue "
                    "WHERE task_type = 'campaign_create' "
                    "AND payload->>'run_id' = :run_id"
                ),
                {"run_id": launched["run_id"]},
            )
        ).scalar()
        resume_receipts = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM command_idempotency_receipts "
                    "WHERE action_kind = 'resume_campaign_run' "
                    "AND target_id = :run_id"
                ),
                {"run_id": launched["run_id"]},
            )
        ).scalar()
    assert task_count == 2
    assert resume_receipts == 1


# Два launch одного оффера с разными датами → code_start второго продолжает первый (нет коллизии CRxxx).
@pytest.mark.asyncio
async def test_launch_allocates_continuing_code_start(
    pg_engine, fake_redis_client, clean_campaigns, tmp_path, monkeypatch
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    upload_dir = tmp_path / "seq-upload"
    upload_dir.mkdir()
    (upload_dir / "a.jpg").write_bytes(b"a")
    (upload_dir / "b.jpg").write_bytes(b"b")
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    # Конфиг с concept_refs чтобы span > 0 (без концептов block_code_span = 0).
    def _cfg_with_refs(start_date: str) -> dict:
        cfg = _valid_config()
        cfg["start_date"] = start_date
        cfg["creo_root"] = "seq-upload"
        cfg["campaigns"][0]["concept_refs"] = ["a.jpg", "b.jpg"]
        return cfg

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post(
            "/api/tools/campaigns/launch", json={"config": _cfg_with_refs("2099-01-01")}
        )
        r2 = await ac.post(
            "/api/tools/campaigns/launch", json={"config": _cfg_with_refs("2099-01-02")}
        )

    assert r1.status_code == 201
    assert r2.status_code == 201
    run_id1 = r1.json()["run_id"]
    run_id2 = r2.json()["run_id"]
    assert run_id1 != run_id2  # разные конфиги → разные run'ы

    # Читаем code_start обоих run'ов из campaign_run.config.
    async with pg_engine.connect() as conn:
        row1 = (
            await conn.execute(
                text(
                    "SELECT (config->>'code_start')::int AS code_start FROM campaign_run WHERE id = :rid"
                ),
                {"rid": uuid.UUID(run_id1)},
            )
        ).first()
        row2 = (
            await conn.execute(
                text(
                    "SELECT (config->>'code_start')::int AS code_start FROM campaign_run WHERE id = :rid"
                ),
                {"rid": uuid.UUID(run_id2)},
            )
        ).first()

    base1 = row1.code_start
    base2 = row2.code_start
    # span = число КОНЦЕПТОВ (код общий по adset'ам): 2 концепта → 2 кода на запуск.
    span1 = 2
    assert base2 == base1 + span1
