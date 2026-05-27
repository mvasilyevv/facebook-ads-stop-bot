# -*- coding: utf-8 -*-
"""E2E cross-cutting сценарий: observer pipeline → outbox → disable worker.

Сшивка двух доменов: `core/observer/pipeline.py` создаёт alert_state=stop_sent
и пишет outbox-запись (task_type='disable'). После этого
`core/tasks/toggle_executor.execute_one_toggle_task` подхватывает её через
claim_next_task и доводит до task_queue.status='succeeded'.

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

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow
from core.tasks.toggle_executor import execute_one_toggle_task


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


class _RecordingGate:
    """Fake ToggleGate — записывает все вызовы + программируемый ответ.

    Симулирует gRPC-клиента к browser-agent: на проде делает реальный клик
    в Ads Manager, в тестах — просто пишет вызов в self.calls.
    """

    def __init__(self, *, succeed: bool = True, raise_exc: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._succeed = succeed
        self._raise_exc = raise_exc

    async def toggle_ad(self, fb_ad_id: str, target_state: bool = True) -> dict[str, Any]:
        self.calls.append({"fb_ad_id": fb_ad_id, "target_state": target_state})
        if self._raise_exc is not None:
            raise self._raise_exc
        return {
            "success": self._succeed,
            "final_state": "true" if target_state else "false",
        }


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


# E2E: scan → FSM stop_sent → outbox disable → toggle gate → task_queue.succeeded
@pytest.mark.asyncio
async def test_full_cycle_observer_to_disable_success(
    pg_engine,
    offer_e2e,
) -> None:
    fb_ad_id = f"230011{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_e2e["code"], fb_ad_id=fb_ad_id)

    # Шаг 1: observer pipeline отрабатывает scan-цикл
    result = await process_scan_rows(pg_engine, rows=[row], scan_id=999)
    assert result.rows_with_offer == 1
    # FSM должен сразу попасть в stop_sent (fast-stop по spend без deposits)
    assert result.alerts_stop >= 1
    assert result.disable_tasks_created == 1

    # Шаг 2: проверяем что в БД действительно создан outbox-task на disable
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
                    "FROM task_queue WHERE task_type = 'disable' LIMIT 1"
                )
            )
        ).first()

    assert state_row[0] == "stop_sent"
    assert state_row[1] == "stop"
    open_token = state_row[2]
    assert open_token is not None

    assert task_row is not None
    initial_task_id = task_row[0]
    assert task_row[1] == "disable"
    assert task_row[2] == "pending"
    assert task_row[3]["fb_ad_id"] == fb_ad_id
    assert task_row[4] == "bot_auto_stop"

    # Шаг 3: toggle worker подхватывает задачу
    gate = _RecordingGate(succeed=True)
    outcome = await execute_one_toggle_task(pg_engine, task_type="disable", gate=gate)
    assert outcome == "succeeded"

    # Шаг 4: gate реально был вызван с target_state=False для нашего ad
    assert len(gate.calls) == 1
    assert gate.calls[0]["fb_ad_id"] == fb_ad_id
    assert gate.calls[0]["target_state"] is False

    # Шаг 5: task_queue запись финализирована
    async with pg_engine.connect() as conn:
        final_row = (
            await conn.execute(
                text("SELECT status, result, completed_at FROM task_queue WHERE id = :i"),
                {"i": initial_task_id},
            )
        ).first()
    assert final_row[0] == "succeeded"
    assert final_row[1]["final_state"] == "false"
    assert final_row[2] is not None

    # Шаг 6: FSM-синхронизация — после успешного disable ad_alert_state должен
    # перейти из stop_sent в disabled (исправление техдолга из CLAUDE.md).
    async with pg_engine.connect() as conn:
        fsm_after = (
            await conn.execute(
                text("SELECT alert_state FROM ad_alert_state LIMIT 1"),
            )
        ).first()
    assert fsm_after[0] == "disabled"


# E2E: повторный scan того же STOP-row → НЕ создаёт вторую disable task
@pytest.mark.asyncio
async def test_idempotency_repeated_scan_does_not_duplicate_disable(
    pg_engine,
    offer_e2e,
) -> None:
    fb_ad_id = f"230012{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_e2e["code"], fb_ad_id=fb_ad_id)

    # Первый scan-цикл — создаёт STOP + disable task
    await process_scan_rows(pg_engine, rows=[row], scan_id=1)
    # Второй scan-цикл с теми же данными — FSM stop_sent → stop_sent без emit
    await process_scan_rows(pg_engine, rows=[row], scan_id=2)
    # Третий — на всякий случай ещё раз
    await process_scan_rows(pg_engine, rows=[row], scan_id=3)

    async with pg_engine.connect() as conn:
        n_tasks = (
            await conn.execute(text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'disable'"))
        ).scalar()
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
        n_metrics = (await conn.execute(text("SELECT COUNT(*) FROM ad_metrics"))).scalar()

    # idempotency_key = "auto:disable:{fb_ad_id}:{open_state_token}" — один открытый
    # инцидент = одна задача, сколько бы раз scan не прошёл
    assert n_tasks == 1
    # alert_events создаётся только при emit_alert=True; для stop_sent → stop_sent
    # FSM не эмитит → 1 запись (от первого цикла)
    assert n_alerts == 1
    # ad_metrics UNIQUE (ad_id, cycle_ts); cycle_ts разный — 3 записи
    assert n_metrics == 3


# E2E: после disable исполнения повторный scan с теми же данными не создаст
# новый disable task (open_state_token не сменился, alert_state stop_sent остаётся).
@pytest.mark.asyncio
async def test_after_disable_succeeds_no_new_task_on_same_incident(
    pg_engine,
    offer_e2e,
) -> None:
    fb_ad_id = f"230013{uuid.uuid4().hex[:6]}"
    row = _stop_row(code=offer_e2e["code"], fb_ad_id=fb_ad_id)

    # Полный цикл: scan → outbox → toggle succeeded
    await process_scan_rows(pg_engine, rows=[row], scan_id=10)
    gate = _RecordingGate(succeed=True)
    outcome = await execute_one_toggle_task(pg_engine, task_type="disable", gate=gate)
    assert outcome == "succeeded"

    # Повторный scan с теми же STOP-метриками: FSM stop_sent → stop_sent без emit,
    # open_state_token тот же → idempotency_key совпадёт → ON CONFLICT DO NOTHING.
    await process_scan_rows(pg_engine, rows=[row], scan_id=11)

    async with pg_engine.connect() as conn:
        statuses = [
            r[0]
            for r in (
                await conn.execute(
                    text("SELECT status FROM task_queue WHERE task_type = 'disable'")
                )
            ).all()
        ]
    # Одна succeeded запись, никаких новых pending после disable
    assert statuses == ["succeeded"]


# E2E: восстановление метрик после STOP → FSM stop_sent → normal (без emit),
# но disable task всё ещё succeeded (исполнена).
@pytest.mark.asyncio
async def test_recovery_after_stop_resets_fsm_no_new_alert(
    pg_engine,
    offer_e2e,
) -> None:
    fb_ad_id = f"230014{uuid.uuid4().hex[:6]}"
    stop_row = _stop_row(code=offer_e2e["code"], fb_ad_id=fb_ad_id)

    # Сначала STOP
    await process_scan_rows(pg_engine, rows=[stop_row], scan_id=20)

    # Полная воронка с deposits — никаких stop/warning-правил не должно сработать
    good_row = ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=stop_row.campaign_name,
        adset_name=stop_row.adset_name,
        ad_name=stop_row.ad_name,
        delivery_status="ACTIVE",
        spend=Decimal("2.0"),
        leads=10,
        registrations=5,
        deposits=2,
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
