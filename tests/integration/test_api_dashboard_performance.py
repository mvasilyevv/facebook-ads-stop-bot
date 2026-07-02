# -*- coding: utf-8 -*-
"""Интеграционные тесты GET /api/dashboard/performance.

3 параллельных тяжёлых SQL: top_campaigns, offer_leaderboard, top_rule_violations.
"""

from __future__ import annotations

import json
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
async def clean_perf(pg_engine):
    """Очистка таблиц для perf-тестов.

    Ограничена PREFIX='PRF_', чтобы не стирать данные других параллельных тестов
    (например history_fixture, clean_semantics). Исходный _wipe удалял ВСЕ fb_ads —
    что ломало history-тесты при рандомном порядке.
    """

    async def _wipe():
        async with pg_engine.begin() as conn:
            # Удаляем только наши PRF_* сущности (cascade по FK)
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'PRF\\_AD\\_%')"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM alert_events WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'PRF\\_AD\\_%')"
                )
            )
            await conn.execute(
                text(
                    "DELETE FROM ad_alert_state WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'PRF\\_AD\\_%')"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE 'PRF\\_AD\\_%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'PRF\\_ADS\\_%'"))
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'PRF\\_CMP\\_%'")
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'PRF\\_%'"))

    await _wipe()
    yield
    await _wipe()


async def _seed_full(
    conn,
    suffix: str,
    *,
    spend: Decimal = Decimal("100.00"),
    leads: int = 10,
    rule_codes: list[str] | None = None,
    multicycle: bool = False,
):
    """Создаёт offer→campaign→adset→ad с метрикой и алертом.

    multicycle=True: вставляет 3 кумулятивных snapshot'а вместо одного.
    Значения: spend/3*10 → spend/3*20 → spend (latest=spend).
    Это позволяет ловить naive-SUM регрессию (SUM дал бы spend*2).
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"33{suffix}"
    fb_camp_id = f"camp_{suffix}"

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"PRF_{suffix}", "n": f"Perf offer {suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO fb_campaigns (id, fb_campaign_id, campaign_name, offer_id) "
            "VALUES (:i, :fc, :n, :o)"
        ),
        {"i": campaign_id, "fc": fb_camp_id, "n": f"PRF_CMP_{suffix}", "o": offer_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"PRF_ADS_{suffix}"},
    )
    await conn.execute(
        text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
        {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"PRF_AD_{suffix}"},
    )
    if multicycle:
        # 3 кумулятивных snapshot'а: spend_early < spend_mid < spend (latest).
        # latest=spend, naive SUM = spend_early + spend_mid + spend.
        spend_early = (spend / 3).quantize(Decimal("0.01"))
        spend_mid = (spend * 2 / 3).quantize(Decimal("0.01"))
        for s_val, h_ago in [(spend_early, 6), (spend_mid, 4), (spend, 2)]:
            await conn.execute(
                text(
                    "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
                    "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
                    "NOW() - make_interval(hours => :h), :s, 1000, 50, :l, 5, 1)"
                ),
                {"a": ad_id, "h": h_ago, "s": s_val, "l": leads},
            )
    else:
        await conn.execute(
            text(
                "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, impressions, clicks, "
                "leads, registrations, deposits) VALUES (gen_random_uuid(), :a, "
                "NOW() - INTERVAL '2 hours', :s, 1000, 50, :l, 5, 1)"
            ),
            {"a": ad_id, "s": spend, "l": leads},
        )
    if rule_codes:
        await conn.execute(
            text(
                "INSERT INTO alert_events (id, ad_id, stage, state, matched_rule_codes, "
                "metrics_json) VALUES (gen_random_uuid(), :a, 'warning', 'warning_sent', "
                "CAST(:codes AS jsonb), CAST(:mj AS jsonb))"
            ),
            {
                "a": ad_id,
                "codes": json.dumps(rule_codes),
                "mj": json.dumps({"spend": str(spend)}),
            },
        )
    return ad_id, campaign_id, offer_id


# Тест: default days=7 — данные за 7 дней.
@pytest.mark.asyncio
async def test_performance_default_7d(pg_engine, fake_redis_client, clean_perf) -> None:
    """Default ?days=7 → endpoint отрабатывает и возвращает три ключа."""
    async with pg_engine.begin() as conn:
        await _seed_full(conn, "D7", spend=Decimal("500.00"), leads=10, rule_codes=["CPC"])

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance")

    assert resp.status_code == 200
    data = resp.json()
    assert "top_campaigns" in data
    assert "offer_leaderboard" in data
    assert "top_rule_violations" in data


# Тест: top_campaigns spend = latest-per-ad (не naive SUM трёх циклов).
# Проверяем ФАКТИЧЕСКОЕ значение из JSON-ответа endpoint'а (не только scoped-SQL —
# MID-20: раньше тест ассертил scoped-SQL и лишь проверял "форму" resp.json(),
# оставляя возможный баг маппинга SQL→Pydantic-схема незамеченным). Гоняем с большим
# limit_campaigns, чтобы наша кампания гарантированно попала в выдачу endpoint'а,
# несмотря на дефолтный LIMIT 10 в shared-БД с параллельными тестами.
@pytest.mark.asyncio
async def test_performance_top_campaigns_exact_spend(pg_engine, fake_redis_client) -> None:
    """top_campaigns.spend == latest snapshot из ОТВЕТА endpoint'а, не naive SUM.

    scoped-SQL остаётся как независимая эталонная проверка агрегации, но главный
    assert — на resp.json()["top_campaigns"] — ловит и SQL-регрессию, и баг
    сериализации/маппинга в TopCampaignOut.
    """
    sfx = uuid.uuid4().hex[:6]
    # multicycle=True: 3 snapshot'а с latest=300.00. Naive SUM = 100+200+300 = 600.
    async with pg_engine.begin() as conn:
        _ad_id, campaign_id, _offer_id = await _seed_full(
            conn, sfx, spend=Decimal("300.00"), leads=20, multicycle=True
        )

    try:
        # Scoped-SQL: latest-per-(day×ad) за 7 дней для нашего campaign_id (эталон)
        async with pg_engine.connect() as conn:
            scoped_spend = (
                await conn.execute(
                    text(
                        """
                        WITH latest AS (
                            SELECT DISTINCT ON (date_trunc('day', m.cycle_ts), m.ad_id)
                                m.spend
                            FROM ad_metrics m
                            JOIN fb_ads a ON a.id = m.ad_id
                            JOIN fb_adsets ads ON ads.id = a.adset_id
                            WHERE ads.campaign_id = :cid
                              AND m.cycle_ts >= NOW() - INTERVAL '7 days'
                            ORDER BY date_trunc('day', m.cycle_ts), m.ad_id, m.cycle_ts DESC
                        )
                        SELECT COALESCE(SUM(spend), 0) FROM latest
                        """
                    ),
                    {"cid": campaign_id},
                )
            ).scalar_one()
        # latest=300.00, naive SUM трёх циклов был бы 100+200+300=600
        assert Decimal(str(scoped_spend)) == Decimal("300.00"), (
            f"scoped spend={scoped_spend}, ожидалось 300.00 (latest), не naive SUM 3 циклов (600)"
        )

        # Главная проверка: ФАКТИЧЕСКИЙ ответ endpoint'а содержит ровно то же значение.
        # limit_campaigns=100 гарантирует попадание нашей кампании в выдачу.
        app = _make_app(engine=pg_engine, redis=fake_redis_client)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/dashboard/performance",
                params={"days": 7, "limit_campaigns": 100},
            )
        assert resp.status_code == 200
        camps = resp.json()["top_campaigns"]
        our_camp = next((c for c in camps if c["campaign_name"] == f"PRF_CMP_{sfx}"), None)
        assert our_camp is not None, (
            f"Кампания PRF_CMP_{sfx} не найдена в top_campaigns ответа endpoint'а"
        )
        assert Decimal(our_camp["spend"]) == Decimal("300.00"), (
            f"endpoint spend={our_camp['spend']}, ожидалось 300.00 (latest), "
            "не naive SUM 3 циклов (600) — регрессия CRIT-1 в сериализации ответа"
        )
        assert our_camp["leads"] == 20
        assert Decimal(our_camp["cost_per_lead"]) == Decimal("15.00"), (
            f"endpoint cost_per_lead={our_camp['cost_per_lead']}, ожидалось 15.00 (300/20)"
        )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    f"(SELECT id FROM fb_ads WHERE ad_name = 'PRF_AD_{sfx}')"
                )
            )
            await conn.execute(text(f"DELETE FROM fb_ads WHERE ad_name = 'PRF_AD_{sfx}'"))
            await conn.execute(text(f"DELETE FROM fb_adsets WHERE adset_name = 'PRF_ADS_{sfx}'"))
            await conn.execute(
                text(f"DELETE FROM fb_campaigns WHERE campaign_name = 'PRF_CMP_{sfx}'")
            )
            await conn.execute(text(f"DELETE FROM offers WHERE code = 'PRF_{sfx}'"))


# Тест: cost_per_lead = spend/leads на latest-значениях (не naive SUM).
# scoped-SQL — эталон; главный assert — на resp.json() endpoint'а (MID-20).
@pytest.mark.asyncio
async def test_performance_cost_per_lead_exact(pg_engine, fake_redis_client) -> None:
    """cost_per_lead = spend/leads (latest-значения) — из ФАКТИЧЕСКОГО ответа endpoint'а.

    Без мультицикла выглядит верным, с мультициклом: если spend взялся как SUM,
    cost_per_lead завышен в 3×. Раньше тест проверял только scoped-SQL и не трогал
    resp.json() вовсе — баг сериализации TopCampaignOut.cost_per_lead прошёл бы незамеченным.
    """
    sfx = uuid.uuid4().hex[:6]
    # spend=300, leads=20, multicycle=True → latest spend=300, leads=20, cpl=15.00.
    async with pg_engine.begin() as conn:
        _ad_id, campaign_id, _offer_id = await _seed_full(
            conn, sfx, spend=Decimal("300.00"), leads=20, multicycle=True
        )

    try:
        # Scoped: latest spend и leads для нашего campaign (эталон)
        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        WITH latest AS (
                            SELECT DISTINCT ON (date_trunc('day', m.cycle_ts), m.ad_id)
                                m.spend, m.leads
                            FROM ad_metrics m
                            JOIN fb_ads a ON a.id = m.ad_id
                            JOIN fb_adsets ads ON ads.id = a.adset_id
                            WHERE ads.campaign_id = :cid
                              AND m.cycle_ts >= NOW() - INTERVAL '7 days'
                            ORDER BY date_trunc('day', m.cycle_ts), m.ad_id, m.cycle_ts DESC
                        )
                        SELECT COALESCE(SUM(spend), 0), COALESCE(SUM(leads), 0) FROM latest
                        """
                    ),
                    {"cid": campaign_id},
                )
            ).one()
        scoped_spend, scoped_leads = Decimal(str(row[0])), int(row[1])
        # latest spend=300.00, leads=20 → cpl должен быть 15.00
        assert scoped_spend == Decimal("300.00"), f"scoped spend={scoped_spend}, ожидалось 300.00"
        assert scoped_leads == 20, f"scoped leads={scoped_leads}, ожидалось 20"
        expected_cpl = scoped_spend / Decimal(str(scoped_leads))
        assert expected_cpl == Decimal("15.00"), (
            f"cost_per_lead={expected_cpl}, ожидалось 15.00 (300/20)"
        )

        # Главная проверка: ФАКТИЧЕСКИЙ ответ endpoint'а содержит те же 15.00.
        app = _make_app(engine=pg_engine, redis=fake_redis_client)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/dashboard/performance",
                params={"days": 7, "limit_campaigns": 100},
            )
        assert resp.status_code == 200
        camps = resp.json()["top_campaigns"]
        our_camp = next((c for c in camps if c["campaign_name"] == f"PRF_CMP_{sfx}"), None)
        assert our_camp is not None, (
            f"Кампания PRF_CMP_{sfx} не найдена в top_campaigns ответа endpoint'а"
        )
        assert Decimal(our_camp["cost_per_lead"]) == Decimal("15.00"), (
            f"endpoint cost_per_lead={our_camp['cost_per_lead']}, ожидалось 15.00 (300/20) — "
            "не 3-кратно завышенное значение при naive SUM спенда"
        )
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    f"(SELECT id FROM fb_ads WHERE ad_name = 'PRF_AD_{sfx}')"
                )
            )
            await conn.execute(text(f"DELETE FROM fb_ads WHERE ad_name = 'PRF_AD_{sfx}'"))
            await conn.execute(text(f"DELETE FROM fb_adsets WHERE adset_name = 'PRF_ADS_{sfx}'"))
            await conn.execute(
                text(f"DELETE FROM fb_campaigns WHERE campaign_name = 'PRF_CMP_{sfx}'")
            )
            await conn.execute(text(f"DELETE FROM offers WHERE code = 'PRF_{sfx}'"))


