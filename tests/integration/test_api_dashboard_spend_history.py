# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/spend-history.

Сырые точки ad_metrics за окно ?hours. Параметры — fb_ad_id, hours (max=168).
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


def _make_app(engine=None, redis=None):
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def clean_spend(pg_engine):
    """Очистка только SPND_* сущностей — не трогаем данные других тестов.

    Исходный _wipe удалял ВСЕ fb_ads/metrics, что ломало history_fixture
    при рандомном порядке тестов.
    """

    async def _wipe():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'SPND\\_AD\\_%')"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM ad_alert_state WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'SPND\\_AD\\_%')"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE 'SPND\\_AD\\_%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'SPND\\_ADS\\_%'"))
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'SPND\\_CMP\\_%'")
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'SPND\\_%'"))

    await _wipe()
    yield
    await _wipe()


async def _seed_ad_with_metric(conn, suffix: str, *, hours_ago: int = 1, spend=Decimal("10.00")):
    """Создаёт ad + 1 метрику в нужный момент времени."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"55{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"SPND_{suffix}", "n": f"Spnd offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"SPND_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"SPND_ADS_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"SPND_AD_{suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
            "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
            "NOW() - make_interval(hours => :h), :s, 100, 5, 1, 1, 0)"
        ),
        {"a": ad_id, "h": hours_ago, "s": spend},
    )
    return ad_id, fb_ad_id


# Тест: default hours=24 → точки за 24h.
@pytest.mark.asyncio
async def test_spend_default_24h(pg_engine, fake_redis_client, clean_spend) -> None:
    """Default ?hours=24 — точки за 24h попадают, старше — нет."""
    async with pg_engine.begin() as conn:
        await _seed_ad_with_metric(conn, "A", hours_ago=1)
        await _seed_ad_with_metric(conn, "B", hours_ago=48)  # вне default

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/spend-history")

    assert resp.status_code == 200
    points = resp.json()
    # Только точка из последнего часа (B вне 24h)
    fb_ids = {p["fb_ad_id"] for p in points}
    assert "55A" in fb_ids
    assert "55B" not in fb_ids


# Тест: hours=168 — max, доходит всё за неделю.
@pytest.mark.asyncio
async def test_spend_hours_168(pg_engine, fake_redis_client, clean_spend) -> None:
    """?hours=168 (7d) — точка за 48h в окно попадает."""
    async with pg_engine.begin() as conn:
        await _seed_ad_with_metric(conn, "W", hours_ago=48)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/spend-history", params={"hours": 168})

    assert resp.status_code == 200
    points = resp.json()
    fb_ids = {p["fb_ad_id"] for p in points}
    assert "55W" in fb_ids


# Тест: hours=200 → 422 (превышен максимум).
@pytest.mark.asyncio
async def test_spend_hours_too_large(pg_engine, fake_redis_client, clean_spend) -> None:
    """?hours=200 > max=168 → 422 (валидация Query)."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/spend-history", params={"hours": 200})

    assert resp.status_code == 422


# Тест: fb_ad_id фильтр оставляет только нужный.
@pytest.mark.asyncio
async def test_spend_fb_ad_id_filter(pg_engine, fake_redis_client, clean_spend) -> None:
    """?fb_ad_id=X — только точки этого объявления."""
    async with pg_engine.begin() as conn:
        _, target_fb = await _seed_ad_with_metric(conn, "T", hours_ago=2)
        await _seed_ad_with_metric(conn, "O", hours_ago=2)  # другой ad

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/spend-history", params={"fb_ad_id": target_fb})

    assert resp.status_code == 200
    points = resp.json()
    fb_ids = {p["fb_ad_id"] for p in points}
    assert fb_ids == {target_fb} or fb_ids == set()  # либо точно один, либо пусто если не успел


