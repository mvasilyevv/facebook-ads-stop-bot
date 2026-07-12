# -*- coding: utf-8 -*-
"""Интеграционный: aggregate_postback_events → tracker_aggregate.

Проверяет СЕМАНТИКУ (значения денег), а не форму: идемпотентность absolute-recompute,
разрез по country/day, исключение дублей/NULL-fk/без-country, точные суммы.
Использует реальный Postgres (pg_engine) + fb_ad_fixture для валидного fb_ads.id.
Партиция adsetpro_postback_events за текущий месяц должна существовать (миграция 0001).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.tracker_aggregator_worker.worker import run_once
from core.adset_pro.aggregator import aggregate_postback_events

# Все события тестов — с этим префиксом click_id, чтобы cleanup был prefix-scoped (БД общая).
_PREFIX = "aggtest-"


@pytest_asyncio.fixture
async def clean_agg(pg_engine, fb_ad_fixture):
    """Чистит свои postback-события (по префиксу) и агрегаты (по ad_id) до и после теста."""
    ad_id = fb_ad_fixture.ad_id

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM adsetpro_postback_events WHERE click_id LIKE :p"),
                {"p": f"{_PREFIX}%"},
            )
            await conn.execute(
                text("DELETE FROM tracker_aggregate WHERE ad_id = :a"),
                {"a": ad_id},
            )

    await _truncate()
    yield ad_id
    await _truncate()


async def _insert_event(
    pg_engine,
    *,
    click_id: str,
    fb_ad_fk: uuid.UUID | None,
    event_type: str,
    revenue: Decimal,
    received_at: datetime,
    country: str | None,
    is_duplicate: bool = False,
) -> None:
    """Вставляет одно событие в adsetpro_postback_events. country кладём в raw_json."""
    raw = "{}" if country is None else f'{{"country": "{country}"}}'
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO adsetpro_postback_events
                    (received_at, click_id, fb_ad_id, fb_ad_fk, event_type,
                     revenue, currency, raw_json, signature_valid, is_duplicate)
                VALUES (:received_at, :click_id, NULL, :fb_ad_fk, :event_type,
                        :revenue, 'USD', CAST(:raw AS JSONB), TRUE, :is_dup)
                """
            ),
            {
                "received_at": received_at,
                "click_id": click_id,
                "fb_ad_fk": fb_ad_fk,
                "event_type": event_type,
                "revenue": revenue,
                "raw": raw,
                "is_dup": is_duplicate,
            },
        )


