# -*- coding: utf-8 -*-
"""Семантические тесты против завышения spend (CRIT-1 money-bug).

`ad_metrics` хранит КУМУЛЯТИВНЫЕ snapshot'ы: каждый scan-цикл (~90с) пишет
строку с накопленным за сутки значением (spend/leads/deposits растут). Наивный
`SUM(spend)` по окну сложил бы все промежуточные снимки и завысил spend во
столько раз, сколько было циклов. Плюс spend сбрасывается посуточно (cabinet
day reset), поэтому многодневная агрегация должна складывать ДНЕВНЫЕ итоги.

Эти тесты проверяют активные offers/analytics endpoints и общий CTE на
кумулятивных данных: spend равен сумме последних snapshot'ов, а не всех строк.

Используем явные cycle_ts, привязанные к `date_trunc('day', now())` и
`date_trunc('hour', now())`, чтобы границы суток/часа были детерминированы
независимо от момента запуска теста (кроме редкого случая запуска ровно на
границе — циклы кладутся с запасом внутрь бакета).

Изоляция от чужих данных в shared-БД достигается отдельным offer/campaign id.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.deps import get_engine, get_redis
from apps.api.main import create_app
from core.dashboard.metric_aggregation import latest_per_ad_per_day_cte
from core.meta_api.account_tz import persist_account_context

_SAFE_ROLLING_DAY_ANCHOR_SQL = """(
    CASE
        WHEN NOW() - date_trunc('day', NOW()) >= INTERVAL '12 hours'
        THEN date_trunc('day', NOW()) + INTERVAL '6 hours'
        ELSE date_trunc('day', NOW()) - INTERVAL '1 day' + INTERVAL '18 hours'
    END
)"""


def _make_app(*, engine, redis):
    """FastAPI с подменой engine/redis (как в остальных integration-тестах)."""
    app = create_app()
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_redis] = lambda: redis
    app.state.redis = redis
    return app


async def _seed_chain(conn, *, code_suffix: str, account_id: str | None = None) -> dict:
    """Создаёт offer→campaign→adset→2 ads. Возвращает id'шники.

    Два объявления нужны, чтобы проверить, что spend складывается ПО объявлениям
    (после взятия latest на каждое), а не схлопывается в одно.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad1_id = uuid.uuid4()
    ad2_id = uuid.uuid4()
    fb_campaign_id = f"{campaign_id.int % 10**18:018d}"
    fb_adset_id = f"{adset_id.int % 10**18:018d}"
    fb_ad_ids = {
        ad1_id: f"{ad1_id.int % 10**18:018d}",
        ad2_id: f"{ad2_id.int % 10**18:018d}",
    }

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"SEM_{code_suffix}", "n": f"Semantics {code_suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO fb_campaigns "
            "(id, fb_campaign_id, campaign_name, offer_id, ad_account_id) "
            "VALUES (:i, :fb_campaign_id, :n, :o, :account_id)"
        ),
        {
            "i": campaign_id,
            "fb_campaign_id": fb_campaign_id,
            "n": f"SEM_CMP_{code_suffix}",
            "o": offer_id,
            "account_id": account_id,
        },
    )
    await conn.execute(
        text(
            "INSERT INTO fb_adsets (id, campaign_id, fb_adset_id, adset_name) "
            "VALUES (:i, :c, :fb_adset_id, :n)"
        ),
        {
            "i": adset_id,
            "c": campaign_id,
            "fb_adset_id": fb_adset_id,
            "n": f"SEM_ADS_{code_suffix}",
        },
    )
    for aid in (ad1_id, ad2_id):
        fb_ad_id = fb_ad_ids[aid]
        await conn.execute(
            text(
                "INSERT INTO fb_ads "
                "(id, adset_id, fb_ad_id, ad_name, first_seen_at, last_seen_at) "
                "VALUES (:i, :a, :f, :n, NOW() - INTERVAL '7 days', NOW())"
            ),
            {"i": aid, "a": adset_id, "f": fb_ad_id, "n": f"SEM_AD_{fb_ad_id}"},
        )

    stored_rows = (
        await conn.execute(
            text(
                """
                SELECT c.fb_campaign_id, c.ad_account_id, s.fb_adset_id, a.fb_ad_id
                FROM fb_ads AS a
                JOIN fb_adsets AS s ON s.id = a.adset_id
                JOIN fb_campaigns AS c ON c.id = s.campaign_id
                WHERE a.id = ANY(:ad_ids)
                ORDER BY a.id
                """
            ),
            {"ad_ids": [ad1_id, ad2_id]},
        )
    ).all()
    assert len(stored_rows) == 2
    assert {row.fb_ad_id for row in stored_rows} == set(fb_ad_ids.values())
    for row in stored_rows:
        identity = (
            row.fb_campaign_id,
            row.fb_adset_id,
            row.fb_ad_id,
        )
        assert all(value.isdigit() for value in identity)
        assert row.ad_account_id is None or row.ad_account_id.isdigit()

    return {
        "offer_id": offer_id,
        "campaign_id": campaign_id,
        "ad1_id": ad1_id,
        "ad2_id": ad2_id,
        "offer_code": f"SEM_{code_suffix}",
        "campaign_name": f"SEM_CMP_{code_suffix}",
        "account_id": account_id,
        "fb_campaign_id": fb_campaign_id,
        "fb_adset_id": fb_adset_id,
        "fb_ad_ids": tuple(fb_ad_ids.values()),
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
                text(
                    "DELETE FROM meta_account_snapshot WHERE account_id IN "
                    "(SELECT ad_account_id FROM fb_campaigns "
                    "WHERE campaign_name LIKE 'SEM\\_CMP\\_%' "
                    "AND ad_account_id IS NOT NULL)"
                )
            )
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'SEM\\_CMP\\_%'")
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'SEM\\_%'"))

    await _cleanup()
    yield
    await _cleanup()


