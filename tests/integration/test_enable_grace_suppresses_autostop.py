# -*- coding: utf-8 -*-
"""Integration: grace «держать до цены лида» подавляет авто-стоп в observer pipeline.

Money-контракт кейса куратора:
1. Под активным grace STOP-правило НЕ создаёт pause-задачу и НЕ шлёт алерт,
   FSM остаётся в normal (не застревает в stop_sent без задачи).
2. Grace истёк по времени → тот же скан штатно даёт stop_sent + pause_ad задачу.
3. Спенд достиг капа (~1×CPA) → grace не действует, стоп срабатывает.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.usefixtures("known_test_cabinet_timezones"),
]


@pytest_asyncio.fixture
async def clean_grace_tables(pg_engine):
    """Чистит таблицы пайплайна до и после теста (паттерн test_e2e_observer_to_disable)."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            # Список таблиц — только те, что существуют в ЗАКОММИЧЕННОЙ схеме:
            # tracker_click_state здесь нет (живёт в незакоммиченной tracker-миграции
            # параллельной работы; в CI-базе её нет — DELETE падал UndefinedTableError).
            for t in (
                "task_queue",
                "alert_events",
                "ad_metrics",
                "ad_alert_state",
                "fb_ads",
                "fb_adsets",
                "fb_campaigns",
                "offer_rules",
                "offers",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def grace_offer(pg_engine, clean_grace_tables):
    """Оффер с CPA=10: spend=25 без воронки гарантированно даёт STOP."""
    offer_id = uuid.uuid4()
    code = f"GR{uuid.uuid4().hex[:4].upper()}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, TRUE)"),
            {"i": offer_id, "c": code, "n": f"Grace offer {code}"},
        )
        await conn.execute(
            text(
                "INSERT INTO offer_rules (offer_id, cpa_threshold, currency) "
                "VALUES (:oid, :cpa, 'USD')"
            ),
            {"oid": offer_id, "cpa": Decimal("10.00")},
        )
    return {"offer_id": offer_id, "code": code}


def _stop_row(*, code: str, fb_ad_id: str, spend: str = "25.00") -> ScannedAdRow:
    """Строка с метриками, которые evaluator оценивает как STOP (spend ≫ CPA, депозитов нет)."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_id=f"9{fb_ad_id}",
        adset_id=f"8{fb_ad_id}",
        campaign_name=f"{code} | KE | MV | promo",
        adset_name="EQ_KE",
        ad_name="Curator001",
        delivery_status="ACTIVE",
        spend=Decimal(spend),
        budget="$30",
        reach=2000,
        impressions=4000,
        clicks=120,
        cpc=Decimal("0.21"),
        ctr=Decimal("3.0"),
        cpm=Decimal("6.0"),
        leads=0,
        registrations=0,
        deposits=0,
        outbound_clicks=80,
        landing_page_views=40,
    )


async def _fsm_and_tasks(pg_engine) -> tuple[str | None, int]:
    """(alert_state | None, число pause-задач в task_queue)."""
    async with pg_engine.connect() as conn:
        state_row = (
            await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))
        ).first()
        tasks = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'meta_api_mutation'")
            )
        ).scalar()
    return (str(state_row[0]) if state_row else None, int(tasks or 0))


async def _seed_durable_grace(
    pg_engine,
    *,
    row: ScannedAdRow,
    until: datetime,
    spend_cap: Decimal,
    baseline_spend: Decimal = Decimal("0"),
) -> datetime:
    """Create the catalog row, then persist grace exactly as the worker does."""
    cycle_ts = datetime.now(timezone.utc)
    seed_result = await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[
            replace(
                row,
                spend=Decimal("0"),
                cpc=Decimal("0.01"),
                deposits=1,
                leads=1,
                registrations=1,
            )
        ],
        scan_id=1000,
        cycle_ts=cycle_ts - timedelta(seconds=1),
    )
    assert seed_result.alerts_stop == 0
    assert seed_result.alerts_warning == 0
    seed_state, seed_tasks = await _fsm_and_tasks(pg_engine)
    assert seed_state in (None, "normal")
    assert seed_tasks == 0
    cabinet_day_start = cycle_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state (
                    ad_id,
                    alert_state,
                    enable_grace_until,
                    enable_grace_spend_cap,
                    enable_grace_baseline_spend,
                    enable_grace_cabinet_day_start,
                    enable_grace_currency,
                    enable_grace_currency_exponent
                )
                SELECT id, 'normal', :until, :cap, :baseline, :day_start, 'USD', 2
                FROM fb_ads
                WHERE fb_ad_id = :fb_ad_id
                ON CONFLICT (ad_id) DO UPDATE
                SET enable_grace_until = EXCLUDED.enable_grace_until,
                    enable_grace_spend_cap = EXCLUDED.enable_grace_spend_cap,
                    enable_grace_baseline_spend = EXCLUDED.enable_grace_baseline_spend,
                    enable_grace_cabinet_day_start = EXCLUDED.enable_grace_cabinet_day_start,
                    enable_grace_currency = EXCLUDED.enable_grace_currency,
                    enable_grace_currency_exponent = EXCLUDED.enable_grace_currency_exponent
                """
            ),
            {
                "until": until,
                "cap": spend_cap,
                "baseline": baseline_spend,
                "day_start": cabinet_day_start,
                "fb_ad_id": row.fb_ad_id,
            },
        )
    return cycle_ts


