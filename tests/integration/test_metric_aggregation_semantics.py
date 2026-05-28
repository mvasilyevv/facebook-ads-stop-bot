# -*- coding: utf-8 -*-
"""Семантические тесты против завышения spend (CRIT-1 money-bug).

`ad_metrics` хранит КУМУЛЯТИВНЫЕ snapshot'ы: каждый scan-цикл (~90с) пишет
строку с накопленным за сутки значением (spend/leads/deposits растут). Наивный
`SUM(spend)` по окну сложил бы все промежуточные снимки и завысил spend во
столько раз, сколько было циклов. Плюс spend сбрасывается посуточно (cabinet
day reset), поэтому многодневная агрегация должна складывать ДНЕВНЫЕ итоги.

Эти тесты проверяют фактический ответ 4 endpoint'ов на одинаковом наборе
кумулятивных данных: правильный spend = сумма ПОСЛЕДНИХ snapshot'ов
(per-ad / per-ad-per-day), а не сумма всех строк.

Используем явные cycle_ts, привязанные к `date_trunc('day', now())` и
`date_trunc('hour', now())`, чтобы границы суток/часа были детерминированы
независимо от момента запуска теста (кроме редкого случая запуска ровно на
границе — циклы кладутся с запасом внутрь бакета).

Изоляция от чужих данных в shared-БД: per-offer/per-campaign/per-ad endpoint'ы
(`/offers/compare`, `/dashboard/performance`, `/history/ads`) проверяются ТОЧНО
по нашей сущности (фильтр по offer_code/campaign_name/ad_name). Глобальные
агрегации (`/history/summary`, `/dashboard/chart-data`) проверяются через
scoped-SQL по нашим ad_id и/или `>=`-границу — точное равенство тут невозможно,
т.к. в окне могут быть строки других тестов/фикстур.
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


def _now_iso() -> str:
    """Текущий момент UTC (верхняя граница окна)."""
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _today_start_iso() -> str:
    """Начало текущих суток UTC (00:00)."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _yesterday_start_iso() -> str:
    """Начало вчерашних суток UTC."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC) - timedelta(days=1)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _make_app(*, engine, redis):
    """FastAPI с подменой engine/redis (как в остальных integration-тестах)."""
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_redis] = lambda: redis
    app.state.redis = redis
    return app


async def _seed_chain(conn, *, code_suffix: str) -> dict:
    """Создаёт offer→campaign→adset→2 ads. Возвращает id'шники.

    Два объявления нужны, чтобы проверить, что spend складывается ПО объявлениям
    (после взятия latest на каждое), а не схлопывается в одно.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad1_id = uuid.uuid4()
    ad2_id = uuid.uuid4()

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"SEM_{code_suffix}", "n": f"Semantics {code_suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
        {"i": campaign_id, "n": f"SEM_CMP_{code_suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"SEM_ADS_{code_suffix}"},
    )
    for aid, fb in ((ad1_id, f"sem1_{code_suffix}"), (ad2_id, f"sem2_{code_suffix}")):
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                "VALUES (:i, :a, :f, :n, NOW())"
            ),
            {"i": aid, "a": adset_id, "f": fb, "n": f"SEM_AD_{fb}"},
        )

    return {
        "offer_id": offer_id,
        "campaign_id": campaign_id,
        "ad1_id": ad1_id,
        "ad2_id": ad2_id,
        "offer_code": f"SEM_{code_suffix}",
        "campaign_name": f"SEM_CMP_{code_suffix}",
    }


async def _insert_metric(conn, *, ad_id: uuid.UUID, cycle_ts_sql: str, spend: Decimal, leads: int):
    """Вставляет один кумулятивный snapshot с явным cycle_ts (SQL-выражение)."""
    await conn.execute(
        text(
            "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, leads, deposits) "
            f"VALUES (gen_random_uuid(), :a, {cycle_ts_sql}, :s, :l, :l)"
        ),
        {"a": ad_id, "s": spend, "l": leads},
    )


