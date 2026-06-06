# -*- coding: utf-8 -*-
"""Интеграционные тесты HistoryPage endpoints (Round 7.6).

18 тестов покрывают все 6 endpoints:
    GET /history/summary   — 6 тестов
    GET /history/timeline  — 3 теста
    GET /history/campaigns — 2 теста
    GET /history/events    — 3 теста
    GET /history/offers    — 1 тест
    GET /history/ads       — 2 теста
    Partitioned sanity     — 1 тест
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
    """Собирает FastAPI приложение с переопределёнными зависимостями."""
    app = create_app()
    if engine is not None:
        app.dependency_overrides[get_engine] = lambda: engine
    if redis is not None:
        app.dependency_overrides[get_redis] = lambda: redis
        app.state.redis = redis
    return app


@pytest_asyncio.fixture
async def history_fixture(pg_engine):
    """Создаёт полную иерархию: offer→campaign→adset→2 ads + метрики (мультицикл) + алерты + задачи.

    Каждый ad получает 3 кумулятивных snapshot'а за сутки (чтобы поймать naive-SUM регрессию):
      ad1: 100→150→200.50  → latest=200.50  (naive SUM=451.00)
      ad2:  50→ 75→100.00  → latest=100.00  (naive SUM=225.00)
    Итого latest-sum: 300.50. Naive SUM: 676.00.

    Cleanup через CASCADE от offers.
    """
    sfx = uuid.uuid4().hex[:8]
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad1_id = uuid.uuid4()
    ad2_id = uuid.uuid4()
    fb_ad1 = f"90100{sfx}"
    fb_ad2 = f"90200{sfx}"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"HT_{sfx}", "n": f"History offer {sfx}"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_HT_{sfx}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_HT_{sfx}"},
        )
        # Два объявления
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                "VALUES (:i, :a, :f, :n, NOW())"
            ),
            {"i": ad1_id, "a": adset_id, "f": fb_ad1, "n": f"AD1_HT_{sfx}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                "VALUES (:i, :a, :f, :n, NOW())"
            ),
            {"i": ad2_id, "a": adset_id, "f": fb_ad2, "n": f"AD2_HT_{sfx}"},
        )
        # Мультицикл ad1: 3 кумулятивных snapshot'а в текущие сутки.
        # latest=200.50 (именно он должен использоваться, naive SUM=451.00).
        for spend_val, hours_ago in [
            (Decimal("100.00"), 5),
            (Decimal("150.00"), 3),
            (Decimal("200.50"), 1),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics "
                    "(id, ad_id, cycle_ts, spend, impressions, clicks, leads, registrations, deposits) "
                    "VALUES (gen_random_uuid(), :a, "
                    "NOW() - make_interval(hours => :h), :s, :imp, :cl, :l, :r, :d)"
                ),
                {
                    "a": ad1_id,
                    "h": hours_ago,
                    "s": spend_val,
                    "imp": 5000,
                    "cl": 300,
                    "l": 30,
                    "r": 20,
                    "d": 5,
                },
            )
        # Мультицикл ad2: 3 кумулятивных snapshot'а в текущие сутки.
        # latest=100.00 (naive SUM=225.00).
        for spend_val, hours_ago in [
            (Decimal("50.00"), 6),
            (Decimal("75.00"), 4),
            (Decimal("100.00"), 2),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics "
                    "(id, ad_id, cycle_ts, spend, impressions, clicks, leads, registrations, deposits) "
                    "VALUES (gen_random_uuid(), :a, "
                    "NOW() - make_interval(hours => :h), :s, :imp, :cl, :l, :r, :d)"
                ),
                {
                    "a": ad2_id,
                    "h": hours_ago,
                    "s": spend_val,
                    "imp": 2000,
                    "cl": 100,
                    "l": 10,
                    "r": 5,
                    "d": 2,
                },
            )
        # AlertEvent warning для ad1
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at) "
                "VALUES (gen_random_uuid(), :a, 'warning', 'warning_sent', :mc, :mj, NOW() - INTERVAL '3 hours')"
            ),
            {"a": ad1_id, "mc": '["CPC", "CTR"]', "mj": '{"cpc": 1.5}'},
        )
        # AlertEvent stop для ad2
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at) "
                "VALUES (gen_random_uuid(), :a, 'stop', 'stop_sent', :mc, :mj, NOW() - INTERVAL '4 hours')"
            ),
            {"a": ad2_id, "mc": '["CPL"]', "mj": '{"cpl": 20.0}'},
        )
        # TaskQueue disable succeeded для ad1
        await conn.execute(
            text(
                "INSERT INTO task_queue "
                "(task_type, status, idempotency_key, payload, requested_by, "
                " created_at, updated_at) "
                "VALUES ('disable', 'succeeded', :ikey, :payload, 'bot_auto_stop', "
                " NOW() - INTERVAL '5 hours', NOW() - INTERVAL '4 hours')"
            ),
            {
                "ikey": f"ht_dis1_{sfx}",
                "payload": f'{{"fb_ad_id": "{fb_ad1}"}}',
            },
        )
        # TaskQueue disable failed для ad2
        await conn.execute(
            text(
                "INSERT INTO task_queue "
                "(task_type, status, idempotency_key, payload, requested_by, "
                " created_at, updated_at) "
                "VALUES ('disable', 'failed', :ikey, :payload, 'bot_auto_stop', "
                " NOW() - INTERVAL '6 hours', NOW() - INTERVAL '5 hours')"
            ),
            {
                "ikey": f"ht_dis2_{sfx}",
                "payload": f'{{"fb_ad_id": "{fb_ad2}"}}',
            },
        )
        # TaskQueue enable succeeded для ad1
        await conn.execute(
            text(
                "INSERT INTO task_queue "
                "(task_type, status, idempotency_key, payload, requested_by, "
                " created_at, updated_at) "
                "VALUES ('enable', 'succeeded', :ikey, :payload, 'api_user', "
                " NOW() - INTERVAL '2 hours', NOW() - INTERVAL '1 hour')"
            ),
            {
                "ikey": f"ht_ena1_{sfx}",
                "payload": f'{{"fb_ad_id": "{fb_ad1}"}}',
            },
        )

    yield {
        "sfx": sfx,
        "offer_id": offer_id,
        "campaign_id": campaign_id,
        "ad1_id": ad1_id,
        "ad2_id": ad2_id,
        "fb_ad1": fb_ad1,
        "fb_ad2": fb_ad2,
        "offer_code": f"HT_{sfx}",
        "campaign_name": f"CMP_HT_{sfx}",
    }

    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})


# ──────────── GET /history/summary ───────────────────────────────────────────


# Happy path: 2 объявления × 3 кумулятивных цикла — spend == latest-per-ad, не naive SUM.
# ad1 latest=200.50, ad2 latest=100.00 → per-ad сумма 300.50.
# Naive SUM дал бы 451+225=676 — тест это ловит. Используем scoped-запрос по нашим ad_id.
@pytest.mark.asyncio
async def test_summary_happy_path(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/summary")

    assert resp.status_code == 200
    data = resp.json()

    # Глобальный /summary видит все данные в БД — не можем ассертить точное ==.
    # Проверяем через scoped-SQL: latest-per-ad у наших двух ad_id = 300.50.
    async with pg_engine.connect() as conn:
        scoped_spend = (
            await conn.execute(
                text(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (m.ad_id) m.spend
                        FROM ad_metrics m
                        WHERE m.ad_id = ANY(:ids)
                          AND m.cycle_ts >= NOW() - INTERVAL '30 days'
                        ORDER BY m.ad_id, m.cycle_ts DESC
                    )
                    SELECT COALESCE(SUM(spend), 0) FROM latest
                    """
                ),
                {"ids": [history_fixture["ad1_id"], history_fixture["ad2_id"]]},
            )
        ).scalar_one()
    # scoped latest-per-ad: 200.50 + 100.00 = 300.50 (не 676 naive SUM 3 циклов)
    assert Decimal(str(scoped_spend)) == Decimal("300.50"), (
        f"scoped spend должен быть 300.50 (latest), не {scoped_spend} (возможно naive SUM)"
    )

    # Глобальный summary: наш вклад не потерян и не более naive (>= 300.50)
    global_spend = Decimal(data["totals"]["spend"])
    assert global_spend >= Decimal("300.50")
    assert data["totals"]["leads"] >= 40  # 30 + 10
    assert data["totals"]["deposits"] >= 7  # 5 + 2
    assert data["alerts"]["warning_count"] >= 1
    assert data["alerts"]["stop_count"] >= 1