# Активный grace: STOP-метрики есть, но ни алерта, ни pause-задачи, FSM=normal
async def test_active_grace_suppresses_stop(pg_engine, grace_offer) -> None:
    fb_ad_id = f"7788{uuid.uuid4().int % 100_000_000:08d}"
    row = _stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id)
    now = datetime.now(timezone.utc)
    cycle_ts = await _seed_durable_grace(
        pg_engine,
        row=row,
        until=now + timedelta(hours=1),
        spend_cap=Decimal("100.00"),
    )

    result = await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[row],
        scan_id=1001,
        cycle_ts=cycle_ts,
    )

    assert result.rows_grace_suppressed == 1
    assert result.alerts_stop == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    # FSM не ушёл в stop_sent (иначе после окна grace повторный STOP не сработал бы)
    assert state in (None, "normal")
    assert tasks == 0


# Grace истёк по времени: тот же скан штатно даёт stop_sent + pause_ad задачу
async def test_expired_grace_stop_fires(pg_engine, grace_offer) -> None:
    fb_ad_id = f"7788{uuid.uuid4().int % 100_000_000:08d}"
    row = _stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id)
    now = datetime.now(timezone.utc)
    cycle_ts = await _seed_durable_grace(
        pg_engine,
        row=row,
        until=now - timedelta(seconds=5),
        spend_cap=Decimal("100.00"),
    )

    result = await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[row],
        scan_id=1002,
        cycle_ts=cycle_ts,
    )

    assert result.rows_grace_suppressed == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    assert state == "stop_sent"
    assert tasks == 1


# Спенд достиг капа (~1×CPA): grace жив по времени, но стоп срабатывает —
# «держать до цены лида» закончилось, дальше решают обычные правила
async def test_spend_cap_reached_stop_fires(pg_engine, grace_offer) -> None:
    fb_ad_id = f"7788{uuid.uuid4().int % 100_000_000:08d}"
    row = _stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id)
    now = datetime.now(timezone.utc)
    cycle_ts = await _seed_durable_grace(
        pg_engine,
        row=row,
        until=now + timedelta(hours=1),
        spend_cap=Decimal("10.00"),
    )

    result = await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[row],
        scan_id=1003,
        cycle_ts=cycle_ts,
    )

    assert result.rows_grace_suppressed == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    assert state == "stop_sent"
    assert tasks == 1


async def test_currency_change_clears_grace_and_blocks_non_usd_autopause(
    pg_engine,
    grace_offer,
) -> None:
    fb_ad_id = f"7788{uuid.uuid4().int % 100_000_000:08d}"
    row = _stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id)
    now = datetime.now(timezone.utc)
    cycle_ts = await _seed_durable_grace(
        pg_engine,
        row=row,
        until=now + timedelta(hours=1),
        spend_cap=Decimal("100.00"),
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE meta_account_snapshot
                SET currency = 'KWD',
                    currency_observed_at = NOW()
                WHERE account_id = '123'
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE offer_rules
                SET currency = 'KWD',
                    cpa_threshold = 10.000
                WHERE offer_id = :offer_id
                """
            ),
            {"offer_id": grace_offer["offer_id"]},
        )

    result = await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[row],
        scan_id=1004,
        cycle_ts=cycle_ts,
    )

    assert result.row_errors == [f"{fb_ad_id}:CommandPreconditionError"]
    assert result.rows_grace_suppressed == 0
    assert result.alerts_stop == 0
    assert result.disable_tasks_created == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    assert state in (None, "normal")
    assert tasks == 0
    async with pg_engine.connect() as conn:
        cleared = (
            await conn.execute(
                text(
                    """
                    SELECT enable_grace_until,
                           enable_grace_spend_cap,
                           enable_grace_baseline_spend,
                           enable_grace_cabinet_day_start,
                           enable_grace_currency,
                           enable_grace_currency_exponent
                    FROM ad_alert_state
                    LIMIT 1
                    """
                )
            )
        ).one()
        false_stop_side_effects = (
            await conn.execute(
                text(
                    """
                    SELECT
                      (
                        SELECT COUNT(*)
                        FROM alert_events AS event
                        JOIN fb_ads AS ad ON ad.id = event.ad_id
                        WHERE ad.fb_ad_id = :fb_ad_id
                      ) AS alert_events,
                      (
                        SELECT COUNT(*)
                        FROM incidents
                        WHERE resource_type = 'ad'
                          AND resource_id = :fb_ad_id
                      ) AS incidents,
                      (
                        SELECT COUNT(*)
                        FROM notification_events AS event
                        JOIN incidents AS incident ON incident.id = event.incident_id
                        WHERE incident.resource_type = 'ad'
                          AND incident.resource_id = :fb_ad_id
                      ) AS notifications
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
        ).one()
    assert tuple(cleared) == (None, None, None, None, None, None)
    assert tuple(false_stop_side_effects) == (0, 0, 0)
