# -*- coding: utf-8 -*-
"""Интеграционные тесты CRUD /dashboard/auto-enable-disabled.

Используем реальный Postgres из docker-compose и fakeredis.
Каждый тест изолирован через fixture с cleanup по CASCADE от offers.
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
    """Собирает FastAPI приложение с переопределёнными зависимостями."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def ae_ad_fixture(pg_engine):
    """Создаёт offer→campaign→adset→2 объявления для тестов auto-enable.

    Cleanup через CASCADE от offers.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id_1 = uuid.uuid4()
    ad_id_2 = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    fb_ad_id_1 = f"55000{suffix}01"
    fb_ad_id_2 = f"55000{suffix}02"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"AE_{suffix}", "n": f"AE offer {suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_AE_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADS_AE_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id_1, "a": adset_id, "f": fb_ad_id_1, "n": f"AD_AE1_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id_2, "a": adset_id, "f": fb_ad_id_2, "n": f"AD_AE2_{suffix}"},
        )

    yield {
        "offer_id": offer_id,
        "ad_id_1": ad_id_1,
        "ad_id_2": ad_id_2,
        "fb_ad_id_1": fb_ad_id_1,
        "fb_ad_id_2": fb_ad_id_2,
        "ad_name_1": f"AD_AE1_{suffix}",
        "ad_name_2": f"AD_AE2_{suffix}",
        "suffix": suffix,
    }

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


# Без записей GET возвращает пустой список, не ошибку.
@pytest.mark.asyncio
async def test_auto_enable_list_empty(pg_engine, fake_redis_client, ae_ad_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/auto-enable-disabled")

    assert resp.status_code == 200
    data = resp.json()
    our_ids = {ae_ad_fixture["fb_ad_id_1"], ae_ad_fixture["fb_ad_id_2"]}
    our_records = [r for r in data if r["fb_ad_id"] in our_ids]
    assert our_records == []


# GET с записями возвращает их с JOIN ad_name.
@pytest.mark.asyncio
async def test_auto_enable_list_with_records(pg_engine, fake_redis_client, ae_ad_fixture):
    ad_id_1 = ae_ad_fixture["ad_id_1"]

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ad_auto_enable_disabled (ad_id, cabinet_day_started_at, reason) "
                "VALUES (:a, NOW(), :r)"
            ),
            {"a": ad_id_1, "r": "test reason"},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/auto-enable-disabled")

    assert resp.status_code == 200
    data = resp.json()
    our_ids = {ae_ad_fixture["fb_ad_id_1"]}
    our_records = [r for r in data if r["fb_ad_id"] in our_ids]
    assert len(our_records) == 1
    record = our_records[0]
    # JOIN возвращает ad_name
    assert record["ad_name"] == ae_ad_fixture["ad_name_1"]
    assert record["reason"] == "test reason"
    assert "disabled_at" in record


# POST happy-path: 201, запись создаётся.
@pytest.mark.asyncio
async def test_auto_enable_post_happy(pg_engine, fake_redis_client, ae_ad_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = ae_ad_fixture["fb_ad_id_1"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            f"/api/dashboard/auto-enable-disabled/{fb_ad_id}",
            json={"reason": "manually disabled by user"},
        )

    assert resp.status_code == 201
    data = resp.json()
    assert data["fb_ad_id"] == fb_ad_id
    assert data["reason"] == "manually disabled by user"
    assert data["disabled_at"] is not None

    # Проверяем в БД
    async with pg_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT id FROM ad_auto_enable_disabled WHERE ad_id = :a"),
            {"a": ae_ad_fixture["ad_id_1"]},
        )
        assert row.one_or_none() is not None


# POST для несуществующего fb_ad_id → 404.
@pytest.mark.asyncio
async def test_auto_enable_post_unknown_ad_404(pg_engine, fake_redis_client):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/dashboard/auto-enable-disabled/nonexistent_ae_ad",
            json={"reason": "no such ad"},
        )

    assert resp.status_code == 404


# Повторный POST для уже-disabled объявления → 409 Conflict.
@pytest.mark.asyncio
async def test_auto_enable_post_duplicate_409(pg_engine, fake_redis_client, ae_ad_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = ae_ad_fixture["fb_ad_id_1"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp1 = await ac.post(
            f"/api/dashboard/auto-enable-disabled/{fb_ad_id}",
            json={"reason": "первый раз"},
        )
        assert resp1.status_code == 201

        resp2 = await ac.post(
            f"/api/dashboard/auto-enable-disabled/{fb_ad_id}",
            json={"reason": "повторный"},
        )

    assert resp2.status_code == 409


# DELETE happy-path: запись удаляется, 204.
@pytest.mark.asyncio
async def test_auto_enable_delete_happy(pg_engine, fake_redis_client, ae_ad_fixture):
    # Создаём флаг напрямую
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ad_auto_enable_disabled (ad_id, cabinet_day_started_at) "
                "VALUES (:a, NOW())"
            ),
            {"a": ae_ad_fixture["ad_id_1"]},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = ae_ad_fixture["fb_ad_id_1"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/dashboard/auto-enable-disabled/{fb_ad_id}")

    assert resp.status_code == 204

    # Проверяем что запись удалена
    async with pg_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT id FROM ad_auto_enable_disabled WHERE ad_id = :a"),
            {"a": ae_ad_fixture["ad_id_1"]},
        )
        assert row.one_or_none() is None


# DELETE для не-disabled объявления → 404.
@pytest.mark.asyncio
async def test_auto_enable_delete_not_disabled_404(pg_engine, fake_redis_client, ae_ad_fixture):
    # Флаг не установлен — сразу DELETE
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = ae_ad_fixture["fb_ad_id_2"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/dashboard/auto-enable-disabled/{fb_ad_id}")

    assert resp.status_code == 404


# Идемпотентность: POST→DELETE→POST снова создаёт запись (201).
@pytest.mark.asyncio
async def test_auto_enable_idempotent_post_delete_post(pg_engine, fake_redis_client, ae_ad_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = ae_ad_fixture["fb_ad_id_1"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r1 = await ac.post(f"/api/dashboard/auto-enable-disabled/{fb_ad_id}", json={})
        assert r1.status_code == 201

        r2 = await ac.delete(f"/api/dashboard/auto-enable-disabled/{fb_ad_id}")
        assert r2.status_code == 204

        # После удаления снова можно добавить
        r3 = await ac.post(
            f"/api/dashboard/auto-enable-disabled/{fb_ad_id}",
            json={"reason": "повторное отключение"},
        )
        assert r3.status_code == 201
        assert r3.json()["reason"] == "повторное отключение"
