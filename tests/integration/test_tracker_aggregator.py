# -*- coding: utf-8 -*-
"""PostgreSQL contracts for click-state reconciliation into tracker_aggregate."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.tracker_aggregator_worker.worker import run_once
from core.adset_pro.aggregator import aggregate_postback_events

_PREFIX = "aggtest-"


@pytest_asyncio.fixture
async def clean_agg(pg_engine, fb_ad_fixture):
    ad_id = fb_ad_fixture.ad_id

    async def _clean() -> None:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM adsetpro_postback_events WHERE click_id LIKE :prefix"),
                {"prefix": f"{_PREFIX}%"},
            )
            await conn.execute(
                text("DELETE FROM tracker_click_state WHERE click_id LIKE :prefix"),
                {"prefix": f"{_PREFIX}%"},
            )
            await conn.execute(
                text("DELETE FROM tracker_aggregate WHERE ad_id = :ad_id"), {"ad_id": ad_id}
            )
            await conn.execute(
                text("DELETE FROM system_config WHERE key = 'tracker_aggregator_runs'")
            )

    await _clean()
    yield ad_id
    await _clean()


async def _insert_click_state(
    pg_engine,
    *,
    click_id: str,
    ad_id: uuid.UUID | None,
    occurred_at: datetime,
    revenue: Decimal = Decimal("0"),
    country: str | None = "GH",
    confirmed: bool = True,
) -> None:
    registration_at = occurred_at - timedelta(seconds=1)
    ftd_at = occurred_at if confirmed else None
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO tracker_click_state
                    (id, source, click_id, ad_id, fb_ad_id, country, attribution_status,
                     registration, ftd, confirmed_deposit, registration_at, ftd_at,
                     confirmed_deposit_at, ftd_revenue, redeposits, redeposit_revenue,
                     last_event_at, version, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), 'adsetpro', :click_id, :ad_id,
                     (SELECT fb_ad_id FROM fb_ads WHERE id = :ad_id), :country,
                     CASE WHEN :ad_id IS NULL THEN 'unmatched' ELSE 'matched_direct' END,
                     TRUE, :confirmed, :confirmed, :registration_at, :ftd_at,
                     :ftd_at, :revenue, 0, 0, :last_event_at, 1, now(), now())
                """
            ),
            {
                "click_id": click_id,
                "ad_id": ad_id,
                "country": country,
                "confirmed": confirmed,
                "registration_at": registration_at,
                "ftd_at": ftd_at,
                "revenue": revenue,
                "last_event_at": occurred_at,
            },
        )


async def _insert_redeposit(
    pg_engine,
    *,
    click_id: str,
    ad_id: uuid.UUID | None,
    occurred_at: datetime,
    revenue: Decimal,
    country: str | None = "GH",
    is_duplicate: bool = False,
) -> None:
    raw = {} if country is None else {"country": country}
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO adsetpro_postback_events
                    (received_at, occurred_at, source, provider_event_id, click_id,
                     fb_ad_id, fb_ad_fk, event_type, revenue, currency, raw_json,
                     signature_valid, is_duplicate, attribution_status, processed_at)
                VALUES
                    (:occurred_at, :occurred_at, 'adsetpro', :provider_event_id, :click_id,
                     (SELECT fb_ad_id FROM fb_ads WHERE id = :ad_id), :ad_id,
                     'redeposit', :revenue, 'USD', CAST(:raw AS JSONB), TRUE,
                     :is_duplicate,
                     CASE WHEN :ad_id IS NULL THEN 'unmatched' ELSE 'matched_direct' END,
                     now())
                """
            ),
            {
                "occurred_at": occurred_at,
                "provider_event_id": f"{click_id}-tx",
                "click_id": click_id,
                "ad_id": ad_id,
                "revenue": revenue,
                "raw": json.dumps(raw),
                "is_duplicate": is_duplicate,
            },
        )


async def _get_agg(pg_engine, ad_id: uuid.UUID, country: str, day) -> dict | None:
    async with pg_engine.connect() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT installs, registrations, ftds, deposits,
                           confirmed_deposits, redeposits, revenue, last_postback_at
                    FROM tracker_aggregate
                    WHERE ad_id = :ad_id AND country = :country AND day = :day
                    """
                    ),
                    {"ad_id": ad_id, "country": country, "day": day},
                )
            )
            .mappings()
            .first()
        )
    return dict(row) if row is not None else None