@pytest.mark.asyncio
async def test_cabinet_timezone_groups_across_utc_midnight_for_cte_and_performance(
    pg_engine, fake_redis_client, clean_semantics
) -> None:
    """UTC midnight is not a reset for an Asia/Singapore Meta cabinet."""
    canonical_account = f"{uuid.uuid4().int % 10**12:012d}"
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(
            conn,
            code_suffix="CABTZ",
            account_id=canonical_account,
        )
        anchor = await conn.scalar(text("SELECT date_trunc('day', NOW())"))
        for expression, spend in (
            ("date_trunc('day', NOW()) - INTERVAL '10 minutes'", "40"),
            ("date_trunc('day', NOW()) + INTERVAL '10 minutes'", "50"),
            ("date_trunc('day', NOW()) + INTERVAL '16 hours 10 minutes'", "5"),
            ("date_trunc('day', NOW()) + INTERVAL '17 hours'", "10"),
        ):
            await _insert_metric(
                conn,
                ad_id=ids["ad1_id"],
                cycle_ts_sql=expression,
                spend=Decimal(spend),
                leads=int(Decimal(spend)),
            )
            # Campaign-level analytics is lossless: the sibling ad must have a
            # confirmed snapshot too, otherwise the campaign spend is partial.
            await _insert_metric(
                conn,
                ad_id=ids["ad2_id"],
                cycle_ts_sql=expression,
                spend=Decimal("0"),
                leads=0,
            )

    assert await persist_account_context(
        pg_engine,
        account_id=canonical_account,
        timezone_name="Asia/Singapore",
        currency="USD",
    )
    from_dt = anchor - timedelta(hours=1)
    to_dt = anchor + timedelta(hours=18)
    cte = latest_per_ad_per_day_cte(cte_alias="per_ad_day", columns=("spend",))
    async with pg_engine.connect() as conn:
        total, cabinet_days, timezone_known, account_id = (
            await conn.execute(
                text(
                    f"WITH {cte} "
                    "SELECT COALESCE(SUM(spend), 0), COUNT(*), "
                    "BOOL_AND(timezone_known), MAX(ad_account_id) "
                    "FROM per_ad_day WHERE ad_id = :ad_id"
                ),
                {"from_dt": from_dt, "to_dt": to_dt, "ad_id": ids["ad1_id"]},
            )
        ).one()
        persisted = (
            await conn.execute(
                text(
                    "SELECT account_id, timezone_name, currency "
                    "FROM meta_account_snapshot WHERE account_id = :account_id"
                ),
                {"account_id": canonical_account},
            )
        ).one()
    assert Decimal(total) == Decimal("60")
    assert cabinet_days == 2
    assert timezone_known is True
    assert account_id == canonical_account
    assert persisted == (canonical_account, "Asia/Singapore", "USD")

    app = _make_app(engine=pg_engine, redis=fake_redis_client)
    cabinet_timezone = ZoneInfo("Asia/Singapore")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get(
            "/api/analytics/performance",
            params={
                "period": "custom",
                "from_date": from_dt.astimezone(cabinet_timezone).date().isoformat(),
                "to_date": to_dt.astimezone(cabinet_timezone).date().isoformat(),
                "level": "campaign",
                "campaign_id": str(ids["campaign_id"]),
                "account_id": canonical_account,
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rows"][0]["spend"] == "60.00", body["rows"][0]
    assert body["window"]["timezone"] == "Asia/Singapore"
    assert body["window"]["timezone_known"] is True
    assert body["rows"][0]["timezone_known"] is True


@pytest.mark.asyncio
async def test_invalid_persisted_timezone_falls_back_to_utc(pg_engine, clean_semantics) -> None:
    canonical_account = f"{uuid.uuid4().int % 10**12:012d}"
    async with pg_engine.begin() as conn:
        ids = await _seed_chain(
            conn,
            code_suffix="BADTZ",
            account_id=canonical_account,
        )
        anchor = await conn.scalar(text("SELECT date_trunc('day', NOW())"))
        await conn.execute(
            text(
                """
                INSERT INTO meta_account_snapshot
                    (account_id, timezone_name, currency, currency_observed_at)
                VALUES (:account_id, 'Definitely/Not-A-Timezone', 'USD', NOW())
                """
            ),
            {"account_id": canonical_account},
        )
        for expression, spend in (
            ("date_trunc('day', NOW()) - INTERVAL '10 minutes'", "40"),
            ("date_trunc('day', NOW()) + INTERVAL '10 minutes'", "50"),
            ("date_trunc('day', NOW()) + INTERVAL '17 hours'", "10"),
        ):
            await _insert_metric(
                conn,
                ad_id=ids["ad1_id"],
                cycle_ts_sql=expression,
                spend=Decimal(spend),
                leads=int(Decimal(spend)),
            )

    cte = latest_per_ad_per_day_cte(cte_alias="per_ad_day", columns=("spend",))
    async with pg_engine.connect() as conn:
        total, timezone_known, cabinet_timezone = (
            await conn.execute(
                text(
                    f"WITH {cte} SELECT COALESCE(SUM(spend), 0), "
                    "BOOL_AND(timezone_known), MAX(cabinet_timezone) "
                    "FROM per_ad_day WHERE ad_id = :ad_id"
                ),
                {
                    "from_dt": anchor - timedelta(hours=1),
                    "to_dt": anchor + timedelta(hours=18),
                    "ad_id": ids["ad1_id"],
                },
            )
        ).one()
    # Explicit UTC fallback: previous UTC day=40, current UTC day latest=10.
    assert Decimal(total) == Decimal("50")
    assert timezone_known is False
    assert cabinet_timezone == "UTC"


# ─────────────────── Граничные случаи ─────────────────────────────────────────
