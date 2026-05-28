# -*- coding: utf-8 -*-
"""Интеграционные тесты CRUD /fake-deposits.

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
async def fd_ad_fixture(pg_engine):
    """Создаёт offer→campaign→adset→2 объявления для тестов fake deposits.

    Cleanup через CASCADE от offers.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id_1 = uuid.uuid4()
    ad_id_2 = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    fb_ad_id_1 = f"71000{suffix}01"
    fb_ad_id_2 = f"71000{suffix}02"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"FD_{suffix}", "n": f"FD offer {suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_FD_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADS_FD_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id_1, "a": adset_id, "f": fb_ad_id_1, "n": f"AD_FD1_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id_2, "a": adset_id, "f": fb_ad_id_2, "n": f"AD_FD2_{suffix}"},
        )

    yield {
        "offer_id": offer_id,
        "ad_id_1": ad_id_1,
        "ad_id_2": ad_id_2,
        "fb_ad_id_1": fb_ad_id_1,
        "fb_ad_id_2": fb_ad_id_2,
        "ad_name_1": f"AD_FD1_{suffix}",
        "ad_name_2": f"AD_FD2_{suffix}",
        "suffix": suffix,
    }

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


# Без записей GET возвращает пустой список, не ошибку.
@pytest.mark.asyncio
async def test_fake_deposits_empty(pg_engine, fake_redis_client, fd_ad_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/fake-deposits")

    assert resp.status_code == 200
    # Фильтруем только наши тестовые объявления — другие тесты могут оставить записи
    data = resp.json()
    our_ids = {fd_ad_fixture["fb_ad_id_1"], fd_ad_fixture["fb_ad_id_2"]}
    our_records = [r for r in data if r["fb_ad_id"] in our_ids]
    assert our_records == []


# GET с 2 записями возвращает их с корректным JOIN ad_name.
@pytest.mark.asyncio
async def test_fake_deposits_list_with_records(pg_engine, fake_redis_client, fd_ad_fixture):
    ad_id_1 = fd_ad_fixture["ad_id_1"]
    ad_id_2 = fd_ad_fixture["ad_id_2"]

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO ad_deposit_corrections (ad_id, corrected_deposits, note) "
                "VALUES (:a, :d, :n)"
            ),
            {"a": ad_id_1, "d": 3, "n": "тест 1"},
        )
        await conn.execute(
            text(
                "INSERT INTO ad_deposit_corrections (ad_id, corrected_deposits, note) "
                "VALUES (:a, :d, :n)"
            ),
            {"a": ad_id_2, "d": 7, "n": "тест 2"},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/fake-deposits")

    assert resp.status_code == 200
    data = resp.json()
    our_ids = {fd_ad_fixture["fb_ad_id_1"], fd_ad_fixture["fb_ad_id_2"]}
    our_records = {r["fb_ad_id"]: r for r in data if r["fb_ad_id"] in our_ids}
    assert len(our_records) == 2
    # Проверяем JOIN ad_name
    assert our_records[fd_ad_fixture["fb_ad_id_1"]]["ad_name"] == fd_ad_fixture["ad_name_1"]
    assert our_records[fd_ad_fixture["fb_ad_id_2"]]["fake_count"] == 7


# PUT happy-path: создаёт запись, возвращает 200 с корректными данными.
@pytest.mark.asyncio
async def test_fake_deposits_put_happy(pg_engine, fake_redis_client, fd_ad_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = fd_ad_fixture["fb_ad_id_1"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/fake-deposits/{fb_ad_id}",
            json={"fake_count": 5, "note": "фейк депозиты"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["fb_ad_id"] == fb_ad_id
    assert data["fake_count"] == 5
    assert data["note"] == "фейк депозиты"
    assert data["internal_id"] is not None

    # Проверяем в БД
    async with pg_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT corrected_deposits FROM ad_deposit_corrections WHERE ad_id = :a"),
            {"a": fd_ad_fixture["ad_id_1"]},
        )
        assert row.scalar_one() == 5


# Повторный PUT обновляет запись (upsert).
@pytest.mark.asyncio
async def test_fake_deposits_put_update(pg_engine, fake_redis_client, fd_ad_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = fd_ad_fixture["fb_ad_id_1"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.put(f"/api/fake-deposits/{fb_ad_id}", json={"fake_count": 3, "note": "старое"})
        resp = await ac.put(
            f"/api/fake-deposits/{fb_ad_id}", json={"fake_count": 9, "note": "новое"}
        )

    assert resp.status_code == 200
    data = resp.json()
    # Обновлённое значение
    assert data["fake_count"] == 9
    assert data["note"] == "новое"


# fake_count=-1 не проходит Pydantic-валидацию → 422.
@pytest.mark.asyncio
async def test_fake_deposits_put_negative_count_422(pg_engine, fake_redis_client, fd_ad_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = fd_ad_fixture["fb_ad_id_1"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/fake-deposits/{fb_ad_id}", json={"fake_count": -1, "note": "невалид"}
        )

    assert resp.status_code == 422


# PUT для несуществующего fb_ad_id → 404.
@pytest.mark.asyncio
async def test_fake_deposits_put_unknown_ad_404(pg_engine, fake_redis_client):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            "/api/fake-deposits/nonexistent_fb_ad",
            json={"fake_count": 5, "note": "нет такого"},
        )

    assert resp.status_code == 404


# DELETE happy-path: запись удаляется, 204.
@pytest.mark.asyncio
async def test_fake_deposits_delete_happy(pg_engine, fake_redis_client, fd_ad_fixture):
    # Создаём запись
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO ad_deposit_corrections (ad_id, corrected_deposits) VALUES (:a, :d)"),
            {"a": fd_ad_fixture["ad_id_1"], "d": 2},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = fd_ad_fixture["fb_ad_id_1"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/fake-deposits/{fb_ad_id}")

    assert resp.status_code == 204

    # Проверяем что запись удалена
    async with pg_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT id FROM ad_deposit_corrections WHERE ad_id = :a"),
            {"a": fd_ad_fixture["ad_id_1"]},
        )
        assert row.one_or_none() is None


# DELETE для несуществующей корректировки → 404.
@pytest.mark.asyncio
async def test_fake_deposits_delete_not_found_404(pg_engine, fake_redis_client, fd_ad_fixture):
    # Записи нет — сразу удаляем
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fb_ad_id = fd_ad_fixture["fb_ad_id_2"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/fake-deposits/{fb_ad_id}")

    assert resp.status_code == 404