@pytest.mark.asyncio
async def test_aggregate_requires_registration_and_ftd_and_is_idempotent(
    pg_engine, clean_agg
) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 20, 10, 0, tzinfo=UTC)
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}1",
        ad_id=ad_id,
        occurred_at=base,
        revenue=Decimal("25.00"),
    )
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}2",
        ad_id=ad_id,
        occurred_at=base + timedelta(minutes=1),
        revenue=Decimal("30.50"),
    )
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}registration-only",
        ad_id=ad_id,
        occurred_at=base + timedelta(minutes=2),
        confirmed=False,
    )
    window = {
        "window_start": datetime(2026, 5, 20, tzinfo=UTC),
        "window_end": datetime(2026, 5, 20, 23, tzinfo=UTC),
    }

    first = await aggregate_postback_events(pg_engine, **window)
    assert first.rows_inserted == 1
    aggregate = await _get_agg(pg_engine, ad_id, "GH", base.date())
    assert aggregate is not None
    assert aggregate["registrations"] == 3
    assert aggregate["ftds"] == 2
    assert aggregate["deposits"] == 2
    assert aggregate["confirmed_deposits"] == 2
    assert aggregate["redeposits"] == 0
    assert aggregate["revenue"] == Decimal("55.50")

    second = await aggregate_postback_events(pg_engine, **window)
    assert second.rows_inserted == 0
    assert second.rows_updated == 1
    assert (await _get_agg(pg_engine, ad_id, "GH", base.date()))["deposits"] == 2


@pytest.mark.asyncio
async def test_aggregate_splits_days_and_keeps_redeposit_analytics_separate(
    pg_engine, clean_agg
) -> None:
    ad_id = clean_agg
    day_one = datetime(2026, 5, 20, 12, tzinfo=UTC)
    day_two = datetime(2026, 5, 21, 12, tzinfo=UTC)
    await _insert_click_state(
        pg_engine, click_id=f"{_PREFIX}d20", ad_id=ad_id, occurred_at=day_one, revenue=Decimal("10")
    )
    await _insert_click_state(
        pg_engine, click_id=f"{_PREFIX}d21", ad_id=ad_id, occurred_at=day_two, revenue=Decimal("10")
    )
    await _insert_redeposit(
        pg_engine,
        click_id=f"{_PREFIX}d21-redeposit",
        ad_id=ad_id,
        occurred_at=day_two + timedelta(minutes=1),
        revenue=Decimal("5"),
    )

    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 20, tzinfo=UTC),
        window_end=datetime(2026, 5, 21, 23, tzinfo=UTC),
    )
    first = await _get_agg(pg_engine, ad_id, "GH", day_one.date())
    second = await _get_agg(pg_engine, ad_id, "GH", day_two.date())
    assert first["deposits"] == 1 and first["revenue"] == Decimal("10")
    assert second["deposits"] == 1
    assert second["redeposits"] == 1
    assert second["revenue"] == Decimal("15")


@pytest.mark.asyncio
async def test_aggregate_splits_by_country(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 22, 9, tzinfo=UTC)
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}gh",
        ad_id=ad_id,
        occurred_at=base,
        revenue=Decimal("8"),
        country="GH",
    )
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}ke",
        ad_id=ad_id,
        occurred_at=base,
        revenue=Decimal("3"),
        country="KE",
    )
    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 22, tzinfo=UTC),
        window_end=datetime(2026, 5, 22, 23, tzinfo=UTC),
    )
    assert (await _get_agg(pg_engine, ad_id, "GH", base.date()))["revenue"] == Decimal("8")
    assert (await _get_agg(pg_engine, ad_id, "KE", base.date()))["revenue"] == Decimal("3")


@pytest.mark.asyncio
async def test_aggregate_excludes_duplicate_and_unmatched_redeposit(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 23, 11, tzinfo=UTC)
    await _insert_click_state(
        pg_engine, click_id=f"{_PREFIX}real", ad_id=ad_id, occurred_at=base, revenue=Decimal("20")
    )
    await _insert_redeposit(
        pg_engine,
        click_id=f"{_PREFIX}duplicate",
        ad_id=ad_id,
        occurred_at=base + timedelta(minutes=1),
        revenue=Decimal("999"),
        is_duplicate=True,
    )
    await _insert_redeposit(
        pg_engine,
        click_id=f"{_PREFIX}unmatched",
        ad_id=None,
        occurred_at=base + timedelta(minutes=2),
        revenue=Decimal("777"),
    )
    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 23, tzinfo=UTC),
        window_end=datetime(2026, 5, 23, 23, tzinfo=UTC),
    )
    aggregate = await _get_agg(pg_engine, ad_id, "GH", base.date())
    assert aggregate["deposits"] == 1
    assert aggregate["redeposits"] == 0
    assert aggregate["revenue"] == Decimal("20")


@pytest.mark.asyncio
async def test_aggregate_missing_country_uses_xx_and_reports_quality(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 24, 8, tzinfo=UTC)
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}no-country",
        ad_id=ad_id,
        occurred_at=base,
        revenue=Decimal("12"),
        country=None,
    )
    result = await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 24, tzinfo=UTC),
        window_end=datetime(2026, 5, 24, 23, tzinfo=UTC),
    )
    aggregate = await _get_agg(pg_engine, ad_id, "XX", base.date())
    assert aggregate["deposits"] == 1
    assert aggregate["revenue"] == Decimal("12")
    assert result.rows_dropped_invalid_country == 1