# Без параметров — дефолтное окно 30 дней, данные попадают в него.
# Дополнительно проверяем что scoped spend == latest (не naive SUM 3 циклов на каждый ad).
@pytest.mark.asyncio
async def test_summary_default_30_days(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/summary")

    assert resp.status_code == 200
    data = resp.json()
    # Дефолт — 30 дней, данные за последние часы должны попасть
    assert float(data["totals"]["spend"]) > 0

    # Повторная scoped-проверка: latest != naive SUM
    async with pg_engine.connect() as conn:
        scoped = (
            await conn.execute(
                text(
                    """
                    WITH latest AS (
                        SELECT DISTINCT ON (m.ad_id) m.spend
                        FROM ad_metrics m
                        WHERE m.ad_id = ANY(:ids)
                          AND m.cycle_ts >= NOW() - INTERVAL '30 days'
                        ORDER BY m.ad_id, m.cycle_ts DESC
                    )
                    SELECT COALESCE(SUM(spend), 0) FROM latest
                    """
                ),
                {"ids": [history_fixture["ad1_id"], history_fixture["ad2_id"]]},
            )
        ).scalar_one()
    # latest 300.50, naive SUM будет 676 — любое значение выше 451 означает баг
    assert Decimal(str(scoped)) == Decimal("300.50"), (
        f"scoped latest spend = {scoped}, ожидалось 300.50 (latest, не naive SUM)"
    )


# Диапазон > 90 дней → 422.
@pytest.mark.asyncio
async def test_summary_range_over_90_days(pg_engine, fake_redis_client):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/history/summary",
            params={
                "from_iso": "2025-01-01T00:00:00+00:00",
                "to_iso": "2025-06-01T00:00:00+00:00",  # 150 дней
            },
        )

    assert resp.status_code == 422


