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
                "name": o.get("name", o["code"]),
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


# ─────────────────────── POST /offers ───────────────────────


# Успешное создание оффера: 201, запись появляется в БД (мульти-кабинет: min 1 кабинет).
@pytest.mark.asyncio
async def test_create_offer_happy_path(pg_engine, fake_redis_client, clean_offers):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    body = {
        "code": "TST_NEW",
        "vertical": "casino",
        "is_active": False,
        "ad_account_ids": ["111222333"],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/offers", json=body)

    assert resp.status_code == 201
    data = resp.json()
    assert data["code"] == "TST_NEW"
    assert data["name"] == "TST_NEW"  # бэк пишет name=code (поле «Название» убрано)
    assert data["vertical"] == "casino"
    assert data["is_active"] is False
    assert data["id"] is not None
    assert data["ad_account_ids"] == ["111222333"]

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
        resp = await ac.post(
            "/api/offers",
            json={"code": "DUP_CODE", "ad_account_ids": ["111"]},
        )

    assert resp.status_code == 409


# Мульти-кабинет: создание БЕЗ кабинетов → 422 (минимум 1 обязателен).
@pytest.mark.asyncio
async def test_create_offer_without_accounts_returns_422(
    pg_engine, fake_redis_client, clean_offers
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        no_field = await ac.post("/api/offers", json={"code": "TST_NOACC"})
        empty_list = await ac.post("/api/offers", json={"code": "TST_NOACC", "ad_account_ids": []})
    assert no_field.status_code == 422
    assert empty_list.status_code == 422


# Мульти-кабинет: act_-префикс срезается, дубли схлопываются, нечисловой ID → 422.
@pytest.mark.asyncio
async def test_create_offer_normalizes_account_ids(pg_engine, fake_redis_client, clean_offers):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ok = await ac.post(
            "/api/offers",
            json={"code": "TST_NORM", "ad_account_ids": ["act_555", "555", " 777 "]},
        )
        bad = await ac.post(
            "/api/offers", json={"code": "TST_BAD", "ad_account_ids": ["not-a-number"]}
        )
    assert ok.status_code == 201
    assert ok.json()["ad_account_ids"] == ["555", "777"]
    assert bad.status_code == 422


# Невалидный code (строчные буквы) должен возвращать 422.
@pytest.mark.asyncio
async def test_create_offer_invalid_code_lowercase_returns_422(
    pg_engine, fake_redis_client, clean_offers
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/offers",
            json={"code": "lowercase_code", "ad_account_ids": ["111"]},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ("name", "country_code", "use_vision_creator", "notes"))
async def test_create_offer_rejects_retired_fields(
    pg_engine, fake_redis_client, clean_offers, field: str
):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post(
            "/api/offers",
            json={"code": "STRICT", "ad_account_ids": ["111"], field: "legacy"},
        )
    assert resp.status_code == 422


# ─────────────────────── PUT /offers/{id} ───────────────────────


# Успешное обновление изменяемого поля.
@pytest.mark.asyncio
async def test_update_offer_happy_path(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "UPD_TST"}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/offers/{offer_id}",
            json={"vertical": "betting"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "UPD_TST"  # PUT не обновляет name — всегда = code
    assert data["vertical"] == "betting"
    assert data["code"] == "UPD_TST"  # code не изменился


# PUT несуществующего оффера → 404.
@pytest.mark.asyncio
async def test_update_offer_not_found(pg_engine, fake_redis_client, clean_offers):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    fake_id = uuid.uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(f"/api/offers/{fake_id}", json={"vertical": "betting"})
    assert resp.status_code == 404


# Immutable/retired identity fields are rejected instead of silently ignored.
@pytest.mark.asyncio
@pytest.mark.parametrize("field", ("code", "name", "country_code", "use_vision_creator", "notes"))
async def test_update_offer_rejects_immutable_or_retired_fields(
    pg_engine, fake_redis_client, clean_offers, field: str
):
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "ORIG_CODE"}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(
            f"/api/offers/{offer_id}",
            json={field: "legacy"},
        )

    assert resp.status_code == 422
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT code, name FROM offers WHERE id = :id"), {"id": offer_id}
            )
        ).one()
    assert row.code == "ORIG_CODE"
    assert row.name == "ORIG_CODE"


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


