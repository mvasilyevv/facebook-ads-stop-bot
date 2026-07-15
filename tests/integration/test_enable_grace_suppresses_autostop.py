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
            for t in (
                "task_queue",
                "alert_events",
                "tracker_click_state",
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
    grace_map = {
        fb_ad_id: EnableGrace(
            until=datetime.now(timezone.utc) + timedelta(hours=1),
            spend_cap=Decimal("100.00"),  # spend=25 < 100 — кап не выбран
        )
    }

    result = await process_scan_rows(
        pg_engine,
        rows=[_stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id)],
        scan_id=1001,
        enable_grace_map=grace_map,
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
    grace_map = {
        fb_ad_id: EnableGrace(
            until=datetime.now(timezone.utc) - timedelta(seconds=5),
            spend_cap=Decimal("100.00"),
        )
    }

    result = await process_scan_rows(
        pg_engine,
        rows=[_stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id)],
        scan_id=1002,
        enable_grace_map=grace_map,
    )

    assert result.rows_grace_suppressed == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    assert state == "stop_sent"
    assert tasks == 1


# Спенд достиг капа (~1×CPA): grace жив по времени, но стоп срабатывает —
# «держать до цены лида» закончилось, дальше решают обычные правила
async def test_spend_cap_reached_stop_fires(pg_engine, grace_offer) -> None:
    fb_ad_id = f"7788{uuid.uuid4().hex[:8]}"
    grace_map = {
        fb_ad_id: EnableGrace(
            until=datetime.now(timezone.utc) + timedelta(hours=1),
            spend_cap=Decimal("10.00"),  # кап = 1×CPA; spend=25 ≥ 10
        )
    }

    result = await process_scan_rows(
        pg_engine,
        rows=[_stop_row(code=grace_offer["code"], fb_ad_id=fb_ad_id)],
        scan_id=1003,
        enable_grace_map=grace_map,
    )

    assert result.rows_grace_suppressed == 0
    state, tasks = await _fsm_and_tasks(pg_engine)
    assert state == "stop_sent"
    assert tasks == 1