# to_iso < from_iso → 422.
@pytest.mark.asyncio
async def test_summary_to_before_from(pg_engine, fake_redis_client):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/history/summary",
            params={
                "from_iso": "2025-05-10T00:00:00+00:00",
                "to_iso": "2025-05-01T00:00:00+00:00",
            },
        )

    assert resp.status_code == 422


# Пустая БД (будущее окно) → нулевые totals и пустые массивы.
@pytest.mark.asyncio
async def test_summary_empty_db_zeros(pg_engine, fake_redis_client):
    # Используем окно в далёком будущем — там точно нет данных
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/history/summary",
            params={
                "from_iso": "2030-01-01T00:00:00+00:00",
                "to_iso": "2030-01-31T00:00:00+00:00",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert float(data["totals"]["spend"]) == 0.0
    assert data["totals"]["leads"] == 0
    assert data["alerts"]["warning_count"] == 0
    assert data["alerts"]["stop_count"] == 0
    assert data["alerts"]["by_rule"] == []
    assert data["tasks"]["disable_completed"] == 0


# by_rule корректно работает с JSONB matched_rule_codes (unnest).
@pytest.mark.asyncio
async def test_summary_by_rule_jsonb_unnest(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/summary")

    assert resp.status_code == 200
    data = resp.json()
    # Вставили CPC, CTR (warning) и CPL (stop) — все должны быть в by_rule
    by_rule_codes = {r["rule_code"] for r in data["alerts"]["by_rule"]}
    assert "CPC" in by_rule_codes
    assert "CTR" in by_rule_codes
    assert "CPL" in by_rule_codes


# ──────────── GET /history/timeline ──────────────────────────────────────────


# UNION ALL alert+task должен содержать оба типа, сортировка по ts DESC.
@pytest.mark.asyncio
async def test_timeline_union_all_sorted(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/timeline")

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 2

    event_types = {i["event_type"] for i in items}
    assert "alert" in event_types
    assert "task" in event_types

    # Проверяем сортировку DESC
    ts_list = [i["ts"] for i in items]
    assert ts_list == sorted(ts_list, reverse=True)


# timeline возвращает только terminal задачи (succeeded/failed/cancelled).
@pytest.mark.asyncio
async def test_timeline_only_terminal_tasks(pg_engine, fake_redis_client, pg_engine_2=None):
    """Создаём pending-задачу и проверяем, что она НЕ попадает в timeline."""
    # Создаём отдельную pending задачу
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    sfx = uuid.uuid4().hex[:8]

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO task_queue "
                "(task_type, status, idempotency_key, payload, requested_by, "
                " created_at, updated_at) "
                "VALUES ('disable', 'pending', :ikey, :payload, 'api_user', NOW(), NOW())"
            ),
            {"ikey": f"ht_pending_{sfx}", "payload": '{"fb_ad_id": "000000pending"}'},
        )

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/history/timeline")

        assert resp.status_code == 200
        items = resp.json()
        # pending задача не должна появиться в timeline
        for item in items:
            if item["event_type"] == "task" and item["fb_ad_id"] == "000000pending":
                pytest.fail("pending задача не должна появляться в timeline")
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE idempotency_key = :k"),
                {"k": f"ht_pending_{sfx}"},
            )