async def _get_agg(pg_engine, ad_id: uuid.UUID, country: str, day) -> dict | None:
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT installs, registrations, deposits, revenue, last_postback_at
                    FROM tracker_aggregate
                    WHERE ad_id = :a AND country = :c AND day = :d
                    """
                ),
                {"a": ad_id, "c": country, "d": day},
            )
        ).first()
    if row is None:
        return None
    return {
        "installs": row[0],
        "registrations": row[1],
        "deposits": row[2],
        "revenue": row[3],
        "last_postback_at": row[4],
    }


# Базовая агрегация + ИДЕМПОТЕНТНОСТЬ: 2 ftd + 1 reg за день → deposits=2/reg=1, повтор НЕ задваивает.
@pytest.mark.asyncio
async def test_aggregate_basic_and_idempotent(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 20, 10, 0, tzinfo=UTC)
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}1",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("25.00"),
        received_at=base,
        country="GH",
    )
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}2",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("30.50"),
        received_at=base + timedelta(minutes=1),
        country="GH",
    )
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}3",
        fb_ad_fk=ad_id,
        event_type="reg",
        revenue=Decimal("0"),
        received_at=base + timedelta(minutes=2),
        country="GH",
    )

    window_start = datetime(2026, 5, 20, 0, 0, tzinfo=UTC)
    window_end = datetime(2026, 5, 20, 23, 0, tzinfo=UTC)

    r1 = await aggregate_postback_events(
        pg_engine, window_start=window_start, window_end=window_end
    )
    assert r1.rows_inserted == 1
    agg = await _get_agg(pg_engine, ad_id, "GH", base.date())
    assert agg is not None
    # Точные значения: 2 депозита, 1 рега, 0 инсталлов, revenue = 25.00 + 30.50 (reg даёт 0).
    assert agg["deposits"] == 2
    assert agg["registrations"] == 1
    assert agg["installs"] == 0
    assert agg["revenue"] == Decimal("55.50")

    # Повторный прогон того же окна — значения НЕ удваиваются (absolute recompute).
    r2 = await aggregate_postback_events(
        pg_engine, window_start=window_start, window_end=window_end
    )
    assert r2.rows_updated == 1
    assert r2.rows_inserted == 0
    agg2 = await _get_agg(pg_engine, ad_id, "GH", base.date())
    assert agg2["deposits"] == 2
    assert agg2["revenue"] == Decimal("55.50")


# Разные UTC-дни → отдельные строки агрегата (разрез по day).
@pytest.mark.asyncio
async def test_aggregate_splits_by_day(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    d20 = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    d21 = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}d20",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("10"),
        received_at=d20,
        country="GH",
    )
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}d21a",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("10"),
        received_at=d21,
        country="GH",
    )
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}d21b",
        fb_ad_fk=ad_id,
        event_type="redep",
        revenue=Decimal("5"),
        received_at=d21 + timedelta(minutes=1),
        country="GH",
    )

    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 20, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 21, 23, 0, tzinfo=UTC),
    )
    agg20 = await _get_agg(pg_engine, ad_id, "GH", d20.date())
    agg21 = await _get_agg(pg_engine, ad_id, "GH", d21.date())
    assert agg20["deposits"] == 1
    # 20-го депозит на 10; redep тоже депозит → 21-го deposits=2, revenue=15.
    assert agg21["deposits"] == 2
    assert agg21["revenue"] == Decimal("15")


# Разные country в один день → отдельные строки (разрез по country, из raw_json).
@pytest.mark.asyncio
async def test_aggregate_splits_by_country(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 22, 9, 0, tzinfo=UTC)
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}gh",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("8"),
        received_at=base,
        country="GH",
    )
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}ke",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("3"),
        received_at=base,
        country="KE",
    )
    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 22, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 22, 23, 0, tzinfo=UTC),
    )
    gh = await _get_agg(pg_engine, ad_id, "GH", base.date())
    ke = await _get_agg(pg_engine, ad_id, "KE", base.date())
    assert gh["deposits"] == 1 and gh["revenue"] == Decimal("8")
    assert ke["deposits"] == 1 and ke["revenue"] == Decimal("3")


# Дубли (is_duplicate=TRUE) и события с NULL fb_ad_fk НЕ попадают в агрегат.
@pytest.mark.asyncio
async def test_aggregate_excludes_duplicates_and_null_fk(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 23, 11, 0, tzinfo=UTC)
    # Реальное событие.
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}real",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("20"),
        received_at=base,
        country="GH",
    )
    # Дубль — должен игнорироваться.
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}dup",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("999"),
        received_at=base + timedelta(minutes=1),
        country="GH",
        is_duplicate=True,
    )
    # NULL fk — не к чему привязать, игнор.
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}nofk",
        fb_ad_fk=None,
        event_type="ftd",
        revenue=Decimal("777"),
        received_at=base + timedelta(minutes=2),
        country="GH",
    )
    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 23, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 23, 23, 0, tzinfo=UTC),
    )
    agg = await _get_agg(pg_engine, ad_id, "GH", base.date())
    # Только реальное событие: deposits=1, revenue=20 (дубль и NULL-fk не в счёте).
    assert agg["deposits"] == 1
    assert agg["revenue"] == Decimal("20")


# M-8 (аудит 2026-07-12): событие без country → бакет country='XX' (деньги
# сохраняются), а НЕ дроп. Раньше deposits/revenue таких постбэков терялись.
@pytest.mark.asyncio
async def test_aggregate_missing_country_goes_to_sentinel(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 24, 8, 0, tzinfo=UTC)
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}noc",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("12"),
        received_at=base,
        country=None,
    )
    result = await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 24, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 24, 23, 0, tzinfo=UTC),
    )
    # Депозит и revenue не потеряны — они в sentinel-строке country='XX'.
    agg = await _get_agg(pg_engine, ad_id, "XX", base.date())
    assert agg is not None, "постбэк без country должен попасть в sentinel 'XX', не дропаться"
    assert agg["deposits"] == 1
    assert agg["revenue"] == Decimal("12")
    # Счётчик-наблюдаемость по-прежнему считает такие постбэки (сигнал смены формата).
    assert result.rows_dropped_invalid_country == 1


# Инкремент: после агрегации добавили события того же дня → повторный прогон отражает ВСЕ.
@pytest.mark.asyncio
async def test_aggregate_incremental_reflects_all(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 25, 7, 0, tzinfo=UTC)
    window = dict(
        window_start=datetime(2026, 5, 25, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 25, 23, 0, tzinfo=UTC),
    )
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}i1",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("10"),
        received_at=base,
        country="GH",
    )
    await aggregate_postback_events(pg_engine, **window)
    agg1 = await _get_agg(pg_engine, ad_id, "GH", base.date())
    assert agg1["deposits"] == 1 and agg1["revenue"] == Decimal("10")

    # Прилетело ещё два депозита того же дня.
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}i2",
        fb_ad_fk=ad_id,
        event_type="redep",
        revenue=Decimal("5"),
        received_at=base + timedelta(hours=1),
        country="GH",
    )
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}i3",
        fb_ad_fk=ad_id,
        event_type="baddep",
        revenue=Decimal("7"),
        received_at=base + timedelta(hours=2),
        country="GH",
    )
    await aggregate_postback_events(pg_engine, **window)
    agg2 = await _get_agg(pg_engine, ad_id, "GH", base.date())
    # ftd+redep+baddep — все депозиты → 3, revenue = 10+5+7 = 22.
    assert agg2["deposits"] == 3
    assert agg2["revenue"] == Decimal("22")


# День вне окна прогона НЕ пересчитывается (absolute recompute трогает только дни окна).
@pytest.mark.asyncio
async def test_aggregate_leaves_out_of_window_day_untouched(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    old_day = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    new_day = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}old",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("100"),
        received_at=old_day,
        country="GH",
    )
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}new",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("1"),
        received_at=new_day,
        country="GH",
    )
    # Прогон 1: широкое окно — оба дня агрегированы.
    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 10, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 26, 23, 0, tzinfo=UTC),
    )
    assert (await _get_agg(pg_engine, ad_id, "GH", old_day.date()))["deposits"] == 1

    # Удаляем старое событие и прогоняем УЗКОЕ окно (только 26-е) — старый день не трогаем.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM adsetpro_postback_events WHERE click_id = :c"),
            {"c": f"{_PREFIX}old"},
        )
    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 26, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 5, 26, 23, 0, tzinfo=UTC),
    )
    # Старый агрегат уцелел (его день не входил в окно), хотя исходное событие удалено.
    old_agg = await _get_agg(pg_engine, ad_id, "GH", old_day.date())
    assert old_agg is not None and old_agg["deposits"] == 1


# Worker.run_once: окно [now - lookback, now] агрегирует свежие события и пишет аудит.
@pytest.mark.asyncio
async def test_worker_run_once_aggregates_recent(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    now = datetime(2026, 5, 27, 15, 0, tzinfo=UTC)
    await _insert_event(
        pg_engine,
        click_id=f"{_PREFIX}w1",
        fb_ad_fk=ad_id,
        event_type="ftd",
        revenue=Decimal("9"),
        received_at=now - timedelta(minutes=30),
        country="KE",
    )
    result = await run_once(pg_engine, now=now, lookback=timedelta(hours=2))
    assert result.rows_upserted >= 1
    agg = await _get_agg(pg_engine, ad_id, "KE", now.date())
    assert agg is not None and agg["deposits"] == 1 and agg["revenue"] == Decimal("9")