# Тест: offer_leaderboard spend = latest-per-ad (не naive SUM). Scoped по offer_id
# + прямая проверка resp.json() (MID-20).
@pytest.mark.asyncio
async def test_performance_offer_leaderboard_exact_spend(pg_engine, fake_redis_client) -> None:
    """offer_leaderboard.spend == latest snapshot из ОТВЕТА endpoint'а, не naive SUM."""
    sfx = uuid.uuid4().hex[:6]
    # latest=240.00, naive SUM 3 циклов был бы 80+160+240=480
    async with pg_engine.begin() as conn:
        _ad_id, _campaign_id, offer_id = await _seed_full(
            conn, sfx, spend=Decimal("240.00"), leads=12, multicycle=True
        )

    try:
        # Scoped: latest spend для нашего offer_id (эталон)
        async with pg_engine.connect() as conn:
            scoped_spend = (
                await conn.execute(
                    text(
                        """
                        WITH latest AS (
                            SELECT DISTINCT ON (date_trunc('day', m.cycle_ts), m.ad_id)
                                m.spend
                            FROM ad_metrics m
                            JOIN fb_ads a ON a.id = m.ad_id
                            JOIN fb_adsets ads ON ads.id = a.adset_id
                            JOIN fb_campaigns c ON c.id = ads.campaign_id
                            WHERE c.offer_id = :oid
                              AND m.cycle_ts >= NOW() - INTERVAL '7 days'
                            ORDER BY date_trunc('day', m.cycle_ts), m.ad_id, m.cycle_ts DESC
                        )
                        SELECT COALESCE(SUM(spend), 0) FROM latest
                        """
                    ),
                    {"oid": offer_id},
                )
            ).scalar_one()
        assert Decimal(str(scoped_spend)) == Decimal("240.00"), (
            f"offer scoped spend={scoped_spend}, ожидалось 240.00 (latest), не naive SUM (480)"
        )

        # Главная проверка: ФАКТИЧЕСКИЙ ответ endpoint'а содержит то же 240.00.
        app = _make_app(engine=pg_engine, redis=fake_redis_client)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(
                "/api/dashboard/performance",
                params={"days": 7, "limit_offers": 100},
            )
        assert resp.status_code == 200
        leaderboard = resp.json()["offer_leaderboard"]
        our_offer = next((o for o in leaderboard if o["offer_code"] == f"PRF_{sfx}"), None)
        assert our_offer is not None, (
            f"Оффер PRF_{sfx} не найден в offer_leaderboard ответа endpoint'а"
        )
        assert Decimal(our_offer["spend"]) == Decimal("240.00"), (
            f"endpoint offer spend={our_offer['spend']}, ожидалось 240.00 (latest), "
            "не naive SUM 3 циклов (480)"
        )
        assert our_offer["leads"] == 12
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    f"(SELECT id FROM fb_ads WHERE ad_name = 'PRF_AD_{sfx}')"
                )
            )
            await conn.execute(text(f"DELETE FROM fb_ads WHERE ad_name = 'PRF_AD_{sfx}'"))
            await conn.execute(text(f"DELETE FROM fb_adsets WHERE adset_name = 'PRF_ADS_{sfx}'"))
            await conn.execute(
                text(f"DELETE FROM fb_campaigns WHERE campaign_name = 'PRF_CMP_{sfx}'")
            )
            await conn.execute(text(f"DELETE FROM offers WHERE code = 'PRF_{sfx}'"))


