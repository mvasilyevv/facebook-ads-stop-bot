# -*- coding: utf-8 -*-
"""Интеграционные тесты роутера offers.

Паттерн: app = _make_app(engine=pg_engine, redis=fake_redis)
         AsyncClient + ASGITransport (без живого HTTP-сервера).

Каждый тест изолирован через clean_offers fixture — DELETE FROM offers.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

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
async def clean_offers(pg_engine):
    """Очищает offers (и cascade) до и после каждого теста."""
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offer_rules"))
        await conn.execute(text("DELETE FROM offers"))
    yield
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offer_rules"))
        await conn.execute(text("DELETE FROM offers"))


async def _seed_offers(conn, offers: list[dict]) -> list[uuid.UUID]:
    """Вставляет офферы, возвращает список id."""
    ids = []
    for o in offers:
        result = await conn.execute(
            text(
                "INSERT INTO offers (code, name, vertical, is_active) "
                "VALUES (:code, :name, :vertical, :is_active) RETURNING id"
            ),
            {
                "code": o["code"],
                "name": o.get("name", o["code"] + " name"),
                "vertical": o.get("vertical"),
                "is_active": o.get("is_active", True),
            },
        )
        ids.append(result.scalar_one())
    return ids


# ─────────────────────── GET /offers ───────────────────────


# Пустая БД должна вернуть пустой список, а не ошибку.
@pytest.mark.asyncio
async def test_list_offers_empty_db(pg_engine, fake_redis_client, clean_offers):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/offers")
    assert resp.status_code == 200
    assert resp.json() == []


# По умолчанию inactive-офферы не возвращаются.
@pytest.mark.asyncio
async def test_list_offers_excludes_inactive_by_default(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        await _seed_offers(
            conn,
            [
                {"code": "ACT_1", "name": "Active 1", "is_active": True},
                {"code": "ACT_2", "name": "Active 2", "is_active": True},
                {"code": "INACT", "name": "Inactive", "is_active": False},
            ],
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/offers")

    assert resp.status_code == 200
    data = resp.json()
    # Только 2 активных
    assert len(data) == 2
    codes = {o["code"] for o in data}
    assert codes == {"ACT_1", "ACT_2"}


# include_inactive=true должен вернуть все офферы включая is_active=false.
@pytest.mark.asyncio
async def test_list_offers_include_inactive(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        await _seed_offers(
            conn,
            [
                {"code": "ACT_1", "is_active": True},
                {"code": "ACT_2", "is_active": True},
                {"code": "INACT", "is_active": False},
            ],
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/offers", params={"include_inactive": "true"})

    assert resp.status_code == 200
    assert len(resp.json()) == 3


# ─────────────────────── GET /offers/compare ───────────────────────


# compare с кумулятивными метриками: берём ПОСЛЕДНИЙ snapshot за день, не сумму.
@pytest.mark.asyncio
async def test_compare_offers_with_metrics(pg_engine, fake_redis_client, clean_offers):
    """CRIT-1: два snapshot'а одного ad в одни сутки — кумулятив, не два события.

    ad_metrics пишет накопленное за сутки значение каждый scan-цикл. Два снимка
    (1h: spend=300, 2h: spend=200) — это рост кумулятива, latest (300) и есть
    дневной итог. Наивный SUM дал бы 500 (завышение). Проверяем что берётся 300.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:6]

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"COMP_{suffix}", "n": "Compare offer"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADS_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": f"230{suffix}", "n": f"AD_{suffix}"},
        )
        # Два снимка одного ad в текущие сутки: кумулятив рос 200 → 300.
        # Поздний снимок (1h назад) = дневной итог. Оба внутри 7-дневного окна.
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, leads, registrations, deposits) "
                "VALUES (gen_random_uuid(), :a, NOW() - INTERVAL '1 hour', :s, :l, :r, :d)"
            ),
            {"a": ad_id, "s": Decimal("300.00"), "l": 10, "r": 6, "d": 3},
        )
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, leads, registrations, deposits) "
                "VALUES (gen_random_uuid(), :a, NOW() - INTERVAL '2 hours', :s, :l, :r, :d)"
            ),
            {"a": ad_id, "s": Decimal("200.00"), "l": 5, "r": 4, "d": 2},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/offers/compare", params={"days": 7})

    assert resp.status_code == 200
    rows = resp.json()
    row = next((r for r in rows if r["offer_code"] == f"COMP_{suffix}"), None)
    assert row is not None, "Оффер не найден в compare-ответе"

    # Latest snapshot за день, НЕ сумма обоих снимков (которая дала бы 500).
    assert Decimal(row["spend"]) == Decimal("300.00")
    assert row["leads"] == 10
    assert row["registrations"] == 6
    assert row["deposits"] == 3
    # cost_per_lead = 300 / 10 = 30.00
    assert Decimal(row["cost_per_lead"]) == Decimal("30.00")
    # cost_per_registration = 300 / 6 = 50.00
    assert Decimal(row["cost_per_registration"]) == Decimal("50.00")
    # cost_per_deposit = 300 / 3 = 100.00
    assert Decimal(row["cost_per_deposit"]) == Decimal("100.00")


# days=200 превышает максимум 90, должен вернуть 422.
@pytest.mark.asyncio
async def test_compare_days_out_of_range(pg_engine, fake_redis_client, clean_offers):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/offers/compare", params={"days": 200})
    assert resp.status_code == 422


# ─────────────────────── POST /offers ───────────────────────