@pytest.mark.asyncio
async def test_aggregate_incremental_adds_confirmed_and_redeposit_without_negative_statuses(
    pg_engine, clean_agg
) -> None:
    ad_id = clean_agg
    base = datetime(2026, 5, 25, 7, tzinfo=UTC)
    window = {
        "window_start": datetime(2026, 5, 25, tzinfo=UTC),
        "window_end": datetime(2026, 5, 25, 23, tzinfo=UTC),
    }
    await _insert_click_state(
        pg_engine, click_id=f"{_PREFIX}i1", ad_id=ad_id, occurred_at=base, revenue=Decimal("10")
    )
    await aggregate_postback_events(pg_engine, **window)
    await _insert_redeposit(
        pg_engine,
        click_id=f"{_PREFIX}i2",
        ad_id=ad_id,
        occurred_at=base + timedelta(hours=1),
        revenue=Decimal("5"),
    )
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}i3",
        ad_id=ad_id,
        occurred_at=base + timedelta(hours=2),
        revenue=Decimal("7"),
    )
    await aggregate_postback_events(pg_engine, **window)
    aggregate = await _get_agg(pg_engine, ad_id, "GH", base.date())
    assert aggregate["deposits"] == 2
    assert aggregate["redeposits"] == 1
    assert aggregate["revenue"] == Decimal("22")


@pytest.mark.asyncio
async def test_aggregate_leaves_out_of_window_day_untouched(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    old_day = datetime(2026, 5, 10, 12, tzinfo=UTC)
    new_day = datetime(2026, 5, 26, 12, tzinfo=UTC)
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}old",
        ad_id=ad_id,
        occurred_at=old_day,
        revenue=Decimal("100"),
    )
    await _insert_click_state(
        pg_engine, click_id=f"{_PREFIX}new", ad_id=ad_id, occurred_at=new_day, revenue=Decimal("1")
    )
    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 10, tzinfo=UTC),
        window_end=datetime(2026, 5, 26, 23, tzinfo=UTC),
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM tracker_click_state WHERE click_id = :click_id"),
            {"click_id": f"{_PREFIX}old"},
        )
    await aggregate_postback_events(
        pg_engine,
        window_start=datetime(2026, 5, 26, tzinfo=UTC),
        window_end=datetime(2026, 5, 26, 23, tzinfo=UTC),
    )
    assert (await _get_agg(pg_engine, ad_id, "GH", old_day.date()))["deposits"] == 1


@pytest.mark.asyncio
async def test_worker_run_once_aggregates_recent(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    now = datetime(2026, 5, 27, 15, tzinfo=UTC)
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}worker",
        ad_id=ad_id,
        occurred_at=now - timedelta(minutes=30),
        revenue=Decimal("9"),
        country="KE",
    )
    result = await run_once(pg_engine, now=now, lookback=timedelta(hours=2))
    assert result.rows_upserted >= 1
    aggregate = await _get_agg(pg_engine, ad_id, "KE", now.date())
    assert aggregate["deposits"] == 1 and aggregate["revenue"] == Decimal("9")


async def _set_last_run_at(pg_engine, when: datetime) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES ('tracker_aggregator_runs', CAST(:value AS JSONB), 'test')
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
                """
            ),
            {"value": json.dumps({"last_run_at": when.isoformat()})},
        )


@pytest.mark.asyncio
async def test_worker_catchup_after_downtime_across_midnight(pg_engine, clean_agg) -> None:
    ad_id = clean_agg
    event_at = datetime(2026, 5, 27, 23, 30, tzinfo=UTC)
    now = datetime(2026, 5, 28, 3, tzinfo=UTC)
    await _insert_click_state(
        pg_engine,
        click_id=f"{_PREFIX}catchup",
        ad_id=ad_id,
        occurred_at=event_at,
        revenue=Decimal("15"),
    )
    await _set_last_run_at(pg_engine, datetime(2026, 5, 27, 23, tzinfo=UTC))
    await run_once(pg_engine, now=now, lookback=timedelta(hours=2))
    aggregate = await _get_agg(pg_engine, ad_id, "GH", event_at.date())
    assert aggregate["deposits"] == 1 and aggregate["revenue"] == Decimal("15")


@pytest.mark.asyncio
async def test_worker_catchup_is_capped(pg_engine, clean_agg) -> None:
    from apps.tracker_aggregator_worker.worker import MAX_CATCHUP

    now = datetime(2026, 5, 28, 12, tzinfo=UTC)
    await _set_last_run_at(pg_engine, now - timedelta(days=10))
    result = await run_once(pg_engine, now=now, lookback=timedelta(hours=2))
    assert result.window_start >= now - MAX_CATCHUP
