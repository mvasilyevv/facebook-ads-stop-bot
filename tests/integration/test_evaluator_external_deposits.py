# -*- coding: utf-8 -*-
"""Интеграционный: evaluator + RuleContext.external_deposits.

Цель: подтвердить, что AdSet.pro-постбэк защищает «жирный» ad от STOP-правила,
когда Meta Ads Manager ещё не видит депозит. Использует реальный Postgres,
ingest_postback + load_external_deposits_batch + evaluate_stop_rules.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.adset_pro import PostbackEvent
from core.adset_pro.ingest import ingest_postback
from core.adset_pro.processing import (
    TrackerLeaseLostError,
    claim_event_tasks,
    mark_task_retry,
    process_event_task,
)
from core.adset_pro.queries import load_external_deposits_batch
from core.domain import AlertStage
from core.rules.evaluator import evaluate_stop_rules
from core.rules.types import RuleContext
from core.scanner.models import ScannedAdRow


def _tracker_window() -> dict[str, datetime]:
    now = datetime.now(UTC)
    return {
        "window_start": now - timedelta(days=1),
        "window_end": now + timedelta(days=1),
    }


@pytest_asyncio.fixture
async def clean_adsetpro_events(pg_engine):
    """TRUNCATE adsetpro_postback_events до и после теста."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE task_type='tracker_event_process'")
            )
            await conn.execute(text("DELETE FROM tracker_click_state"))
            await conn.execute(text("DELETE FROM adsetpro_postback_events"))

    await _truncate()
    yield
    await _truncate()


async def _get_fb_ad_id_string(pg_engine, ad_uuid: uuid.UUID) -> str:
    async with pg_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT fb_ad_id FROM fb_ads WHERE id = :i"),
            {"i": ad_uuid},
        )
        return result.scalar_one()


