# -*- coding: utf-8 -*-
"""Семантический тест MID-17 (аудит 02.07): current_day_spend без наивного SUM.

`current_day_spend` (core/dashboard/cabinet_spend.py) — это headline-спенд
дашборда («сколько потрачено СЕГОДНЯ»). Ad_metrics хранит КУМУЛЯТИВНЫЕ
snapshot'ы (каждый scan-цикл дописывает накопленное за сутки значение), а сама
функция уже реализует latest-per-ad с полом по границе суток кабинета — но,
в отличие от остальных money-агрегаций (chart-data/history/offers), у неё не
было ни одного семантического SQL-теста (только unit-тест cabinet_day_start_utc
на чистой pure-функции, без реального прогона против БД). Это прямой
рецидив-риск CRIT-1 (см. tests/integration/test_metric_aggregation_semantics.py) —
если кто-то по неосторожности заменит LEFT JOIN LATERAL на SUM() по окну,
регресс не поймает ни один существующий тест.

Проверяем на реальном Postgres:
  1. 5 кумулятивных снимков на два объявления в ТЕКУЩИХ сутках кабинета →
     сумма = latest per-ad (не сумма всех снимков).
  2. Снимок ДО границы суток кабинета (вчерашний остаток) не подмешивается —
     граница cabinet-day по offset'у реально фильтрует.
  3. Мульти-кабинет: два ad_account_id с разными offset'ами — каждый считается
     по СВОЕЙ границе суток (per-account tz_map), а не общей.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.dashboard.cabinet_spend import cabinet_day_start_utc, current_day_spend


async def _seed_ad(
    conn,
    *,
    code_suffix: str,
    ad_suffix: str,
    ad_account_id: str | None,
) -> dict:
    """Создаёт offer→campaign(ad_account_id)→adset→1 ad. Возвращает id'шники."""
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()

    await conn.execute(
        text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
        {"i": offer_id, "c": f"CDS_{code_suffix}", "n": f"CabinetSpend {code_suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO fb_campaigns (id, campaign_name, offer_id, ad_account_id) "
            "VALUES (:i, :n, :o, :a)"
        ),
        {"i": campaign_id, "n": f"CDS_CMP_{code_suffix}", "o": offer_id, "a": ad_account_id},
    )
    await conn.execute(
        text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
        {"i": adset_id, "c": campaign_id, "n": f"CDS_ADS_{code_suffix}"},
    )
    await conn.execute(
        text(
            "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name, is_active, last_seen_at) "
            "VALUES (:i, :a, :f, :n, true, NOW())"
        ),
        {"i": ad_id, "a": adset_id, "f": f"cds_{ad_suffix}", "n": f"CDS_AD_{ad_suffix}"},
    )
    return {"offer_id": offer_id, "campaign_id": campaign_id, "ad_id": ad_id}


async def _insert_metric_at(conn, *, ad_id: uuid.UUID, cycle_ts: datetime, spend: Decimal) -> None:
    """Вставляет один кумулятивный snapshot с явным (Python-стороны) cycle_ts."""
    await conn.execute(
        text(
            "INSERT INTO ad_metrics (id, ad_id, cycle_ts, spend, leads, deposits) "
            "VALUES (gen_random_uuid(), :a, :ts, :s, 0, 0)"
        ),
        {"a": ad_id, "ts": cycle_ts, "s": spend},
    )


@pytest_asyncio.fixture
async def clean_cabinet_spend(pg_engine: AsyncEngine):
    """Чистит созданные тестом строки до и после (префикс CDS_)."""

    async def _cleanup():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM ad_metrics WHERE ad_id IN "
                    "(SELECT id FROM fb_ads WHERE ad_name LIKE 'CDS\\_AD\\_%')"
                )
            )
            await conn.execute(text("DELETE FROM fb_ads WHERE ad_name LIKE 'CDS\\_AD\\_%'"))
            await conn.execute(text("DELETE FROM fb_adsets WHERE adset_name LIKE 'CDS\\_ADS\\_%'"))
            await conn.execute(
                text("DELETE FROM fb_campaigns WHERE campaign_name LIKE 'CDS\\_CMP\\_%'")
            )
            await conn.execute(text("DELETE FROM offers WHERE code LIKE 'CDS\\_%'"))

    await _cleanup()
    yield
    await _cleanup()


