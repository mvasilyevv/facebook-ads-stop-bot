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
async def clean_campaigns(pg_engine):
    """Чистит campaign_run/campaign_preset/task_queue до и после теста.

    Также сбрасывает реестр креативов (campaign_creative) и счётчики
    нумерации (offer_creative_seq) для полной изоляции тестов.
    """

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))
            await conn.execute(text("DELETE FROM campaign_creative"))
            await conn.execute(text("DELETE FROM offer_creative_seq"))
            await conn.execute(text("DELETE FROM campaign_run"))
            await conn.execute(text("DELETE FROM campaign_preset"))

    await _truncate()
    yield
    await _truncate()


def _valid_config() -> dict:
    """Минимально-валидный CampaignConfig (2 adset → раскладка K×2)."""
    return {
        "account": {"act_id": "123", "page_id": "100", "pixel_id": "200"},
        "offer_code": "GH_CR",
        "destination_link": "https://example.com",
        "start_date": "2026-07-01",
        "targeting": {"countries": ["DE"]},
        "budget": {
            "level": "campaign",
            "daily_cents": 5000,
            "bid_strategy": "COST_CAP",
            "bid_amount_cents": 150,
        },
        "campaigns": [
            {
                "key": "static",
                "name": "{byer} | {offer} | static | adset.pro | {date}",
                "adsets": [
                    {"name": "as1", "dir": "as1", "glob": "*.jpg"},
                    {"name": "as2", "dir": "as2", "glob": "*.jpg"},
                ],
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
    # Без концептов в конфиге превью использует фолбэк (1 концепт/блок):
    # adset i = 1 ad → 2 adset × 1 = 2 ads (parity с исполнителем, не баговые 4).
    assert plan["ad_count"] == 2
    assert plan["launch_state"] == "campaign_paused"
    assert "GH_CR" in plan["campaigns"][0]["name"]

    # validate не создал ни одного run.
    async with pg_engine.connect() as conn:
        cnt = (await conn.execute(text("SELECT COUNT(*) FROM campaign_run"))).scalar()
    assert cnt == 0


# Невалидный конфиг (adset_count < 1) → 422 ещё на pydantic.
@pytest.mark.asyncio
async def test_validate_invalid_config_422(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    cfg = _valid_config()
    cfg["campaigns"][0]["adset_count"] = 0  # нарушает Field(ge=1)
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
                text("SELECT status, idempotency_key FROM campaign_run WHERE id = :rid"),
                {"rid": uuid.UUID(run_id)},
            )
        ).first()
        task = (
            await conn.execute(
                text(
                    "SELECT task_type, status, payload->>'run_id' AS run_id, idempotency_key "
                    "FROM task_queue WHERE id = :tid"
                ),
                {"tid": out["task_id"]},
            )
        ).first()
    assert run.status == "queued"
    assert task.task_type == "campaign_create"
    assert task.status == "pending"
    assert task.run_id == run_id
    # Money-safety: один idempotency_key у run и задачи.
    assert task.idempotency_key == run.idempotency_key == out["idempotency_key"]


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
    cfg["budget"] = {
        "daily_cents": 100_000_00 + 1,
        "bid_strategy": "COST_CAP",
        "bid_amount_cents": 150,
    }
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
        "daily_budget_cents": 20000,
        "bid_amount_cents": 150,
        "countries": ["DE"],
        "campaigns": [{"key": "video", "adset_count": 2, "concept_refs": []}],
    }
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/tools/campaigns/launch", json={"config": flat})
    assert resp.status_code == 422
    assert "video" in resp.json()["detail"]
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
    assert "содержимое" in resp.json()["detail"]
    # Никаких осиротевших temp-папок после отказа (cleanup на ошибке).
    assert list(tmp_path.iterdir()) == []


# ─────────────────────────── runs / clone / cancel / cleanup ───────────────────────────


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
        assert detail["progress"] == {}
        assert detail["created_meta_ids"] == {}

        missing = await ac.get(f"/api/tools/campaigns/runs/{uuid.uuid4()}")
        assert missing.status_code == 404


# clone создаёт новый queued-черновик без задачи и без idempotency_key.
@pytest.mark.asyncio
async def test_clone_run(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        resp = await ac.post(f"/api/tools/campaigns/runs/{launched['run_id']}/clone")
    assert resp.status_code == 201
    clone = resp.json()
    assert clone["run_id"] != launched["run_id"]
    assert clone["task_id"] is None
    assert clone["status"] == "queued"

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT config->>'offer_code' AS offer_code, idempotency_key "
                    "FROM campaign_run WHERE id = :rid"
                ),
                {"rid": uuid.UUID(clone["run_id"])},
            )
        ).first()
    assert row.offer_code == "GH_CR"
    assert row.idempotency_key is None


# cancel переводит queued-run в cancelled и отменяет задачу.
@pytest.mark.asyncio
async def test_cancel_run_in_queue(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        resp = await ac.post(f"/api/tools/campaigns/runs/{launched['run_id']}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    async with pg_engine.connect() as conn:
        task_status = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :tid"),
                {"tid": launched["task_id"]},
            )
        ).scalar()
    assert task_status == "cancelled"


# cancel запрещён, если run уже creating (необратимое создание Meta) → 409.
@pytest.mark.asyncio
async def test_cancel_run_creating_conflict(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        # Двигаем run в creating (имитация воркера).
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("UPDATE campaign_run SET status = 'creating' WHERE id = :rid"),
                {"rid": uuid.UUID(launched["run_id"])},
            )
        resp = await ac.post(f"/api/tools/campaigns/runs/{launched['run_id']}/cancel")
    assert resp.status_code == 409


# Два launch одного оффера с разными датами → code_start второго продолжает первый (нет коллизии CRxxx).
@pytest.mark.asyncio
async def test_launch_allocates_continuing_code_start(
    pg_engine, fake_redis_client, clean_campaigns
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    # Конфиг с concept_refs чтобы span > 0 (иначе block_code_span = 0×copies = 0).
    def _cfg_with_refs(start_date: str) -> dict:
        cfg = _valid_config()
        cfg["start_date"] = start_date
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
    # span = 2 концепта × 2 adset = 4 кода на запуск.
    span1 = 2 * 2
    assert base2 == base1 + span1


# cleanup возвращает созданные Meta-ID для сноса (или сообщение, что их нет).
@pytest.mark.asyncio
async def test_cleanup_run(pg_engine, fake_redis_client, clean_campaigns):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        launched = (
            await ac.post("/api/tools/campaigns/launch", json={"config": _valid_config()})
        ).json()
        # Заполняем created_meta_ids (имитация partial-fail воркера).
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE campaign_run SET created_meta_ids = CAST(:ids AS JSONB) WHERE id = :rid"
                ),
                {
                    "ids": '{"campaigns": ["c1"], "adsets": ["a1"]}',
                    "rid": uuid.UUID(launched["run_id"]),
                },
            )
        resp = await ac.post(f"/api/tools/campaigns/runs/{launched['run_id']}/cleanup")
    assert resp.status_code == 200
    out = resp.json()
    assert out["meta_ids"]["campaigns"] == ["c1"]