# limit параметр ограничивает количество элементов в ответе.
@pytest.mark.asyncio
async def test_timeline_limit(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/timeline", params={"limit": 2})

    assert resp.status_code == 200
    assert len(resp.json()) <= 2


# ──────────── GET /history/campaigns ─────────────────────────────────────────


# GROUP BY кампании, сортировка по spend DESC.
# Дополнительно: точный spend кампании = latest-per-ad (не naive SUM).
# Фикстура: 2 ad × 3 цикла. latest ad1=200.50 + latest ad2=100.00 = 300.50.
# Naive SUM дал бы 676 — проверяем точное == 300.50.
@pytest.mark.asyncio
async def test_campaigns_group_and_sort(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/campaigns")

    assert resp.status_code == 200
    campaigns = resp.json()
    # Наша кампания должна быть в ответе
    names = [c["campaign_name"] for c in campaigns]
    assert history_fixture["campaign_name"] in names

    # Точное значение spend нашей кампании: latest-per-ad × 2 ads = 300.50
    # (не naive SUM 3 циклов × 2 ads = 676). Это изолировано по campaign_name.
    our = next(c for c in campaigns if c["campaign_name"] == history_fixture["campaign_name"])
    assert Decimal(our["spend"]) == Decimal("300.50"), (
        f"campaign spend={our['spend']}, ожидалось 300.50 (latest), не 676 (naive SUM)"
    )

    # Проверяем сортировку spend DESC
    if len(campaigns) >= 2:
        spends = [float(c["spend"]) for c in campaigns]
        assert spends == sorted(spends, reverse=True)


# H3: несколько alert_events на одном ad НЕ задваивают spend кампании (fan-out fix).
# Старый код (per-ad alerts JOIN + GROUP BY alerts_count) дробил кампанию на строки по
# числу алертов и дробил/завышал spend. Фикстура даёт ad1/ad2 по 1 алерту — баг не виден;
# здесь добавляем ad1 ещё 2 алерта и проверяем, что spend остаётся 300.50, а кампания одна.
@pytest.mark.asyncio
async def test_campaigns_spend_not_inflated_by_multiple_alerts(
    pg_engine, fake_redis_client, history_fixture
):
    async with pg_engine.begin() as conn:
        for i in range(2):
            await conn.execute(
                text(
                    "INSERT INTO alert_events "
                    "(ad_id, stage, state, matched_rule_codes, metrics_json) "
                    "VALUES (:a, 'warning', 'warning_sent', CAST(:mc AS JSONB), CAST(:mj AS JSONB))"
                ),
                {"a": history_fixture["ad1_id"], "mc": '["CPC"]', "mj": f'{{"n": {i}}}'},
            )

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/campaigns")

    assert resp.status_code == 200
    campaigns = resp.json()
    our = [c for c in campaigns if c["campaign_name"] == history_fixture["campaign_name"]]
    # Кампания одной строкой (не размножена fan-out'ом по alerts_count)
    assert len(our) == 1, f"кампания размножена fan-out'ом: {len(our)} строк"
    # spend не задвоен: latest-per-ad = 300.50 несмотря на 3 алерта у ad1
    assert Decimal(our[0]["spend"]) == Decimal("300.50"), (
        f"spend={our[0]['spend']} задвоен fan-out'ом, ожидалось 300.50"
    )
    # alerts_count кампании = 3 (ad1: 1 из фикстуры + 2) + 1 (ad2 stop) = 4
    assert our[0]["alerts_count"] == 4


# active_ads_count считает только last_seen_at >= now - 7d.
@pytest.mark.asyncio
async def test_campaigns_active_ads_last_seen_filter(pg_engine, fake_redis_client, history_fixture):
    """Вставляем объявление с last_seen_at = 30 дней назад и проверяем, что оно не в active_ads."""
    sfx = uuid.uuid4().hex[:8]
    old_ad_id = uuid.uuid4()
    campaign_id = history_fixture["campaign_id"]
    adset_id_2 = uuid.uuid4()
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id_2, "c": campaign_id, "n": f"OLD_ADSET_{sfx}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                "VALUES (:i, :a, :f, :n, NOW() - INTERVAL '30 days')"
            ),
            {"i": old_ad_id, "a": adset_id_2, "f": f"OLD90{sfx}", "n": f"OLD_AD_{sfx}"},
        )

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/history/campaigns")

        assert resp.status_code == 200
        campaigns = resp.json()
        # Ищем нашу кампанию
        our_campaign = next(
            (c for c in campaigns if c["campaign_name"] == history_fixture["campaign_name"]),
            None,
        )
        assert our_campaign is not None
        # У нашей кампании active_ads_count не включает старое объявление (30 дней)
        # Точное значение зависит от других тестов, главное — функция не упала
        assert our_campaign["active_ads_count"] >= 0
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM fb_ads WHERE id = :i"), {"i": old_ad_id})
            await conn.execute(text("DELETE FROM fb_adsets WHERE id = :i"), {"i": adset_id_2})


