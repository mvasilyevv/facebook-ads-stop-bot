# -*- coding: utf-8 -*-
"""E2E cross-cutting сценарий: observer pipeline → outbox → meta_api_worker.

Сшивка двух доменов: `core/observer/pipeline.py` создаёт alert_state=stop_sent
и пишет outbox-запись (task_type='meta_api_mutation', mutation_kind='pause_ad').
После этого `apps/meta_api_worker.process_one_task` подхватывает её через
claim_pending_task и доводит до task_queue.status='succeeded'.

Покрывает:
1. Полный жизненный цикл одного STOP-инцидента.
2. Идемпотентность: повторный scan той же строки НЕ создаёт вторую disable task
   (`idempotency_key` через open_state_token).
3. Поведение FSM при повторном scan'е stop_sent — alert_state остаётся,
   а task_queue не дублируется.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

import apps.meta_api_worker.main as worker_main
from apps.meta_api_worker.main import process_one_task
from core.meta_api.queue import claim_pending_task
from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow


@pytest_asyncio.fixture
async def clean_e2e_tables(pg_engine):
    """Чистит все таблицы пайплайна до и после теста.

    catalog → metrics → FSM → outbox; восстанавливаем чистый старт.
    """

    async def _truncate():
        async with pg_engine.begin() as conn:
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
async def offer_e2e(pg_engine, clean_e2e_tables):
    """Оффер с CPA=10 USD — порог для fast-stop при отсутствии deposits."""
    offer_id = uuid.uuid4()
    code = f"E2E{uuid.uuid4().hex[:4].upper()}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, TRUE)"),
            {"i": offer_id, "c": code, "n": f"E2E offer {code}"},
        )
        await conn.execute(
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:oid, :cpa)"),
            {"oid": offer_id, "cpa": Decimal("10.00")},
        )
    return {"offer_id": offer_id, "code": code}


def _stop_row(*, code: str, fb_ad_id: str) -> ScannedAdRow:
    """ScannedAdRow с метриками которые FSM должен оценить как STOP.

    spend=$25, deposits=0, cost_per_lead=None — fast-stop правило сработает,
    т.к. spend сильно превышает CPA-порог (=10) при нулевой воронке.
    """
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=f"{code} | KE | MV | promo",
        adset_name="EQ_KE",
        ad_name="Aviator001",
        delivery_status="ACTIVE",
        spend=Decimal("25.00"),
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


# E2E: scan → FSM stop_sent → outbox pause_ad mutation → process_one_task → task_queue.succeeded → FSM disabled
@pytest.mark.asyncio
async def test_full_cycle_observer_to_disable_success(
    pg_engine,
    offer_e2e,
    monkeypatch,
) -> None:
    fb_ad_id = f"230011{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_e2e["code"], fb_ad_id=fb_ad_id)

    # Шаг 1: observer pipeline отрабатывает scan-цикл
    result = await process_scan_rows(pg_engine, rows=[row], scan_id=999)
    assert result.rows_with_offer == 1
    # FSM должен сразу попасть в stop_sent (fast-stop по spend без deposits)
    assert result.alerts_stop >= 1
    assert result.disable_tasks_created == 1

    # Шаг 2: проверяем что в БД создана meta_api_mutation pause_ad задача
    async with pg_engine.connect() as conn:
        state_row = (
            await conn.execute(
                text(
                    "SELECT alert_state, current_stage, open_state_token "
                    "FROM ad_alert_state LIMIT 1"
                )
            )
        ).first()
        task_row = (
            await conn.execute(
                text(
                    "SELECT id, task_type, status, payload, requested_by "
                    "FROM task_queue WHERE task_type = 'meta_api_mutation' LIMIT 1"
                )
            )
        ).first()

    assert state_row[0] == "stop_sent"
    assert state_row[1] == "stop"
    open_token = state_row[2]
    assert open_token is not None

    assert task_row is not None
    initial_task_id = task_row[0]
    assert task_row[1] == "meta_api_mutation"
    assert task_row[2] == "pending"
    assert task_row[3]["target_id"] == fb_ad_id
    assert task_row[3]["mutation_kind"] == "pause_ad"
    assert task_row[4] == "bot_auto_stop"

    # Шаг 3: meta_api_worker забирает задачу и исполняет через fake dispatch_mutation
    fake_result: dict[str, Any] = {
        "success": True,
        "graph_response": {"ok": True},
        "modified_ids": [fb_ad_id],
    }

    async def _fake_dispatch(client, payload):
        return fake_result

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)

    claim = await claim_pending_task(pg_engine)
    assert not claim.queue_empty
    assert claim.task is not None
    assert claim.task.id == initial_task_id

    fake_client = AsyncMock()
    await process_one_task(pg_engine, claim.task, client=fake_client)

    # Шаг 4: task_queue запись финализирована
    async with pg_engine.connect() as conn:
        final_row = (
            await conn.execute(
                text("SELECT status, result, completed_at FROM task_queue WHERE id = :i"),
                {"i": initial_task_id},
            )
        ).first()
    assert final_row[0] == "succeeded"
    assert final_row[1]["success"] is True
    assert final_row[2] is not None

    # Шаг 5: FSM-синхронизация — после успешного pause_ad ad_alert_state → 'disabled'
    async with pg_engine.connect() as conn:
        fsm_after = (
            await conn.execute(
                text("SELECT alert_state FROM ad_alert_state LIMIT 1"),
            )
        ).first()
    assert fsm_after[0] == "disabled"


# E2E: повторный scan того же STOP-row → НЕ создаёт вторую meta_api_mutation задачу
@pytest.mark.asyncio
async def test_idempotency_repeated_scan_does_not_duplicate_disable(
    pg_engine,
    offer_e2e,
) -> None:
    fb_ad_id = f"230012{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_e2e["code"], fb_ad_id=fb_ad_id)

    # Первый scan-цикл — создаёт STOP + pause_ad mutation task
    await process_scan_rows(pg_engine, rows=[row], scan_id=1)
    # Второй scan-цикл с теми же данными — FSM stop_sent → stop_sent без emit
    await process_scan_rows(pg_engine, rows=[row], scan_id=2)
    # Третий — на всякий случай ещё раз
    await process_scan_rows(pg_engine, rows=[row], scan_id=3)

    async with pg_engine.connect() as conn:
        n_tasks = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM task_queue "
                    "WHERE task_type = 'meta_api_mutation' "
                    "AND payload->>'mutation_kind' = 'pause_ad'"
                )
            )
        ).scalar()
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
        n_metrics = (await conn.execute(text("SELECT COUNT(*) FROM ad_metrics"))).scalar()

    # idempotency_key = "auto:pause_ad:{fb_ad_id}:{open_state_token}" — один открытый
    # инцидент = одна задача, сколько бы раз scan не прошёл
    assert n_tasks == 1
    # alert_events создаётся только при emit_alert=True; для stop_sent → stop_sent
    # FSM не эмитит → 1 запись (от первого цикла)
    assert n_alerts == 1
    # ad_metrics UNIQUE (ad_id, cycle_ts); cycle_ts разный — 3 записи
    assert n_metrics == 3


# E2E: после pause_ad succeeded повторный scan не создаёт новую задачу на тот же инцидент
@pytest.mark.asyncio
async def test_after_disable_succeeds_no_new_task_on_same_incident(
    pg_engine,
    offer_e2e,
    monkeypatch,
) -> None:
    fb_ad_id = f"230013{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_e2e["code"], fb_ad_id=fb_ad_id)

    # Полный цикл: scan → outbox → meta_api_worker succeeded
    await process_scan_rows(pg_engine, rows=[row], scan_id=10)

    async def _fake_dispatch(client, payload):
        return {"success": True, "graph_response": {"ok": True}}

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)

    claim = await claim_pending_task(pg_engine)
    assert claim.task is not None
    await process_one_task(pg_engine, claim.task, client=AsyncMock())

    # Повторный scan: FSM stop_sent → stop_sent без emit,
    # open_state_token тот же → idempotency_key совпадёт → ON CONFLICT DO NOTHING.
    await process_scan_rows(pg_engine, rows=[row], scan_id=11)

    async with pg_engine.connect() as conn:
        statuses = [
            r[0]
            for r in (
                await conn.execute(
                    text(
                        "SELECT status FROM task_queue "
                        "WHERE task_type = 'meta_api_mutation' "
                        "AND payload->>'mutation_kind' = 'pause_ad'"
                    )
                )
            ).all()
        ]
    # Одна succeeded запись, никаких новых pending после disable
    assert statuses == ["succeeded"]


# E2E: восстановление метрик после STOP → FSM stop_sent → normal (без emit),
# но pause_ad task всё ещё succeeded (исполнена).
@pytest.mark.asyncio
async def test_recovery_after_stop_resets_fsm_no_new_alert(
    pg_engine,
    offer_e2e,
) -> None:
    fb_ad_id = f"230014{uuid.uuid4().hex[:6]}"
    stop_row = _stop_row(code=offer_e2e["code"], fb_ad_id=fb_ad_id)

    # Сначала STOP
    await process_scan_rows(pg_engine, rows=[stop_row], scan_id=20)

    # Депозиты теперь ТОЛЬКО из AdSet.pro → сидируем 2 ftd-события трекера для этого ад'а.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO adsetpro_postback_events
                    (received_at, click_id, fb_ad_id, event_type, revenue, currency,
                     raw_json, signature_valid, is_duplicate)
                VALUES (now(), :c1, :fb, 'ftd', 10, 'USD', '{}'::jsonb, true, false),
                       (now(), :c2, :fb, 'ftd', 10, 'USD', '{}'::jsonb, true, false)
                """
            ),
            {"fb": fb_ad_id, "c1": f"{fb_ad_id}-d1", "c2": f"{fb_ad_id}-d2"},
        )

    # Полная воронка + депозиты от AdSet.pro — никаких stop/warning-правил не должно сработать
    good_row = ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=stop_row.campaign_name,
        adset_name=stop_row.adset_name,
        ad_name=stop_row.ad_name,
        delivery_status="ACTIVE",
        spend=Decimal("2.0"),
        leads=10,
        registrations=5,
        deposits=0,  # Meta-депозиты больше не источник — депозит приходит из трекера
        cpc=Decimal("0.05"),
        ctr=Decimal("3.0"),
    )
    await process_scan_rows(pg_engine, rows=[good_row], scan_id=21)

    async with pg_engine.connect() as conn:
        state = (
            await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))
        ).scalar()
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()

    # FSM деэскалировал stop_sent → normal (восстановление)
    assert state == "normal"
    # alert_events для recovery не эмитятся (emit_alert=False в FSM)
    assert n_alerts == 1