# Повторная деактивация уже-inactive оффера остаётся идемпотентной.
@pytest.mark.asyncio
async def test_deactivate_offer_already_inactive_returns_204(
    pg_engine, fake_redis_client, clean_offers
):
    """Повторный DELETE подтверждает целевое inactive-состояние."""
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "ALREADY_DEL", "is_active": False}])
    offer_id = ids[0]

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.delete(f"/api/offers/{offer_id}")
    assert resp.status_code == 204


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
    # Все пороги должны быть null (spend_no_event/cpm/ctr/funnel_ratio убраны из API — H-2)
    assert data["cpa_threshold"] is None
    assert data["frequency_threshold"] is None


# ─────────────────────── PUT /offers/{id}/rules ───────────────────────


# Upsert правил: новые пороги сохраняются и возвращаются.
@pytest.mark.asyncio
async def test_upsert_offer_rules_happy_path(pg_engine, fake_redis_client, clean_offers):
    async with pg_engine.begin() as conn:
        ids = await _seed_offers(conn, [{"code": "HAS_RULES"}])
    offer_id = ids[0]

    body = {
        "cpa_threshold": "15.00",
        "currency": "USD",
        "frequency_threshold": "3.00",
    }

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.put(f"/api/offers/{offer_id}/rules", json=body)

    assert resp.status_code == 200
    data = resp.json()
    assert Decimal(data["cpa_threshold"]) == Decimal("15.00")
    assert data["currency"] == "USD"
    assert Decimal(data["frequency_threshold"]) == Decimal("3.00")

    # Повторный upsert (обновление) тоже работает
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp2 = await ac.put(
            f"/api/offers/{offer_id}/rules",
            json={"cpa_threshold": "20.00", "currency": "USD"},
        )
    assert resp2.status_code == 200
    assert Decimal(resp2.json()["cpa_threshold"]) == Decimal("20.00")


@pytest.mark.asyncio
async def test_upsert_offer_rules_round_trips_large_kwd_exactly(
    pg_engine,
    fake_redis_client,
    clean_offers,
) -> None:
    async with pg_engine.begin() as conn:
        offer_id = (await _seed_offers(conn, [{"code": "KWD_EXACT"}]))[0]
    cpa = "9007199254740.123"

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.put(
            f"/api/offers/{offer_id}/rules",
            json={"cpa_threshold": cpa, "currency": "KWD"},
        )
        persisted = await ac.get(f"/api/offers/{offer_id}/rules")

    assert response.status_code == 200
    assert persisted.status_code == 200
    assert Decimal(response.json()["cpa_threshold"]) == Decimal(cpa)
    assert Decimal(persisted.json()["cpa_threshold"]) == Decimal(cpa)
    assert persisted.json()["currency"] == "KWD"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("currency", "cpa"),
    [
        ("JPY", "1000"),
        ("KWD", "3.125"),
        ("KWD", "9007199254740.123"),
    ],
)
async def test_rule_preview_keeps_exact_decimal_query_string(
    pg_engine,
    fake_redis_client,
    currency: str,
    cpa: str,
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            "/api/offers/rules/preview",
            params={
                "cpa": cpa,
                "currency": currency,
                "stop_percent_of_rule": "80",
                "warning_percent_of_stop": "80",
            },
        )

    assert response.status_code == 200
    assert Decimal(response.json()["cpa"]) == Decimal(cpa)
    assert response.json()["currency"] == currency


@pytest.mark.asyncio
async def test_rule_preview_rejects_jpy_fraction(
    pg_engine,
    fake_redis_client,
) -> None:
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            "/api/offers/rules/preview",
            params={"cpa": "1000.1", "currency": "JPY"},
        )

    assert response.status_code == 422


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