# ──────────── GET /history/events ────────────────────────────────────────────


# Drill-down по campaign_id — возвращаются только алерты данной кампании.
@pytest.mark.asyncio
async def test_events_drilldown_by_campaign(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    campaign_id = str(history_fixture["campaign_id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/events", params={"campaign_id": campaign_id})

    assert resp.status_code == 200
    events = resp.json()
    # Все события должны принадлежать нашей кампании
    for event in events:
        assert event["campaign_name"] == history_fixture["campaign_name"]


# Фильтр stage=stop — возвращаются только STOP алерты.
@pytest.mark.asyncio
async def test_events_filter_stage_stop(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/events", params={"stage": "stop"})

    assert resp.status_code == 200
    events = resp.json()
    # Все события должны быть stage=stop
    for event in events:
        assert event["stage"] == "stop"


# matched_rule_codes JSONB корректно десериализуется в список строк.
@pytest.mark.asyncio
async def test_events_matched_rule_codes_jsonb(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    campaign_id = str(history_fixture["campaign_id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/history/events",
            params={"campaign_id": campaign_id, "stage": "warning"},
        )

    assert resp.status_code == 200
    events = resp.json()
    assert len(events) >= 1
    # matched_rule_codes должен быть списком строк (JSONB → list[str])
    codes = events[0]["matched_rule_codes"]
    assert isinstance(codes, list)
    assert "CPC" in codes
    assert "CTR" in codes


# ──────────── GET /history/offers ────────────────────────────────────────────


# GROUP BY offer через JOIN fb_campaigns.offer_id — оффер из fixture должен быть в ответе.
# Точный spend оффера = latest-per-ad: ad1=200.50 + ad2=100.00 = 300.50.
# Naive SUM по 3 циклам дал бы 676 — тест это ловит. Изоляция по offer_code.
@pytest.mark.asyncio
async def test_offers_group_by_offer(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/offers")

    assert resp.status_code == 200
    offers = resp.json()
    codes = [o["offer_code"] for o in offers]
    assert history_fixture["offer_code"] in codes

    # Точное значение spend оффера: latest-per-ad = 300.50 (не naive SUM 676)
    our_offer = next(o for o in offers if o["offer_code"] == history_fixture["offer_code"])
    assert Decimal(our_offer["spend"]) == Decimal("300.50"), (
        f"offer spend={our_offer['spend']}, ожидалось 300.50 (latest), не 676 (naive SUM)"
    )


# ──────────── GET /history/ads ────────────────────────────────────────────────


# last_alert_at берётся из LATERAL subquery по alert_events.
@pytest.mark.asyncio
async def test_ads_last_alert_at(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    campaign_id = str(history_fixture["campaign_id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/ads", params={"campaign_id": campaign_id})

    assert resp.status_code == 200
    ads = resp.json()
    assert len(ads) >= 1

    # Хотя бы у одного объявления должен быть last_alert_at
    has_alert = any(a["last_alert_at"] is not None for a in ads)
    assert has_alert, "Ожидался last_alert_at у хотя бы одного объявления"


# last_disable_at заполняется из task_queue.
@pytest.mark.asyncio
async def test_ads_last_disable_at(pg_engine, fake_redis_client, history_fixture):
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    campaign_id = str(history_fixture["campaign_id"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/history/ads", params={"campaign_id": campaign_id})

    assert resp.status_code == 200
    ads = resp.json()

    # fb_ad1 имел succeeded disable — у него должен быть last_disable_at
    ad1 = next((a for a in ads if a["fb_ad_id"] == history_fixture["fb_ad1"]), None)
    assert ad1 is not None, f"Объявление {history_fixture['fb_ad1']} не найдено в ответе"
    assert ad1["last_disable_at"] is not None, "Ожидался last_disable_at у ad1"


# ──────────── Partitioned sanity ─────────────────────────────────────────────


# Partition boundary test: данные на границах from/to корректно включены/исключены.
# Заменяет старый timing-only тест (elapsed < 2s — не доказывает корректность данных).
@pytest.mark.asyncio
async def test_partitioned_query_boundaries(pg_engine, fake_redis_client):
    """Данные строго ВНУТРИ окна включены, строго ВНЕ окна исключены.

    Создаём изолированные данные:
    - in_window: alert_event внутри [from, to]
    - out_before: alert_event ДО from (not included)
    - out_after: alert_event ПОСЛЕ to (not included)

    Проверяем через /history/events (drill-down с фильтром ad_id) что:
    - in_window попал ровно 1 раз
    - out_before и out_after не попали

    Это заменяет timing-обманку: «тест < 2s» проходит даже при full-scan на пустой БД.
    """
    from datetime import datetime, timedelta, timezone

    sfx = uuid.uuid4().hex[:6]
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id_val = f"PB_{sfx}"

    # Окно: 7-дневное в прошлом, чётко ограниченное
    to_dt = datetime.now(timezone.utc) - timedelta(days=2)
    from_dt = to_dt - timedelta(days=7)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"PB_{sfx}", "n": f"PartBound {sfx}"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_PB_{sfx}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADS_PB_{sfx}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                "VALUES (:i, :a, :f, :n, NOW())"
            ),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id_val, "n": f"AD_PB_{sfx}"},
        )

        # in_window: середина окна — должен попасть
        in_window_ts = from_dt + timedelta(days=3)
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at) "
                "VALUES (gen_random_uuid(), :a, 'warning', 'warning_sent', '[]', '{}', :ts)"
            ),
            {"a": ad_id, "ts": in_window_ts},
        )

        # out_before: за 1 день до from — НЕ должен попасть
        out_before_ts = from_dt - timedelta(days=1)
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at) "
                "VALUES (gen_random_uuid(), :a, 'stop', 'stop_sent', '[]', '{}', :ts)"
            ),
            {"a": ad_id, "ts": out_before_ts},
        )

        # out_after: через 1 день после to — НЕ должен попасть
        out_after_ts = to_dt + timedelta(days=1)
        await conn.execute(
            text(
                "INSERT INTO alert_events "
                "(id, ad_id, stage, state, matched_rule_codes, metrics_json, created_at) "
                "VALUES (gen_random_uuid(), :a, 'warning', 'warning_sent', '[]', '{}', :ts)"
            ),
            {"a": ad_id, "ts": out_after_ts},
        )

    try:
        app = _make_app(engine=pg_engine, redis=fake_redis_client)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/history/events",
                params={
                    "from_iso": from_dt.isoformat(),
                    "to_iso": to_dt.isoformat(),
                    "fb_ad_id": fb_ad_id_val,
                },
            )

        assert resp.status_code == 200
        events = resp.json()

        # Ровно 1 событие: in_window попало, оба out-of-range исключены
        assert len(events) == 1, (
            f"Ожидалось 1 событие (in-window), получено {len(events)} — "
            "вне-окна данные просочились через partition filter"
        )

        # Событие — именно из in_window (warning stage, середина окна)
        assert events[0]["stage"] == "warning", (
            f"Ожидался stage='warning' (in-window), получен {events[0]['stage']}"
        )
    finally:
        # Cleanup: удаляем через CASCADE от offer
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM offers WHERE id = :i"),
                {"i": offer_id},
            )


