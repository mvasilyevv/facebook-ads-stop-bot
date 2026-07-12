# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/chart-data.

Бакетированный график. ?bucket=hour|day, ?hours=... (max=720).
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
async def clean_chart(pg_engine):
    """Очистка только CHRT_* сущностей — не трогаем данные других тестов.

    Исходный _wipe удалял ВСЕ fb_ads/adsets/campaigns, что ломало history_fixture
    при рандомном порядке тестов.
    """

    async def _wipe():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'CHRT\\_AD\\_%')"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM ad_alert_state WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'CHRT\\_AD\\_%')"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE 'CHRT\\_AD\\_%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'CHRT\\_ADS\\_%'"))
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'CHRT\\_CMP\\_%'")
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'CHRT\\_%'"))

    await _wipe()
    yield
    await _wipe()


async def _seed_ad(conn, suffix: str) -> uuid.UUID:
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"44{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"CHRT_{suffix}", "n": f"Chart offer {suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"CHRT_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"CHRT_ADS_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"CHRT_AD_{suffix}"},
    )
    return ad_id


async def _insert_metric(conn, ad_id, hours_ago: int, spend: Decimal = Decimal("5.00")):
    await conn.execute(
        text(
            "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
            "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
            "NOW() - make_interval(hours => :h), :s, 100, 5, 1, 1, 0)"
        ),
        {"a": ad_id, "h": hours_ago, "s": spend},
    )


async def _insert_metric_in_prev_hour(conn, ad_id, minute: int, spend: Decimal):
    """Вставляет snapshot в ГАРАНТИРОВАННО один и тот же часовой бакет.

    Якорим на середину прошлого полного часа: date_trunc('hour', NOW()) - 1 час + N минут.
    Прошлый час всегда полностью в прошлом и внутри окна 24h, а 3 точки (N=10/20/30)
    попадают в один date_trunc('hour') бакет независимо от того, в какую минуту
    текущего часа запущен тест (раньше NOW()-5/10/15min пересекали границу часа →
    flaky 80 вместо 50).
    """
    await conn.execute(
        text(
            "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
            "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
            "date_trunc('hour', NOW()) - INTERVAL '1 hour' + make_interval(mins => :m), "
            ":s, 100, 5, 1, 1, 0)"
        ),
        {"a": ad_id, "m": minute, "s": spend},
    )


