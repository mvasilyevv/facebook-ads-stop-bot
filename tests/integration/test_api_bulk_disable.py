# -*- coding: utf-8 -*-
"""Money-матрица для POST /api/dashboard/disable-tasks/bulk (массовое отключение).

Усиленное покрытие: idempotency (двойной submit), cap → 422, partial-failure
(валидные/несуществующие/дубли), concurrent double-submit (race → exactly-once),
провенанс requested_by в каждой задаче.

Требует реальный Postgres из docker-compose. Cleanup id-scoped по префиксу
99BULK (fb_ad_id) и idempotency_key LIKE '%BULKTOK%' — стабильно при параллельном
прогоне (урок Round 11: prefix-scoped, не глобальный DELETE).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app


def _make_app(engine, redis=None):
    """FastAPI с подменённым engine (и опционально redis) для теста."""
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_bulk(pg_engine):
    """Чистит задачи и тестовые объявления bulk-тестов до и после."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM task_queue WHERE idempotency_key LIKE '%BULKTOK%' "
                    "OR payload->>'target_id' LIKE '99BULK%'"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE fb_ad_id LIKE '99BULK%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'BULK_%'"))
            await conn.execute(text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'BULK_%'"))
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'BULK_%'"))

    await _cleanup()
    yield
    await _cleanup()


async def _seed_ad(conn, suffix: str) -> str:
    """offer→campaign→adset→ad. Возвращает fb_ad_id вида 99BULK<suffix>."""
    offer_id, campaign_id, adset_id, ad_id = (uuid.uuid4() for _ in range(4))
    fb_ad_id = f"99BULK{suffix}"
    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"BULK_{suffix}", "n": f"Bulk offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"BULK_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"BULK_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"Bulk AD {suffix}"},
    )
    return fb_ad_id


async def _count_pause_tasks(engine, fb_ad_id: str) -> int:
    """Сколько pause_ad-задач в task_queue для данного target_id."""
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM task_queue "
                    "WHERE task_type='meta_api_mutation' "
                    "AND payload->>'mutation_kind'='pause_ad' "
                    "AND payload->>'target_id' = :fid"
                ),
                {"fid": fb_ad_id},
            )
        ).scalar() or 0


# ─── Тест 1: happy path — несколько ad создают задачи ──────────────────────────
# Все валидные ad → created, реальные pause_ad-задачи в БД с правильным target_id.
@pytest.mark.asyncio
async def test_bulk_disable_happy(pg_engine, fake_redis_client, clean_bulk) -> None:
    """POST bulk с 3 валидными ad → 200, created=3, в БД 3 задачи pause_ad."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        ids = [await _seed_ad(conn, f"{sfx}{i}") for i in range(3)]

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/disable-tasks/bulk",
            json={
                "fb_ad_ids": ids,
                "reason": "test bulk",
                "idempotency_token": f"BULKTOK{sfx}",
                "requested_by": "tester",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["created"]) == 3
    assert data["skipped"] == []
    assert data["failed"] == []
    created_ids = {item["fb_ad_id"] for item in data["created"]}
    assert created_ids == set(ids)
    for fid in ids:
        assert await _count_pause_tasks(pg_engine, fid) == 1


# ─── Тест 2: idempotency — двойной submit того же token не дублирует ───────────
# Второй идентичный запрос → всё в skipped, в БД по-прежнему 1 задача на ad.
@pytest.mark.asyncio
async def test_bulk_disable_idempotent_double_submit(
    pg_engine, fake_redis_client, clean_bulk
) -> None:
    """Повтор bulk с тем же idempotency_token → НОЛЬ дублей (всё в skipped)."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        ids = [await _seed_ad(conn, f"{sfx}{i}") for i in range(2)]

    token = f"BULKTOK{sfx}"
    payload = {"fb_ad_ids": ids, "idempotency_token": token, "requested_by": "tester"}
    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        first = await ac.post("/api/dashboard/disable-tasks/bulk", json=payload)
        second = await ac.post("/api/dashboard/disable-tasks/bulk", json=payload)

    assert first.status_code == 200
    assert len(first.json()["created"]) == 2
    # Второй вызов: ни одной новой задачи — всё дубли.
    sec = second.json()
    assert second.status_code == 200
    assert sec["created"] == []
    assert len(sec["skipped"]) == 2
    assert all(s["reason"] == "duplicate" for s in sec["skipped"])
    # skipped ссылается на id уже существующей задачи.
    assert all(s["task_id"] is not None for s in sec["skipped"])
    # Exactly-once: в БД ровно 1 задача на каждый ad.
    for fid in ids:
        assert await _count_pause_tasks(pg_engine, fid) == 1