def _make_row(
    *,
    fb_ad_id: str,
    spend: Decimal,
    clicks: int = 200,
    leads: int = 10,
    registrations: int = 3,
    deposits: int = 0,
    cost_per_lead: Decimal | None = None,
    cost_per_registration: Decimal | None = None,
) -> ScannedAdRow:
    """Минимально-валидный ScannedAdRow для проверки правила spend_with_dep/spend_no_dep."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_id=f"9{fb_ad_id}",
        adset_id=f"8{fb_ad_id}",
        campaign_name="CAMP",
        adset_name="ADSET",
        ad_name=f"AD_{fb_ad_id}",
        delivery_status="active",
        spend=spend,
        reach=1000,
        impressions=5000,
        clicks=clicks,
        cpc=Decimal("0.5"),
        ctr=Decimal("4"),
        cpm=Decimal("10"),
        frequency=Decimal("1.2"),
        leads=leads,
        cost_per_lead=cost_per_lead,
        registrations=registrations,
        cost_per_registration=cost_per_registration,
        deposits=deposits,
        outbound_clicks=clicks,
        outbound_ctr=Decimal("3"),
        landing_page_views=clicks,
        cost_per_landing_page_view=Decimal("0.5"),
    )


# Сценарий: ад без депов и с высоким расходом без депозита → STOP.
@pytest.mark.asyncio
async def test_ad_without_deposits_hits_stop(pg_engine, fb_ad_fixture, clean_adsetpro_events):
    fb_ad_id = await _get_fb_ad_id_string(pg_engine, fb_ad_fixture.ad_id)
    # spend = 100% от CPA, реги в норме (CPA $50, реги по $15 при CPR ~16%).
    row = _make_row(
        fb_ad_id=fb_ad_id,
        spend=Decimal("50"),  # 100% от CPA $50 → > spend_no_dep_to_percent 70%
        cost_per_lead=Decimal("0.5"),
        cost_per_registration=Decimal("5"),
        deposits=0,
    )
    ctx = RuleContext(
        currency="USD",
        currency_exponent=2,
        cpa_amount=Decimal("50"),
        warning_percent_of_stop=Decimal("80"),
        external_deposits=0,
    )
    evaluation = evaluate_stop_rules(row, ctx)
    assert evaluation.stage == AlertStage.STOP
    assert "spend_no_dep_range" in evaluation.stop_rule_codes


# Сценарий: тот же ад, но AdSet.pro прислал registration+FTD одного click_id → STOP не срабатывает
# (external_deposits >= 1 переводит в deposit_stage, и при spend=100% всё ещё
# далеко от stop_with_dep диапазона 70-90%).
@pytest.mark.asyncio
async def test_external_confirmed_pair_protects_from_stop(
    pg_engine, fb_ad_fixture, clean_adsetpro_events
):
    fb_ad_id = await _get_fb_ad_id_string(pg_engine, fb_ad_fixture.ad_id)
    # CPA=$50. CPR=$3 — в норме (cpr_stop=$8). spend=$20 = 40% от CPA:
    # - spend_no_dep_range effective = 40-56% от CPA → STOP при 40%.
    # - spend_with_dep_range effective = 56-72% → не сработает при 40%.
    # Таким образом external_deposits переключает на deposit_stage и защищает от STOP.
    row = _make_row(
        fb_ad_id=fb_ad_id,
        spend=Decimal("20"),
        cost_per_lead=Decimal("0.3"),
        cost_per_registration=Decimal("3"),
        deposits=0,
    )
    # Шаг 1: без external_deposits объявление должно попасть в WARNING/STOP по spend_no_dep.
    ctx_no_ext = RuleContext(
        currency="USD",
        currency_exponent=2,
        cpa_amount=Decimal("50"),
        warning_percent_of_stop=Decimal("80"),
        external_deposits=0,
    )
    ev_no_ext = evaluate_stop_rules(row, ctx_no_ext)
    assert ev_no_ext.stage in (AlertStage.WARNING, AlertStage.STOP)
    assert "spend_no_dep_range" in (ev_no_ext.warning_rule_codes + ev_no_ext.stop_rule_codes)

    # Шаг 2: один FTD без registration не должен защищать от STOP.
    ftd = PostbackEvent(
        click_id="protect-1",
        fb_ad_id=fb_ad_id,
        event_type="ftd",
        revenue=Decimal("25"),
        currency="USD",
        received_at=datetime.now(UTC),
        raw={},
    )
    ingest = await ingest_postback(pg_engine, ftd)
    assert ingest.inserted is True
    claim = (await claim_event_tasks(pg_engine))[0]
    assert (await process_event_task(pg_engine, claim=claim)).processed is True

    counts = await load_external_deposits_batch(
        pg_engine, fb_ad_ids=[fb_ad_id], **_tracker_window()
    )
    assert counts.get(fb_ad_id, 0) == 0

    registration = PostbackEvent(
        click_id="protect-1",
        fb_ad_id=fb_ad_id,
        event_type="registration",
        revenue=Decimal("0"),
        currency="USD",
        received_at=datetime.now(UTC),
        raw={},
    )
    await ingest_postback(pg_engine, registration)
    claim = (await claim_event_tasks(pg_engine))[0]
    assert (await process_event_task(pg_engine, claim=claim)).processed is True
    counts = await load_external_deposits_batch(
        pg_engine, fb_ad_ids=[fb_ad_id], **_tracker_window()
    )
    assert counts.get(fb_ad_id, 0) >= 1

    # Шаг 3: с external_deposits=1 — переходим в deposit_stage; spend=60% < stop_with_dep 70%.
    ctx_with_ext = RuleContext(
        currency="USD",
        currency_exponent=2,
        cpa_amount=Decimal("50"),
        warning_percent_of_stop=Decimal("80"),
        external_deposits=counts[fb_ad_id],
    )
    ev_with_ext = evaluate_stop_rules(row, ctx_with_ext)
    # STOP не должен сработать (нет правил для spend < 70% при наличии депа).
    assert ev_with_ext.stage != AlertStage.STOP, (
        f"external_deposits должен был защитить от STOP, но stage={ev_with_ext.stage}, "
        f"codes={ev_with_ext.stop_rule_codes + ev_with_ext.warning_rule_codes}"
    )


@pytest.mark.asyncio
async def test_expired_tracker_generation_cannot_project_event_or_retry_task(
    pg_engine,
    fb_ad_fixture,
    clean_adsetpro_events,
) -> None:
    fb_ad_id = await _get_fb_ad_id_string(pg_engine, fb_ad_fixture.ad_id)
    event = PostbackEvent(
        click_id=f"expired-{uuid.uuid4().hex}",
        fb_ad_id=fb_ad_id,
        event_type="registration",
        revenue=Decimal("0"),
        currency="USD",
        received_at=datetime.now(UTC),
        raw={},
    )
    ingest = await ingest_postback(pg_engine, event)
    assert ingest.inserted is True
    claim = (await claim_event_tasks(pg_engine))[0]
    async with pg_engine.connect() as conn:
        event_before = (
            await conn.execute(
                text(
                    """
                    SELECT processed_at, attribution_status, attempt_count
                    FROM adsetpro_postback_events
                    WHERE id = :event_id
                    """
                ),
                {"event_id": ingest.event_id},
            )
        ).one()
        click_state_before = await conn.scalar(
            text("SELECT COUNT(*) FROM tracker_click_state WHERE click_id = :click_id"),
            {"click_id": event.click_id},
        )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET lease_expires_at = clock_timestamp() - interval '1 second'
                WHERE id = :task_id
                """
            ),
            {"task_id": claim.task_id},
        )

    with pytest.raises(TrackerLeaseLostError):
        await process_event_task(pg_engine, claim=claim)
    assert (
        await mark_task_retry(
            pg_engine,
            claim=claim,
            error="expired generation must not retry",
        )
        is False
    )

    async with pg_engine.connect() as conn:
        task = (
            await conn.execute(
                text(
                    """
                    SELECT status, lease_owner, lease_token, completed_at, result
                    FROM task_queue
                    WHERE id = :task_id
                    """
                ),
                {"task_id": claim.task_id},
            )
        ).one()
        persisted_event = (
            await conn.execute(
                text(
                    """
                    SELECT processed_at, attribution_status, attempt_count
                    FROM adsetpro_postback_events
                    WHERE id = :event_id
                    """
                ),
                {"event_id": ingest.event_id},
            )
        ).one()
        click_state_count = await conn.scalar(
            text("SELECT COUNT(*) FROM tracker_click_state WHERE click_id = :click_id"),
            {"click_id": event.click_id},
        )
    assert task.status == "running"
    assert task.lease_owner == claim.lease_owner
    assert task.lease_token == claim.lease_token
    assert task.completed_at is None
    assert task.result is None
    assert persisted_event == event_before
    assert click_state_count == click_state_before


# Сценарий: только дубль (is_duplicate=TRUE) в БД → load_external_deposits_batch не считает.
@pytest.mark.asyncio
async def test_duplicate_postbacks_do_not_count(pg_engine, fb_ad_fixture, clean_adsetpro_events):
    fb_ad_id = await _get_fb_ad_id_string(pg_engine, fb_ad_fixture.ad_id)
    # Вставляем сразу запись с is_duplicate=TRUE через прямой SQL — имитация прошлого дубля.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO adsetpro_postback_events
                    (received_at, click_id, fb_ad_id, event_type, revenue, currency,
                     raw_json, signature_valid, is_duplicate)
                VALUES (now(), 'dupe-1', :fb, 'ftd', 10, 'USD',
                        '{}'::jsonb, true, true)
                """
            ),
            {"fb": fb_ad_id},
        )

    counts = await load_external_deposits_batch(
        pg_engine, fb_ad_ids=[fb_ad_id], **_tracker_window()
    )
    # is_duplicate=TRUE исключается из счёта.
    assert counts.get(fb_ad_id, 0) == 0