# Успешное создание оффера: 201, запись появляется в БД.
@pytest.mark.asyncio
async def test_create_offer_happy_path(pg_engine, fake_redis_client, clean_offers):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {"code": "TST_NEW", "name": "Test New", "vertical": "casino"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/offers", json=body)

    assert resp.status_code == 201
    data = resp.json()
    assert data["code"] == "TST_NEW"
    assert data["name"] == "Test New"
    assert data["vertical"] == "casino"
    assert data["is_active"] is True
    assert data["id"] is not None

    # Проверяем в БД
    async with pg_engine.connect() as conn:
        row = await conn.execute(text("SELECT code FROM offers WHERE code = 'TST_NEW'"))
        assert row.scalar_one() == "TST_NEW"


# Дубликат code должен возвращать 409 Conflict.
@pytest.mark.asyncio
async def test_create_offer_duplicate_code_returns_409(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        await _seed_offers(conn, [{"code": "DUP_CODE"}])

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/offers", json={"code": "DUP_CODE", "name": "Duplicate"})

    assert resp.status_code == 409


# Невалидный code (строчные буквы) должен возвращать 422.
@pytest.mark.asyncio
async def test_create_offer_invalid_code_lowercase_returns_422(
    pg_engine, fake_redis_client, clean_offers
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/offers", json={"code": "lowercase_code", "name": "Bad"})
    assert resp.status_code == 422


# ─────────────────────── PUT /offers/{id} ───────────────────────


# Успешное обновление оффера: name и vertical меняются.
@pytest.mark.asyncio
async def test_update_offer_happy_path(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "UPD_TST"}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/offers/{offer_id}",
            json={"name": "Updated Name", "vertical": "betting"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated Name"
    assert data["vertical"] == "betting"
    assert data["code"] == "UPD_TST"  # code не изменился


# PUT несуществующего оффера → 404.
@pytest.mark.asyncio
async def test_update_offer_not_found(pg_engine, fake_redis_client, clean_offers):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fake_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(f"/api/offers/{fake_id}", json={"name": "Ghost"})
    assert resp.status_code == 404


# code в теле PUT игнорируется: оффер возвращается с оригинальным кодом.
@pytest.mark.asyncio
async def test_update_offer_code_is_ignored(pg_engine, fake_redis_client, clean_offers):
    """code immutable — передача нового кода не должна его изменить."""
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "ORIG_CODE"}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/offers/{offer_id}",
            json={"code": "NEW_CODE", "name": "Name changed"},
        )

    assert resp.status_code == 200
    data = resp.json()
    # code должен остаться прежним
    assert data["code"] == "ORIG_CODE"
    assert data["name"] == "Name changed"


# ─────────────────────── DELETE /offers/{id} ───────────────────────


# Soft delete: is_active становится false, 204 без тела.
@pytest.mark.asyncio
async def test_delete_offer_soft_deletes(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "DEL_ME"}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/offers/{offer_id}")

    assert resp.status_code == 204

    # Проверяем в БД — запись осталась, is_active=false
    async with pg_engine.connect() as conn:
        row = await conn.execute(
            text("SELECT is_active FROM offers WHERE id = :i"), {"i": offer_id}
        )
        assert row.scalar_one() is False


# DELETE уже-inactive оффера → 404 (не идемпотентно, по документации).
@pytest.mark.asyncio
async def test_delete_offer_already_inactive_returns_404(
    pg_engine, fake_redis_client, clean_offers
):
    """Повторный delete или delete inactive → 404 (не 204 повторно)."""
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "ALREADY_DEL", "is_active": False}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/offers/{offer_id}")
    assert resp.status_code == 404


# ─────────────────────── GET /offers/{id}/rules ───────────────────────


# При отсутствии OfferRule возвращается структура с offer_id и null-порогами.
@pytest.mark.asyncio
async def test_get_offer_rules_no_rules_returns_default(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "NO_RULES"}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(f"/api/offers/{offer_id}/rules")

    assert resp.status_code == 200
    data = resp.json()
    assert str(data["offer_id"]) == str(offer_id)
    # Все пороги должны быть null
    assert data["spend_no_event_threshold"] is None
    assert data["cpa_threshold"] is None
    assert data["cpm_threshold"] is None
    assert data["ctr_threshold"] is None
    assert data["frequency_threshold"] is None
    assert data["funnel_ratio_threshold"] is None


# ─────────────────────── PUT /offers/{id}/rules ───────────────────────


# Upsert правил: новые пороги сохраняются и возвращаются.
@pytest.mark.asyncio
async def test_upsert_offer_rules_happy_path(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "HAS_RULES"}])
    offer_id = ids[0]

    body = {
        "spend_no_event_threshold": "50.00",
        "cpa_threshold": "15.00",
        "cpm_threshold": "5.00",
        "ctr_threshold": "2.50",
        "frequency_threshold": "3.00",
        "funnel_ratio_threshold": "0.30",
    }

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(f"/api/offers/{offer_id}/rules", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert Decimal(data["spend_no_event_threshold"]) == Decimal("50.00")
    assert Decimal(data["cpa_threshold"]) == Decimal("15.00")
    assert Decimal(data["ctr_threshold"]) == Decimal("2.50")

    # Повторный upsert (обновление) тоже работает
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp2 = await ac.put(f"/api/offers/{offer_id}/rules", json={"cpa_threshold": "20.00"})
    assert resp2.status_code == 200
    assert Decimal(resp2.json()["cpa_threshold"]) == Decimal("20.00")


# Отрицательный порог → 422 (Pydantic-валидация ge=0).
@pytest.mark.asyncio
async def test_upsert_offer_rules_negative_threshold_returns_422(
    pg_engine, fake_redis_client, clean_offers
):
    """Отрицательные пороги физически бессмысленны — должны отклоняться до БД."""
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "NEG_RULE"}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/offers/{offer_id}/rules",
            json={"cpa_threshold": "-10.00"},
        )
    assert resp.status_code == 422