# ─── Тест 3: cap — превышение лимита batch → 422 ──────────────────────────────
# 51 id (> BULK_DISABLE_MAX_IDS=50) → 422, ни одной задачи не создано.
@pytest.mark.asyncio
async def test_bulk_disable_cap_exceeded(pg_engine, fake_redis_client, clean_bulk) -> None:
    """Batch >50 ad → 422 (валидация входа)."""
    ids = [f"99BULK{uuid.uuid4().hex[:8]}" for _ in range(51)]
    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/disable-tasks/bulk",
            json={"fb_ad_ids": ids, "idempotency_token": "BULKTOKcap"},
        )
    assert resp.status_code == 422


# ─── Тест 4: пустой список → 422 (min_length=1) ───────────────────────────────
# Пустой fb_ad_ids отсекается pydantic-валидацией до обработки.
@pytest.mark.asyncio
async def test_bulk_disable_empty_list(pg_engine, fake_redis_client, clean_bulk) -> None:
    """Пустой fb_ad_ids → 422 (min_length=1)."""
    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/disable-tasks/bulk",
            json={"fb_ad_ids": [], "idempotency_token": "BULKTOKempty"},
        )
    assert resp.status_code == 422


# ─── Тест 5: partial-failure — смесь валидных/несуществующих/дублей ───────────
# Валидный новый → created; несуществующий → failed; уже-созданный → skipped.
@pytest.mark.asyncio
async def test_bulk_disable_partial_failure(pg_engine, fake_redis_client, clean_bulk) -> None:
    """Смесь: 1 новый валидный + 1 несуществующий + 1 предсозданный дубль."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        valid_new = await _seed_ad(conn, f"{sfx}N")
        valid_dup = await _seed_ad(conn, f"{sfx}D")
    missing = f"99BULK{sfx}MISSING"  # нет в fb_ads
    token = f"BULKTOK{sfx}"

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Предсоздаём задачу для valid_dup тем же token → попадёт в skipped.
        pre = await ac.post(
            "/api/dashboard/disable-tasks/bulk",
            json={"fb_ad_ids": [valid_dup], "idempotency_token": token},
        )
        assert pre.status_code == 200 and len(pre.json()["created"]) == 1

        resp = await ac.post(
            "/api/dashboard/disable-tasks/bulk",
            json={"fb_ad_ids": [valid_new, missing, valid_dup], "idempotency_token": token},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert {i["fb_ad_id"] for i in data["created"]} == {valid_new}
    assert {f["fb_ad_id"] for f in data["failed"]} == {missing}
    assert data["failed"][0]["reason"] == "not_found_in_fb_ads"
    assert {s["fb_ad_id"] for s in data["skipped"]} == {valid_dup}
    # valid_dup не задвоился.
    assert await _count_pause_tasks(pg_engine, valid_dup) == 1


# ─── Тест 6 (КЛЮЧЕВОЙ money): concurrent double-submit → exactly-once per ad ───
# Два параллельных идентичных запроса (asyncio.gather, две сессии) конкурируют
# на UNIQUE idempotency_key. В сумме на каждый ad ровно 1 задача; суммарно по
# обоим ответам created=1 и skipped=1 на ad (UNIQUE отдаёт победителя одному).
@pytest.mark.asyncio
async def test_bulk_disable_concurrent_race_exactly_once(
    pg_engine, fake_redis_client, clean_bulk
) -> None:
    """Race: 2 одновременных одинаковых bulk → exactly-once per ad (money-критично)."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        ids = [await _seed_ad(conn, f"{sfx}{i}") for i in range(4)]

    token = f"BULKTOK{sfx}"
    payload = {"fb_ad_ids": ids, "idempotency_token": token, "requested_by": "racer"}

    # Две независимые сессии на одном app — реальная конкуренция на БД-уровне.
    app = _make_app(pg_engine, fake_redis_client)

    async def _submit():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post("/api/dashboard/disable-tasks/bulk", json=payload)
            return r.json()

    res_a, res_b = await asyncio.gather(_submit(), _submit())

    # Главный инвариант: ровно 1 pause_ad-задача на каждый ad, дублей нет.
    for fid in ids:
        assert await _count_pause_tasks(pg_engine, fid) == 1, f"дубль задачи для {fid}!"

    # Суммарно по обоим ответам: каждый ad ровно один раз в created (победитель)
    # и при этом проигравший видит его в skipped. created по всем = 4 уникальных.
    created_total = {i["fb_ad_id"] for i in res_a["created"]} | {
        i["fb_ad_id"] for i in res_b["created"]
    }
    assert created_total == set(ids)
    # Для каждого ad: суммарно (created + skipped) по обоим ответам учитывает оба
    # запроса — один создал, другой пропустил (либо оба увидели свою долю).
    for fid in ids:
        seen_created = sum(
            1 for res in (res_a, res_b) for i in res["created"] if i["fb_ad_id"] == fid
        )
        seen_skipped = sum(
            1 for res in (res_a, res_b) for s in res["skipped"] if s["fb_ad_id"] == fid
        )
        # Ровно один из двух запросов создал задачу; второй обязан был её пропустить.
        assert seen_created == 1, f"{fid}: created в {seen_created} ответах (ждали 1)"
        assert seen_skipped == 1, f"{fid}: skipped в {seen_skipped} ответах (ждали 1)"