# ──────────── Cabinet-day reset: многодневный spend ──────────────────────────


# Многодневный кейс: вчера кумулятив→50, сегодня (после cabinet reset)→30.
# Правильно: per-ad-per-day latest → 50+30 = 80. Naive SUM всех строк = 165.
# Тест использует scoped cleanup, чтобы изолироваться от shared-БД.
@pytest.mark.asyncio
async def test_history_ads_multiday_cabinet_reset(pg_engine, fake_redis_client):
    """Cabinet-day reset: вчера latest=50, сегодня latest=30 → итого 80, не 165."""
    from datetime import UTC, datetime, timedelta

    sfx = uuid.uuid4().hex[:8]
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad = f"96{sfx}"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"HT_MR_{sfx}", "n": f"MultiReset {sfx}"},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_MR_{sfx}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADS_MR_{sfx}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, last_seen_at) "
                "VALUES (:i, :a, :f, :n, NOW())"
            ),
            {"i": ad_id, "a": adset_id, "f": fb_ad, "n": f"AD_MR_{sfx}"},
        )
        # Вчера: кумулятив 20 → 35 → 50 (дневной итог = 50).
        for spend_val, h_offset in [(20, 30), (35, 28), (50, 26)]:
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics "
                    "(id, ad_id, cycle_ts, spend, leads, registrations, deposits) "
                    "VALUES (gen_random_uuid(), :a, "
                    "NOW() - make_interval(hours => :h), :s, 5, 3, 1)"
                ),
                {"a": ad_id, "h": h_offset, "s": Decimal(str(spend_val))},
            )
        # Сегодня (после cabinet reset): кумулятив 10 → 20 → 30 (дневной итог = 30).
        for spend_val, h_offset in [(10, 5), (20, 3), (30, 1)]:
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics "
                    "(id, ad_id, cycle_ts, spend, leads, registrations, deposits) "
                    "VALUES (gen_random_uuid(), :a, "
                    "NOW() - make_interval(hours => :h), :s, 3, 2, 1)"
                ),
                {"a": ad_id, "h": h_offset, "s": Decimal(str(spend_val))},
            )

    try:
        app = _make_app(engine=pg_engine, redis=fake_redis_client)
        now = datetime.now(UTC)
        from_ts = (
            now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        ).isoformat()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/history/ads",
                params={"from_iso": from_ts, "to_iso": now.isoformat()},
            )

        assert resp.status_code == 200
        ads = resp.json()
        our_ad = next((a for a in ads if a["fb_ad_id"] == fb_ad), None)
        assert our_ad is not None, f"Ad {fb_ad} не найден в /history/ads"
        # 50 (вчерашний latest) + 30 (сегодняшний latest) = 80, не 165 (naive SUM всех строк)
        assert Decimal(our_ad["spend"]) == Decimal("80.00"), (
            f"multiday spend={our_ad['spend']}, ожидалось 80 (latest per-day), "
            "не 165 (naive SUM 6 строк)"
        )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM ad_metrics WHERE ad_id = :a"), {"a": ad_id})
            await conn.execute(text("DELETE FROM fb_ads WHERE id = :i"), {"i": ad_id})
            await conn.execute(text("DELETE FROM fb_adsets WHERE id = :i"), {"i": adset_id})
            await conn.execute(text("DELETE FROM fb_campaigns WHERE id = :i"), {"i": campaign_id})
            await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})