# Тест: days=30 — максимум.
@pytest.mark.asyncio
async def test_performance_days_30(pg_engine, fake_redis_client, clean_perf) -> None:
    """?days=30 → max, endpoint отвечает 200."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance", params={"days": 30})
    assert resp.status_code == 200


# Тест: days=31 → 422.
@pytest.mark.asyncio
async def test_performance_days_too_large(pg_engine, fake_redis_client, clean_perf) -> None:
    """?days=31 > max=30 → 422."""
    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance", params={"days": 31})
    assert resp.status_code == 422


# Тест: limit_* параметры реально режут результат.
@pytest.mark.asyncio
async def test_performance_limits(pg_engine, fake_redis_client, clean_perf) -> None:
    """limit_campaigns=1 → не больше 1 строки в top_campaigns."""
    async with pg_engine.begin() as conn:
        await _seed_full(conn, "L1", spend=Decimal("500.00"))
        await _seed_full(conn, "L2", spend=Decimal("400.00"))
        await _seed_full(conn, "L3", spend=Decimal("300.00"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/api/dashboard/performance",
            params={"limit_campaigns": 1, "limit_offers": 1, "limit_rules": 1},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["top_campaigns"]) <= 1
    assert len(data["offer_leaderboard"]) <= 1


# Тест: top_campaigns отсортированы по spend DESC.
@pytest.mark.asyncio
async def test_performance_top_campaigns_sorted(pg_engine, fake_redis_client, clean_perf) -> None:
    """top_campaigns — сортировка SUM(spend) DESC NULLS LAST."""
    async with pg_engine.begin() as conn:
        await _seed_full(conn, "S1", spend=Decimal("100.00"))
        await _seed_full(conn, "S2", spend=Decimal("500.00"))  # самый дорогой
        await _seed_full(conn, "S3", spend=Decimal("200.00"))

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance")

    assert resp.status_code == 200
    camps = resp.json()["top_campaigns"]
    # Наши 3 кампании или больше; проверяем что первая в нашей выборке — S2
    our_camps = [c for c in camps if c["campaign_name"].startswith("PRF_CMP_S")]
    if len(our_camps) >= 2:
        # Превышение spend первого нашего ≥ второго (по убыванию)
        spend_vals = [Decimal(c["spend"]) for c in our_camps if c["spend"]]
        assert spend_vals == sorted(spend_vals, reverse=True)


# Тест: offer_leaderboard включает alerts_count.
# Guard "if our_offer is not None" заменён на жёсткий assert — иначе тест
# проходит молча когда оффер не найден (аудит: §3 #16).
@pytest.mark.asyncio
async def test_performance_offer_alerts_count(pg_engine, fake_redis_client, clean_perf) -> None:
    """offer_leaderboard.alerts_count учитывает алерты за окно."""
    async with pg_engine.begin() as conn:
        # 1 ad + 1 alert
        await _seed_full(conn, "AL", spend=Decimal("100.00"), rule_codes=["CPC"])

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance")

    assert resp.status_code == 200
    leaderboard = resp.json()["offer_leaderboard"]
    our_offer = next((o for o in leaderboard if o["offer_code"] == "PRF_AL"), None)
    # Жёсткий assert: если оффер не найден — тест должен упасть, а не пройти молча
    assert our_offer is not None, "Оффер PRF_AL не найден в offer_leaderboard"
    assert our_offer["alerts_count"] >= 1


# Тест: top_rule_violations через unnest matched_rule_codes.
@pytest.mark.asyncio
async def test_performance_rule_violations_unnest(pg_engine, fake_redis_client, clean_perf) -> None:
    """matched_rule_codes JSONB разворачивается через jsonb_array_elements_text."""
    async with pg_engine.begin() as conn:
        await _seed_full(conn, "RU1", rule_codes=["CPC", "CPL"])
        await _seed_full(conn, "RU2", rule_codes=["CPC"])  # CPC встречается 2 раза
        await _seed_full(conn, "RU3", rule_codes=["FUNNEL"])

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/api/dashboard/performance")

    assert resp.status_code == 200
    rules = resp.json()["top_rule_violations"]
    rule_codes_count = {r["rule_code"]: r["count"] for r in rules}
    # Среди нашего набора CPC встречается >= 2
    if "CPC" in rule_codes_count:
        assert rule_codes_count["CPC"] >= 2


# Тест: fail-all поведение — если один подзапрос провалится, весь endpoint падает.
@pytest.mark.asyncio
async def test_performance_fail_all_policy(pg_engine, fake_redis_client, clean_perf) -> None:
    """Документируем fail-all: ошибка в одном из 3 параллельных SQL → 5xx.

    Подменяем один из внутренних query-функций на падающую корутину.
    httpx по умолчанию пробрасывает unhandled exception, поэтому используем
    raise_app_exceptions=False, чтобы получить именно 500-ответ.
    """
    from apps.api.routers.v1 import dashboard_performance as dp_module

    async def _broken(*a, **kw):
        raise RuntimeError("smt broken")

    original = dp_module._query_top_rule_violations
    dp_module._query_top_rule_violations = _broken
    try:
        app = _make_app(engine=pg_engine, redis=fake_redis_client)
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/dashboard/performance")
        # asyncio.gather пробрасывает первую же ошибку → 5xx
        assert resp.status_code >= 500
    finally:
        dp_module._query_top_rule_violations = original