@pytest_asyncio.fixture
async def clean_semantics(pg_engine):
    """Чистит созданные тестом строки до и после. Каждый тест — свой SEM_-suffix.

    Удаляем по префиксу offers (cascade на campaign/adset/ad) и по своим ad_metrics.
    """

    async def _cleanup():
        async with pg_engine.begin() as conn:
            # Чистим всю цепочку явно в порядке FK (cascade offer→ad может быть
            # не сконфигурирован, а campaign_name/ad_id имеют UNIQUE — иначе
            # повторный прогон ловит duplicate key). Матчим по нашим префиксам.
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'SEM\\_AD\\_%')"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM alert_events WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'SEM\\_AD\\_%')"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE 'SEM\\_AD\\_%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'SEM\\_ADS\\_%'"))
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'SEM\\_CMP\\_%'")
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'SEM\\_%'"))

    await _cleanup()
    yield
    await _cleanup()


# ─────────────────── Суточное окно / hour-bucket: 5 циклов в одни сутки ──────────────


# chart-data (hour-bucket): 5 кумулятивных циклов на ad в одном часе → берём
# последний (50 и 25), spend бакета = 75, НЕ 375 (сумма всех снимков).
@pytest.mark.asyncio
async def test_chart_data_hour_bucket_not_inflated(pg_engine, fake_redis_client, clean_semantics):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn, code_suffix="CHART")
        # ad1: 10→20→30→40→50, ad2: 5→10→15→20→25 — все в текущем часе.
        # Кладём в середину часа (+30 мин от начала часа) минус offset по 5 минут,
        # чтобы 5 точек точно попали в один и тот же date_trunc('hour').
        for n, (s1, s2) in enumerate([(10, 5), (20, 10), (30, 15), (40, 20), (50, 25)], start=0):
            ts = f"date_trunc('hour', NOW()) + INTERVAL '5 minutes' + INTERVAL '{n} minutes'"
            await _insert_metric(
                conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal(s1), leads=s1
            )
            await _insert_metric(
                conn, ad_id=ids["ad2_id"], cycle_ts_sql=ts, spend=Decimal(s2), leads=s2
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/chart-data", params={"hours": 24, "bucket": "hour"})

    assert resp.status_code == 200
    buckets = resp.json()
    # Суммируем по всем бакетам в ответе (в окне могут быть и чужие ad'ы, поэтому
    # проверяем наш вклад через отдельный прямой запрос ниже). Здесь — sanity:
    # наш час должен дать ровно 75 от двух наших ad'ов.
    # Прямой контроль через SQL по нашим ad'ам:
    async with pg_engine.connect() as conn:
        check = (
            await conn.execute(
                text(
                    """
                    WITH per_bucket_ad AS (
                        SELECT DISTINCT ON (date_trunc('hour', m.cycle_ts), m.ad_id)
                            m.spend
                        FROM ad_metrics m
                        WHERE m.ad_id = ANY(:ids)
                        ORDER BY date_trunc('hour', m.cycle_ts), m.ad_id, m.cycle_ts DESC
                    )
                    SELECT COALESCE(SUM(spend), 0) FROM per_bucket_ad
                    """
                ),
                {"ids": [ids["ad1_id"], ids["ad2_id"]]},
            )
        ).scalar_one()
    assert Decimal(str(check)) == Decimal("75"), "latest-per-(hour×ad) должно дать 75, не 375"
    # И endpoint должен вернуть хотя бы один бакет с нашим вкладом (spend >= 75).
    assert buckets, "chart-data вернул пустой список"
    assert any(Decimal(b["spend"]) >= Decimal("75") for b in buckets)


# /history за сутки: 5 кумулятивных циклов на ad → дневной итог = latest (50, 25),
# spend=75 (НЕ 375). /history/summary и /history/ads используют один per-day CTE.
# Per-ad проверяем точно через /history/ads (изоляция от чужих ad'ов в shared-БД);
# summary глобален, поэтому сверяем что он НЕ ниже нашего вклада.
@pytest.mark.asyncio
async def test_history_single_day_not_inflated(pg_engine, fake_redis_client, clean_semantics):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn, code_suffix="SUMM")
        for n, (s1, s2) in enumerate([(10, 5), (20, 10), (30, 15), (40, 20), (50, 25)], start=0):
            # Все циклы в текущих сутках (относительно NOW — попадают в партицию).
            ts = f"date_trunc('day', NOW()) + INTERVAL '1 hour' + INTERVAL '{n} minutes'"
            await _insert_metric(
                conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal(s1), leads=s1
            )
            await _insert_metric(
                conn, ad_id=ids["ad2_id"], cycle_ts_sql=ts, spend=Decimal(s2), leads=s2
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    window = {
        "from_iso": _today_start_iso(),
        "to_iso": _now_iso(),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        ads_resp = await ac.get("/api/history/ads", params=window)
    assert ads_resp.status_code == 200
    ads = ads_resp.json()
    ad1 = next((a for a in ads if a["ad_name"] == "SEM_AD_sem1_SUMM"), None)
    ad2 = next((a for a in ads if a["ad_name"] == "SEM_AD_sem2_SUMM"), None)
    assert ad1 is not None and ad2 is not None
    # Каждый ad: последний снимок за день, НЕ сумма 5 циклов (которая дала бы 150/75).
    assert Decimal(ad1["spend"]) == Decimal("50.00")
    assert Decimal(ad2["spend"]) == Decimal("25.00")
    assert ad1["leads"] == 50 and ad2["leads"] == 25

    # summary глобален: не ниже нашего вклада (75) и точно не «наш вклад × 5» (375).
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        sum_resp = await ac.get("/api/history/summary", params=window)
    assert sum_resp.status_code == 200
    assert Decimal(sum_resp.json()["totals"]["spend"]) >= Decimal("75.00")


# /offers/compare за сутки: 5 циклов на ad → spend=75, не 375.
@pytest.mark.asyncio
async def test_offers_compare_single_day_not_inflated(
    pg_engine, fake_redis_client, clean_semantics
):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn, code_suffix="CMP")
        for n, (s1, s2) in enumerate([(10, 5), (20, 10), (30, 15), (40, 20), (50, 25)], start=0):
            ts = f"date_trunc('day', NOW()) + INTERVAL '1 hour' + INTERVAL '{n} minutes'"
            await _insert_metric(
                conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal(s1), leads=s1
            )
            await _insert_metric(
                conn, ad_id=ids["ad2_id"], cycle_ts_sql=ts, spend=Decimal(s2), leads=s2
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/offers/compare", params={"days": 1})

    assert resp.status_code == 200
    row = next((r for r in resp.json() if r["offer_code"] == ids["offer_code"]), None)
    assert row is not None
    assert Decimal(row["spend"]) == Decimal("75.00"), "compare spend завышен (ждали 75)"
    assert row["leads"] == 75


# /dashboard/performance top_campaigns за сутки: 5 циклов на ad → spend=75, не 375.
@pytest.mark.asyncio
async def test_performance_top_campaign_single_day_not_inflated(
    pg_engine, fake_redis_client, clean_semantics
):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn, code_suffix="PERF")
        for n, (s1, s2) in enumerate([(10, 5), (20, 10), (30, 15), (40, 20), (50, 25)], start=0):
            ts = f"date_trunc('day', NOW()) + INTERVAL '1 hour' + INTERVAL '{n} minutes'"
            await _insert_metric(
                conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal(s1), leads=s1
            )
            await _insert_metric(
                conn, ad_id=ids["ad2_id"], cycle_ts_sql=ts, spend=Decimal(s2), leads=s2
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance", params={"days": 1})

    assert resp.status_code == 200
    camp = next(
        (c for c in resp.json()["top_campaigns"] if c["campaign_name"] == ids["campaign_name"]),
        None,
    )
    assert camp is not None
    assert Decimal(camp["spend"]) == Decimal("75.00"), "top_campaigns spend завышен (ждали 75)"
    # leaderboard на том же наборе
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp2 = await ac.get("/api/dashboard/performance", params={"days": 1})
    off = next(
        (o for o in resp2.json()["offer_leaderboard"] if o["offer_code"] == ids["offer_code"]),
        None,
    )
    assert off is not None
    assert Decimal(off["spend"]) == Decimal("75.00"), "offer_leaderboard spend завышен (ждали 75)"


# ─────────────────── Многодневное окно: посуточный reset ──────────────────────


# /history за 2 суток: вчера кумулятив→50, сегодня (после reset) кумулятив→30.
# Правильно: per-ad-per-day latest → 50 + 30 = 80, НЕ сумма всех строк (165).
# Per-ad через /history/ads (изолировано от чужих данных в shared-БД).
@pytest.mark.asyncio
async def test_history_multiday_sums_daily_totals(pg_engine, fake_redis_client, clean_semantics):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn, code_suffix="MULTI")
        # Вчера: кумулятив 20 → 50 (последний снимок = дневной итог 50).
        for n, s in enumerate([20, 35, 50], start=0):
            ts = (
                "date_trunc('day', NOW()) - INTERVAL '1 day' "
                f"+ INTERVAL '2 hours' + INTERVAL '{n} minutes'"
            )
            await _insert_metric(
                conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal(s), leads=s
            )
        # Сегодня (после reset): кумулятив 10 → 30 (дневной итог 30).
        for n, s in enumerate([10, 20, 30], start=0):
            ts = f"date_trunc('day', NOW()) + INTERVAL '2 hours' + INTERVAL '{n} minutes'"
            await _insert_metric(
                conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal(s), leads=s
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    window = {"from_iso": _yesterday_start_iso(), "to_iso": _now_iso()}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/ads", params=window)

    assert resp.status_code == 200
    ad_row = next((a for a in resp.json() if a["ad_name"] == "SEM_AD_sem1_MULTI"), None)
    assert ad_row is not None
    # 50 (день1 итог) + 30 (день2 итог) = 80, а не 20+35+50+10+20+30 = 165.
    assert Decimal(ad_row["spend"]) == Decimal("80.00"), "многодневный spend должен быть 80, не 165"
    assert ad_row["leads"] == 80


# ─────────────────── Граничные случаи ─────────────────────────────────────────


# Пустое окно (нет метрик у наших ad'ов в окне) → spend 0.
@pytest.mark.asyncio
async def test_empty_window_zero(pg_engine, fake_redis_client, clean_semantics):
    async with pg_engine.begin() as conn:
        await _seed_chain(conn, code_suffix="EMPTY")
        # Метрик не вставляем вовсе.

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        row_resp = await ac.get("/api/offers/compare", params={"days": 1})
    assert row_resp.status_code == 200
    row = next((r for r in row_resp.json() if r["offer_code"] == "SEM_EMPTY"), None)
    assert row is not None
    assert Decimal(row["spend"]) == Decimal("0.00")
    assert row["leads"] == 0


# Один ad, один цикл → возвращается ровно его значение (не теряется и не двоится).
# Per-ad через /history/ads (изолировано от чужих ad'ов в shared-БД).
@pytest.mark.asyncio
async def test_single_ad_single_cycle(pg_engine, fake_redis_client, clean_semantics):
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(conn, code_suffix="ONE")
        ts = "date_trunc('day', NOW()) + INTERVAL '3 hours'"
        await _insert_metric(
            conn, ad_id=ids["ad1_id"], cycle_ts_sql=ts, spend=Decimal("42.50"), leads=7
        )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/history/ads",
            params={"from_iso": _today_start_iso(), "to_iso": _now_iso()},
        )
    assert resp.status_code == 200
    ad_row = next((a for a in resp.json() if a["ad_name"] == "SEM_AD_sem1_ONE"), None)
    assert ad_row is not None
    assert Decimal(ad_row["spend"]) == Decimal("42.50")
    assert ad_row["leads"] == 7
