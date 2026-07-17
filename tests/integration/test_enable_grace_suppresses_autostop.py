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
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.observer.enable_grace import EnableGrace
from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow

pytestmark = pytest.mark.asyncio


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
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:oid, :cpa)"),
            {"oid": offer_id, "cpa": Decimal("10.00")},
        )
    return {"offer_id": offer_id, "code": code}


@pytest_asyncio.fixture
async def grace_offer_without_cpa(pg_engine, clean_grace_tables):
    """Активный оффер без CPA: grace обязан fail-close, не подавляя STOP."""
    offer_id = uuid.uuid4()
    code = f"GN{uuid.uuid4().hex[:4].upper()}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, TRUE)"),
            {"i": offer_id, "c": code, "n": f"No-CPA grace offer {code}"},
        )
    return {"offer_id": offer_id, "code": code}


def _stop_row(*, code: str, fb_ad_id: str, spend: str = "25.00") -> ScannedAdRow:
    """Строка с метриками, которые evaluator оценивает как STOP (spend ≫ CPA, депозитов нет)."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
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


# Активный grace: STOP-метрики есть, но ни алерта, ни pause-задачи, FSM=normal
async def test_active_grace_suppresses_stop(pg_engine, grace_offer) -> None:
    fb_ad_id = f"7788{uuid.uuid4().hex[:8]}"
    cycle_ts = datetime.now(timezone.utc)
    day_start = cycle_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    grace_map = {
        fb_ad_id: EnableGrace(
            until=cycle_ts + timedelta(hours=1),
            spend_cap=Decimal("10.00"),
            baseline_spend=Decimal("0.50"),
            cabinet_day_start=day_start,
        )
    }

    result = await process_scan_rows(
        pg_engine,
        rows=[_stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id, spend="9.00")],
        scan_id=1001,
        cycle_ts=cycle_ts,
        enable_grace_map=grace_map,
        tracker_day_start=day_start,
    )

    assert result.rows_grace_suppressed == 1
    assert result.alerts_stop == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    # FSM не ушёл в stop_sent (иначе после окна grace повторный STOP не сработал бы)
    assert state in (None, "normal")
    assert tasks == 0


# Grace истёк по времени: тот же скан штатно даёт stop_sent + pause_ad задачу
async def test_expired_grace_stop_fires(pg_engine, grace_offer) -> None:
    fb_ad_id = f"7788{uuid.uuid4().hex[:8]}"
    cycle_ts = datetime.now(timezone.utc)
    day_start = cycle_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    grace_map = {
        fb_ad_id: EnableGrace(
            until=cycle_ts - timedelta(seconds=5),
            spend_cap=Decimal("10.00"),
            baseline_spend=Decimal("0.50"),
            cabinet_day_start=day_start,
        )
    }

    result = await process_scan_rows(
        pg_engine,
        rows=[_stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id, spend="9.00")],
        scan_id=1002,
        cycle_ts=cycle_ts,
        enable_grace_map=grace_map,
        tracker_day_start=day_start,
    )

    assert result.rows_grace_suppressed == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    assert state == "stop_sent"
    assert tasks == 1


# Спенд достиг капа (~1×CPA): grace жив по времени, но стоп срабатывает —
# «держать до цены лида» закончилось, дальше решают обычные правила
async def test_spend_cap_reached_stop_fires(pg_engine, grace_offer) -> None:
    fb_ad_id = f"7788{uuid.uuid4().hex[:8]}"
    cycle_ts = datetime.now(timezone.utc)
    day_start = cycle_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    grace_map = {
        fb_ad_id: EnableGrace(
            until=cycle_ts + timedelta(hours=1),
            spend_cap=Decimal("10.00"),  # абсолютный cap = CPA; spend ровно на границе
            baseline_spend=Decimal("0.50"),
            cabinet_day_start=day_start,
        )
    }

    result = await process_scan_rows(
        pg_engine,
        rows=[_stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id, spend="10.00")],
        scan_id=1003,
        cycle_ts=cycle_ts,
        enable_grace_map=grace_map,
        tracker_day_start=day_start,
    )

    assert result.rows_grace_suppressed == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    assert state == "stop_sent"
    assert tasks == 1


# Даже валидный marker v2 не имеет права suppress без актуального CPA оффера.
async def test_current_cpa_missing_does_not_suppress_stop(
    pg_engine, grace_offer_without_cpa
) -> None:
    fb_ad_id = f"7788{uuid.uuid4().hex[:8]}"
    cycle_ts = datetime.now(timezone.utc)
    day_start = cycle_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    grace_map = {
        fb_ad_id: EnableGrace(
            until=cycle_ts + timedelta(hours=1),
            spend_cap=Decimal("200.00"),
            baseline_spend=Decimal("0.50"),
            cabinet_day_start=day_start,
        )
    }

    result = await process_scan_rows(
        pg_engine,
        rows=[
            _stop_row(
                code=grace_offer_without_cpa["code"],
                fb_ad_id=fb_ad_id,
                spend="100.00",
            )
        ],
        scan_id=1004,
        cycle_ts=cycle_ts,
        enable_grace_map=grace_map,
        tracker_day_start=day_start,
    )

    assert result.rows_grace_suppressed == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    assert state == "stop_sent"
    assert tasks == 1