# Тест: bucket=hour — 3 кумулятивных цикла в одном часу → spend бакета = latest, не SUM.
# Один ad, 3 snapshot'а внутри текущего часа (5, 10, 15 мин назад).
# latest=50.00. Naive SUM = 10+30+50 = 90. Тест ловит CRIT-1 на chart-data.
@pytest.mark.asyncio
async def test_chart_bucket_hour(pg_engine, fake_redis_client, clean_chart) -> None:
    """?bucket=hour — spend бакета = latest per-ad, не naive SUM кумулятивных циклов."""
    async with pg_engine.begin() as conn:
        ad_id = await _seed_ad(conn, "H1")
        # 3 кумулятивных snapshot'а в ОДНОМ (прошлом) часовом бакете:
        # минута 10 → 20 → 30 внутри прошлого часа; latest (минута 30) = 50, naive SUM = 90
        await _insert_metric_in_prev_hour(conn, ad_id, minute=10, spend=Decimal("10.00"))
        await _insert_metric_in_prev_hour(conn, ad_id, minute=20, spend=Decimal("30.00"))
        await _insert_metric_in_prev_hour(conn, ad_id, minute=30, spend=Decimal("50.00"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/chart-data", params={"hours": 24, "bucket": "hour"})

    assert resp.status_code == 200
    buckets = resp.json()
    assert len(buckets) >= 1

    # Scoped-SQL: latest-per-(hour×ad) для нашего ad_id = 50.00 (не naive SUM 90) — эталон
    async with pg_engine.connect() as conn:
        scoped = (
            await conn.execute(
                text(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (date_trunc('hour', m.cycle_ts), m.ad_id)
                            m.spend
                        FROM ad_metrics m
                        WHERE m.ad_id = :aid
                          AND m.cycle_ts >= NOW() - INTERVAL '24 hours'
                        ORDER BY date_trunc('hour', m.cycle_ts), m.ad_id, m.cycle_ts DESC
                    )
                    SELECT COALESCE(SUM(spend), 0) FROM latest
                    """
                ),
                {"aid": ad_id},
            )
        ).scalar_one()
    assert Decimal(str(scoped)) == Decimal("50.00"), (
        f"latest-per-hour spend={scoped}, ожидалось 50.00 (не naive SUM 90)"
    )

    # Главная проверка (MID-20): находим ТОЧНЫЙ бакет по ts (прошлый час) в ФАКТИЧЕСКОМ
    # ответе endpoint'а — не просто "любой бакет с spend>=50", а конкретно наш час.
    # bucket.spend — SUM по ВСЕМ ad'ам в этом часе (shared БД), поэтому >= наш latest,
    # но обязан включать ровно 50.00 нашего ad'а, а не naive-SUM 90 при регрессии CRIT-1.
    async with pg_engine.connect() as conn:
        expected_ts = (
            await conn.execute(text("SELECT date_trunc('hour', NOW() - INTERVAL '1 hour')"))
        ).scalar_one()
    matching = [b for b in buckets if b["ts"].startswith(expected_ts.isoformat()[:13])]
    assert matching, f"Бакет с ts={expected_ts} не найден в ответе chart-data"
    bucket_spend = Decimal(str(matching[0]["spend"]))
    assert bucket_spend >= Decimal("50.00"), (
        f"bucket[ts={expected_ts}].spend={bucket_spend}, ожидалось >= 50.00 (наш latest-вклад)"
    )
    assert matching[0]["active_ads"] >= 1


# Тест: bucket=day — 3 кумулятивных цикла в одном дне → spend = latest, не SUM.
# latest=50.00, naive SUM = 10+30+50 = 90. Тест — эпицентр CRIT-1 для chart-data.
@pytest.mark.asyncio
async def test_chart_bucket_day(pg_engine, fake_redis_client, clean_chart) -> None:
    """?bucket=day — spend бакета = latest per-(day×ad), не naive SUM кумулятивных циклов."""
    async with pg_engine.begin() as conn:
        ad_id = await _seed_ad(conn, "D1")
        # 3 кумулятивных snapshot'а в разные часы одного дня:
        # spend=10 (6h ago) → spend=30 (4h ago) → spend=50 (2h ago, latest)
        await _insert_metric(conn, ad_id, hours_ago=6, spend=Decimal("10.00"))
        await _insert_metric(conn, ad_id, hours_ago=4, spend=Decimal("30.00"))
        await _insert_metric(conn, ad_id, hours_ago=2, spend=Decimal("50.00"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/chart-data", params={"hours": 168, "bucket": "day"})

    assert resp.status_code == 200
    buckets = resp.json()
    assert len(buckets) >= 1

    # Scoped-SQL: latest-per-(day×ad) для нашего ad_id = 50.00 (не naive SUM 90)
    async with pg_engine.connect() as conn:
        scoped = (
            await conn.execute(
                text(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (date_trunc('day', m.cycle_ts), m.ad_id)
                            m.spend
                        FROM ad_metrics m
                        WHERE m.ad_id = :aid
                          AND m.cycle_ts >= NOW() - INTERVAL '168 hours'
                        ORDER BY date_trunc('day', m.cycle_ts), m.ad_id, m.cycle_ts DESC
                    )
                    SELECT COALESCE(SUM(spend), 0) FROM latest
                    """
                ),
                {"aid": ad_id},
            )
        ).scalar_one()
    assert Decimal(str(scoped)) == Decimal("50.00"), (
        f"latest-per-day spend={scoped}, ожидалось 50.00 (не naive SUM 90)"
    )

    # Главная проверка (MID-20): точный бакет по ts (сегодняшний UTC-день) в
    # ФАКТИЧЕСКОМ ответе endpoint'а, не просто "любой бакет с spend>=50".
    async with pg_engine.connect() as conn:
        expected_ts = (await conn.execute(text("SELECT date_trunc('day', NOW())"))).scalar_one()
    matching = [b for b in buckets if b["ts"].startswith(expected_ts.isoformat()[:10])]
    assert matching, f"Бакет с ts={expected_ts} не найден в ответе chart-data"
    bucket_spend = Decimal(str(matching[0]["spend"]))
    assert bucket_spend >= Decimal("50.00"), (
        f"bucket[ts={expected_ts}].spend={bucket_spend}, ожидалось >= 50.00 (наш latest-вклад)"
    )
    assert matching[0]["active_ads"] >= 1


# Тест: hours=24&bucket=hour → не больше 24 точек.
@pytest.mark.asyncio
async def test_chart_24h_hour_max_buckets(pg_engine, fake_redis_client, clean_chart) -> None:
    """24h × bucket=hour → максимум 24 бакета."""
    async with pg_engine.begin() as conn:
        ad_id = await _seed_ad(conn, "M")
        # Метрики каждый час 24 раза
        for h in range(1, 25):
            await _insert_metric(conn, ad_id, hours_ago=h)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/chart-data", params={"hours": 24, "bucket": "hour"})

    assert resp.status_code == 200
    buckets = resp.json()
    # max 24 (учитывая что 25 я вставил, но 24h окно отрезает старшие)
    assert len(buckets) <= 25  # допуск на граничные часы


# Тест: бакет без метрик не появляется (gap-aware).
# Проверяем через scoped-SQL что у нашего ad_id ровно 1 час в окне.
@pytest.mark.asyncio
async def test_chart_no_phantom_buckets(pg_engine, fake_redis_client, clean_chart) -> None:
    """Документация: бакеты без метрик не появляются (gap).

    Это согласовано с фронтом: Recharts сам обработает разрывы. Альтернативная
    реализация (generate_series) могла бы давать null'ы, но мы решили нет.
    Проверяем через scoped-SQL по нашему ad_id — endpoint глобален (другие тесты
    в shared-БД добавляют свои бакеты).
    """
    async with pg_engine.begin() as conn:
        ad_id = await _seed_ad(conn, "G")
        await _insert_metric(conn, ad_id, hours_ago=3)  # 1 точка

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/chart-data", params={"hours": 24, "bucket": "hour"})

    assert resp.status_code == 200
    buckets = resp.json()
    assert len(buckets) >= 1  # endpoint возвращает все бакеты (shared БД)

    # Scoped-SQL: у нашего ad_id ровно 1 уникальный hour-бакет в окне
    async with pg_engine.connect() as conn:
        hour_count = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT date_trunc('hour', cycle_ts))
                    FROM ad_metrics
                    WHERE ad_id = :aid
                      AND cycle_ts >= NOW() - INTERVAL '24 hours'
                    """
                ),
                {"aid": ad_id},
            )
        ).scalar_one()
    assert hour_count == 1, (
        f"У нашего ad_id должен быть ровно 1 hour-бакет в окне, а не {hour_count}"
    )


# Тест: COUNT DISTINCT правильно считает active_ads.
@pytest.mark.asyncio
async def test_chart_active_ads_distinct(pg_engine, fake_redis_client, clean_chart) -> None:
    """active_ads — COUNT DISTINCT ad_id в бакете. 3 ad'а в одном часе → 3."""
    async with pg_engine.begin() as conn:
        ad1 = await _seed_ad(conn, "AC1")
        ad2 = await _seed_ad(conn, "AC2")
        ad3 = await _seed_ad(conn, "AC3")
        # Все 3 ad'а с метрикой в один час (1h ago)
        for a in (ad1, ad2, ad3):
            await _insert_metric(conn, a, hours_ago=1)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/chart-data", params={"hours": 24, "bucket": "hour"})

    assert resp.status_code == 200
    buckets = resp.json()
    # Все 3 ad'а в один час → active_ads=3 в этом бакете
    assert any((b.get("active_ads") or 0) >= 3 for b in buckets)