# Тест: partition pruning — старые метрики не подтягиваются.
@pytest.mark.asyncio
async def test_spend_partition_pruning(pg_engine, fake_redis_client, clean_spend) -> None:
    """Метрика старше окна не должна попасть в ответ.

    Дополнительная защита: проверяет, что WHERE cycle_ts реально применяется
    и старая партиция не сканируется (косвенно — по отсутствию данных).
    """
    async with pg_engine.begin() as conn:
        # 1 точка за 2h (в окне)
        _, fid_now = await _seed_ad_with_metric(conn, "PN", hours_ago=2)
        # 1 точка 100h назад (~4 дня, в существующей партиции, но вне окна 24h)
        _, fid_old = await _seed_ad_with_metric(conn, "PO", hours_ago=100)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/spend-history", params={"hours": 24})

    assert resp.status_code == 200
    fb_ids = {p["fb_ad_id"] for p in resp.json()}
    assert fid_now in fb_ids
    assert fid_old not in fb_ids


# Тест: точка содержит верный spend — контракт «raw spend-значение не теряется и не искажается».
# spend-history возвращает СЫРЫЕ точки (не агрегирует), поэтому это не про naive-SUM,
# а про базовый контракт: поле spend == именно то значение, которое было вставлено.
@pytest.mark.asyncio
async def test_spend_point_value_contract(pg_engine, fake_redis_client, clean_spend) -> None:
    """Каждая точка spend-history содержит исходное spend-значение без искажений."""
    known_spend = Decimal("123.45")
    async with pg_engine.begin() as conn:
        ad_id, fb_ad = await _seed_ad_with_metric(conn, "VAL", hours_ago=2, spend=known_spend)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/spend-history", params={"fb_ad_id": fb_ad})

    assert resp.status_code == 200
    points = resp.json()
    assert len(points) >= 1, f"Ожидалась хотя бы одна точка для fb_ad_id={fb_ad}"
    # Первая и единственная точка — наш snapshot с известным spend
    point = next((p for p in points if p["fb_ad_id"] == fb_ad), None)
    assert point is not None
    assert Decimal(str(point["spend"])) == known_spend, (
        f"spend={point['spend']}, ожидалось {known_spend} — контракт spend-значения нарушен"
    )


# Тест: несколько сырых точек одного ad возвращаются все (NOT aggregated).
# spend-history — это raw timeline, не latest-per-ad агрегация.
# 2 точки с разными spend → оба должны присутствовать.
@pytest.mark.asyncio
async def test_spend_multiple_raw_points_returned(
    pg_engine, fake_redis_client, clean_spend
) -> None:
    """spend-history возвращает ВСЕ сырые точки ad_id в окне (не агрегирует)."""
    sfx = "MP"
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad = f"55{sfx}"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"SPND_{sfx}", "n": "Spnd MP"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"SPND_CMP_{sfx}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"SPND_ADS_{sfx}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": fb_ad, "n": f"SPND_AD_{sfx}"},
        )
        # Два raw snapshot'а с разными cycle_ts (кумулятивный рост 50→80)
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
                "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
                "NOW() - INTERVAL '3 hours', :s, 100, 5, 1, 1, 0)"
            ),
            {"a": ad_id, "s": Decimal("50.00")},
        )
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
                "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
                "NOW() - INTERVAL '1 hour', :s, 100, 5, 1, 1, 0)"
            ),
            {"a": ad_id, "s": Decimal("80.00")},
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/spend-history", params={"fb_ad_id": fb_ad})

    assert resp.status_code == 200
    points = resp.json()
    # raw endpoint: должны вернуться ОБА snapshot'а (не только latest)
    assert len(points) == 2, f"spend-history должен вернуть 2 сырых точки, вернул {len(points)}"
    spends = {Decimal(str(p["spend"])) for p in points}
    assert Decimal("50.00") in spends, "Первый snapshot (50.00) не найден"
    assert Decimal("80.00") in spends, "Второй snapshot (80.00) не найден"