# ─── Тест 7: провенанс — requested_by записан в каждую задачу ──────────────────
# created-задачи должны нести инициатора и в ответе, и в БД (task_queue.requested_by).
@pytest.mark.asyncio
async def test_bulk_disable_provenance(pg_engine, fake_redis_client, clean_bulk) -> None:
    """requested_by проставлен в каждую созданную задачу (провенанс)."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        ids = [await _seed_ad(conn, f"{sfx}{i}") for i in range(2)]

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/disable-tasks/bulk",
            json={
                "fb_ad_ids": ids,
                "idempotency_token": f"BULKTOK{sfx}",
                "requested_by": "operator_mark",
                "requested_by_chat_id": 777001,
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert all(item["requested_by"] == "operator_mark" for item in data["created"])
    assert all(item["requested_by_chat_id"] == 777001 for item in data["created"])
    # Сверяем с БД.
    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT requested_by, created_by_chat_id FROM task_queue "
                    "WHERE payload->>'target_id' = ANY(CAST(:ids AS text[]))"
                ),
                {"ids": ids},
            )
        ).all()
    assert len(rows) == 2
    assert all(r.requested_by == "operator_mark" for r in rows)
    assert all(r.created_by_chat_id == 777001 for r in rows)


# ─── Тест 8: дубли внутри одного запроса схлопываются ─────────────────────────
# Один и тот же ad дважды в одном body → 1 created, второй экземпляр не создаёт
# дубль (внутренний dedup до обращения к БД).
@pytest.mark.asyncio
async def test_bulk_disable_intra_request_dedup(pg_engine, fake_redis_client, clean_bulk) -> None:
    """Дубль fb_ad_id в одном body → ровно 1 задача (внутренний dedup)."""
    sfx = uuid.uuid4().hex[:6]
    async with pg_engine.begin() as conn:
        fid = await _seed_ad(conn, sfx)

    app = _make_app(pg_engine, fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/disable-tasks/bulk",
            json={"fb_ad_ids": [fid, fid, fid], "idempotency_token": f"BULKTOK{sfx}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["created"]) == 1
    assert await _count_pause_tasks(pg_engine, fid) == 1