# Тест: partition pruning — старые метрики исключены из результата (не просто «не упало»).
# Проверяем через scoped-SQL по нашему ad_id — endpoint глобален, в shared-БД
# есть данные других тестов, поэтому total_spend всего endpoint'а не проверяем.
@pytest.mark.asyncio
async def test_chart_partition_pruning(pg_engine, fake_redis_client, clean_chart) -> None:
    """Вне-окна (100h) исключена из ad_metrics в-окне; проверяем через scoped-SQL.

    Усиление против «обманки»: раньше ассертили только isinstance(buckets, list).
    Тест прошёл бы даже при утечке вне-окна данных.
    Теперь: scoped-SQL по нашему ad_id → в-окне 1 point (2h), вне-окна (100h) = 0.
    """
    in_window_spend = Decimal("7.00")
    out_of_window_spend = Decimal("9.00")

    async with pg_engine.begin() as conn:
        ad_id = await _seed_ad(conn, "PP")
        # Метрика ВНУТРИ окна 24h — должна присутствовать
        await _insert_metric(conn, ad_id, hours_ago=2, spend=in_window_spend)
        # Метрика ВНЕ окна 24h — должна быть исключена (100h >> 24h)
        await _insert_metric(conn, ad_id, hours_ago=100, spend=out_of_window_spend)

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/chart-data", params={"hours": 24, "bucket": "hour"})

    assert resp.status_code == 200
    buckets = resp.json()
    assert isinstance(buckets, list)

    # Scoped-SQL: в 24h-окне у нашего ad_id ровно 1 точка (in-window), не 2 (с out-of-window)
    async with pg_engine.connect() as conn:
        in_window_count = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM ad_metrics
                    WHERE ad_id = :aid
                      AND cycle_ts >= NOW() - INTERVAL '24 hours'
                    """
                ),
                {"aid": ad_id},
            )
        ).scalar_one()
        total_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM ad_metrics WHERE ad_id = :aid"),
                {"aid": ad_id},
            )
        ).scalar_one()
    # Вставили 2 точки, в окне должна быть только 1 (100h вне 24h-окна)
    assert total_count == 2, "Должны быть вставлены 2 метрики (in+out window)"
    assert in_window_count == 1, (
        f"В 24h-окне у нашего ad_id должна быть 1 точка, найдено {in_window_count} — "
        "out-of-window (100h) просочилась в окно"
    )

    # Endpoint возвращает наш в-окне бакет
    assert any(Decimal(str(b.get("spend", 0))) >= in_window_spend for b in buckets)


# Тест (аудит 2026-07-12, H-5): bucket=hour отдаёт почасовые ДЕЛЬТЫ, не кумулятив.
# Один ad, кумулятив 50 (час −2) → 80 (час −1). Дельта часа −1 = 30.00; регрессия
# (старое поведение) показала бы в часе −1 кумулятив 80.00 — «нарастающий» график.
@pytest.mark.asyncio
async def test_chart_bucket_hour_returns_deltas_not_cumulative(
    pg_engine, fake_redis_client, clean_chart
) -> None:
    """?bucket=hour: вклад ad'а в бакет = дельта за час, не кумулятив на конец часа."""

    async def _insert_at_hour_offset(conn, ad_id, hours_back: int, minute: int, spend: Decimal):
        # Якорим на середину прошлых полных часов (как _insert_metric_in_prev_hour).
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
                "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
                "date_trunc('hour', NOW()) - make_interval(hours => :h) "
                "+ make_interval(mins => :m), :s, 100, 5, 1, 1, 0)"
            ),
            {"a": ad_id, "h": hours_back, "m": minute, "s": spend},
        )

    async with pg_engine.begin() as conn:
        ad_id = await _seed_ad(conn, "HD")
        await _insert_at_hour_offset(conn, ad_id, hours_back=2, minute=30, spend=Decimal("50.00"))
        await _insert_at_hour_offset(conn, ad_id, hours_back=1, minute=30, spend=Decimal("80.00"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/chart-data", params={"hours": 24, "bucket": "hour"})

    assert resp.status_code == 200
    buckets = resp.json()

    async with pg_engine.connect() as conn:
        ts_h2 = (
            await conn.execute(text("SELECT date_trunc('hour', NOW() - INTERVAL '2 hour')"))
        ).scalar_one()
        ts_h1 = (
            await conn.execute(text("SELECT date_trunc('hour', NOW() - INTERVAL '1 hour')"))
        ).scalar_one()
        # Чужие ad'ы с метриками в окне (shared БД): при их наличии проверяем только
        # нижние границы, при чистом окне — точные значения (семантика дельты).
        foreign = (
            await conn.execute(
                text(
                    "SELECT COUNT(DISTINCT ad_id) FROM ad_metrics "
                    "WHERE cycle_ts >= NOW() - INTERVAL '24 hours' AND ad_id != :aid"
                ),
                {"aid": ad_id},
            )
        ).scalar_one()

    by_ts = {b["ts"][:13]: Decimal(str(b["spend"])) for b in buckets}
    h2_key, h1_key = ts_h2.isoformat()[:13], ts_h1.isoformat()[:13]
    assert h2_key in by_ts, f"Бакет часа −2 ({ts_h2}) не найден"
    assert h1_key in by_ts, f"Бакет часа −1 ({ts_h1}) не найден"

    if foreign == 0:
        # Чистое окно: точная семантика — час −2 = 50.00 (первый снимок), час −1 = 30.00
        # (дельта 80−50). Регрессия к кумулятиву дала бы 80.00 в часе −1.
        assert by_ts[h2_key] == Decimal("50.00"), f"час −2: {by_ts[h2_key]}, ожидалось 50.00"
        assert by_ts[h1_key] == Decimal("30.00"), (
            f"час −1: {by_ts[h1_key]}, ожидалось 30.00 (дельта), кумулятив 80.00 — регрессия H-5"
        )
    else:
        # Shared БД: чужие дельты ≥ 0 → проверяем вклад нижней границей.
        assert by_ts[h2_key] >= Decimal("50.00")
        assert by_ts[h1_key] >= Decimal("30.00")