# Сценарий 1: 5 кумулятивных снимков на два объявления в текущих сутках кабинета (UTC,
# offset=0) → current_day_spend должен взять latest на КАЖДОЕ объявление (100 и 40),
# а не сложить все промежуточные снимки серии (что дало бы 300 и 100 → 400 вместо 140).
@pytest.mark.asyncio
async def test_current_day_spend_takes_latest_not_sum(
    pg_engine: AsyncEngine, clean_cabinet_spend
) -> None:
    now = datetime.now(UTC)
    boundary = cabinet_day_start_utc(0.0, now)
    # Снимки внутри сегодняшних суток, с запасом от границы (минимум +5 минут).
    base = boundary + timedelta(minutes=10)

    async with pg_engine.begin() as conn:
        ad1 = await _seed_ad(
            conn, code_suffix="LATEST1", ad_suffix="latest1", ad_account_id="acc_utc"
        )
        ad2 = await _seed_ad(
            conn, code_suffix="LATEST2", ad_suffix="latest2", ad_account_id="acc_utc"
        )

        for i, spend in enumerate([20, 40, 60, 80, 100]):
            await _insert_metric_at(
                conn, ad_id=ad1["ad_id"], cycle_ts=base + timedelta(minutes=i), spend=Decimal(spend)
            )
        for i, spend in enumerate([10, 20, 30, 40]):
            await _insert_metric_at(
                conn, ad_id=ad2["ad_id"], cycle_ts=base + timedelta(minutes=i), spend=Decimal(spend)
            )

    total = await current_day_spend(
        pg_engine,
        tz_map={"acc_utc": 0.0},
        default_offset=0.0,
        now=now,
    )
    # latest(ad1)=100 + latest(ad2)=40 = 140, НЕ сумма серии (300+100=400).
    assert total == Decimal("140"), (
        f"current_day_spend должен взять latest-per-ad (140), получил {total} — "
        "похоже на регресс к наивному SUM() по кумулятивной серии (CRIT-1)"
    )


# Сценарий 2: снимок ДО границы суток кабинета (вчерашний «хвост») не должен
# попадать в текущий спенд — граница cabinet_day_start_utc реально фильтрует
# по cycle_ts, а не просто берёт последний снимок независимо от даты.
@pytest.mark.asyncio
async def test_current_day_spend_excludes_prev_day_tail(
    pg_engine: AsyncEngine, clean_cabinet_spend
) -> None:
    now = datetime.now(UTC)
    boundary = cabinet_day_start_utc(0.0, now)

    async with pg_engine.begin() as conn:
        ad = await _seed_ad(
            conn, code_suffix="PREVDAY", ad_suffix="prevday", ad_account_id="acc_utc"
        )
        # Снимок ДО границы (вчерашний остаток кумулятивной серии, ещё не обнулился)
        await _insert_metric_at(
            conn, ad_id=ad["ad_id"], cycle_ts=boundary - timedelta(minutes=5), spend=Decimal("999")
        )

    total = await current_day_spend(
        pg_engine,
        tz_map={"acc_utc": 0.0},
        default_offset=0.0,
        now=now,
    )
    # Ад без снимка ПОСЛЕ полуночи → latest NULL → не учтён (его текущий спенд ещё 0,
    # новый снимок не пришёл). 999 из вчерашнего хвоста НЕ должен просочиться.
    assert total == Decimal("0"), (
        f"current_day_spend не должен учитывать снимки до границы суток кабинета, "
        f"получил {total} вместо 0 (утечка вчерашнего спенда)"
    )


# Сценарий 3: мульти-кабинет — два ad_account_id с РАЗНЫМИ offset'ами. Кабинет A
# (UTC+0) ещё не пересёк полночь на новый снимок, кабинет B (UTC+10) уже глубоко
# в новых сутках. Каждый должен считаться по СВОЕЙ границе (per-account tz_map),
# не общей — иначе смешение таймзон завышает/занижает спенд одного из кабинетов.
@pytest.mark.asyncio
async def test_current_day_spend_per_account_boundary(
    pg_engine: AsyncEngine, clean_cabinet_spend
) -> None:
    now = datetime.now(UTC)
    boundary_utc = cabinet_day_start_utc(0.0, now)
    boundary_plus10 = cabinet_day_start_utc(10.0, now)

    async with pg_engine.begin() as conn:
        ad_a = await _seed_ad(
            conn, code_suffix="ACCTA", ad_suffix="accta", ad_account_id="acc_a_utc0"
        )
        ad_b = await _seed_ad(
            conn, code_suffix="ACCTB", ad_suffix="acctb", ad_account_id="acc_b_utc10"
        )

        # Кабинет A (offset 0): один снимок сразу после его полуночи → latest=50.
        await _insert_metric_at(
            conn,
            ad_id=ad_a["ad_id"],
            cycle_ts=boundary_utc + timedelta(minutes=5),
            spend=Decimal("50"),
        )
        # Кабинет B (offset +10): снимок ДО его полуночи (вчерашний хвост, spend=999)
        # и снимок ПОСЛЕ его полуночи (latest=30) — граница B должна отсечь 999.
        await _insert_metric_at(
            conn,
            ad_id=ad_b["ad_id"],
            cycle_ts=boundary_plus10 - timedelta(minutes=10),
            spend=Decimal("999"),
        )
        await _insert_metric_at(
            conn,
            ad_id=ad_b["ad_id"],
            cycle_ts=boundary_plus10 + timedelta(minutes=5),
            spend=Decimal("30"),
        )

    total = await current_day_spend(
        pg_engine,
        tz_map={"acc_a_utc0": 0.0, "acc_b_utc10": 10.0},
        default_offset=0.0,
        now=now,
    )
    # 50 (A, своя граница) + 30 (B, своя граница, 999 отсечён) = 80, НЕ 999+50+30.
    assert total == Decimal("80"), (
        f"per-account граница cabinet-day должна дать 80 (50+30), получил {total} — "
        "похоже, кабинеты используют общую границу вместо своей taймзоны"
    )


__all__ = [
    "test_current_day_spend_takes_latest_not_sum",
    "test_current_day_spend_excludes_prev_day_tail",
    "test_current_day_spend_per_account_boundary",
]
