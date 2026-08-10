# -*- coding: utf-8 -*-
"""Integration e2e: meta_api_worker синхронизирует ad_alert_state после mutation (#39).

Закрывает money-пробел: при auto-stop через Marketing API FSM обязан перейти в
'disabled' (а при activate — в 'normal'), иначе застревает в 'stop_sent', хотя
объявление реально на паузе. Прогоняем полный process_one_task с мок-dispatch.
Требует реальный Postgres (pg_engine).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.meta_api_worker.main import process_one_task
from core.meta_api.queue import (
    claim_browser_ready_mutation_task,
    create_mutation_task,
)
from core.meta_api.schemas import MetaMutationPayload
from tests.integration.scan_evidence import begin_complete_test_scan, finish_complete_test_scan

pytestmark = pytest.mark.usefixtures(
    "fresh_browser_readiness",
    "known_test_cabinet_timezones",
)


@pytest_asyncio.fixture
async def ad_with_state(pg_engine: AsyncEngine):
    """offer→campaign→adset→ad + ad_alert_state. Возвращает (fb_ad_id, seed(state)).

    Teardown: чистит task_queue по fb_ad_id (idempotency_key LIKE) + offers cascade.
    """
    suffix = uuid.uuid4().hex[:8]
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    fb_ad_id = f"23007{uuid.uuid4().int % 10_000_000_000}"

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO observer_config (singleton_key, is_scanning_enabled) "
                "VALUES ('default', TRUE) "
                "ON CONFLICT (singleton_key) DO UPDATE SET is_scanning_enabled = TRUE"
            )
        )
        await conn.execute(
            text("INSERT INTO offers (id, code, name) VALUES (:i, :c, :n)"),
            {"i": offer_id, "c": f"FSY_{suffix}", "n": f"FSM-sync offer {suffix}"},
        )
        await conn.execute(
            text(
                "INSERT INTO offer_rules (offer_id, cpa_threshold, currency) "
                "VALUES (:offer_id, 10, 'USD')"
            ),
            {"offer_id": offer_id},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, campaign_name, offer_id, ad_account_id) VALUES (:i, :n, :o, '123')"
            ),
            {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"AD_{suffix}"},
        )

    async def _seed(initial_state: str) -> str:
        scan_id = await begin_complete_test_scan(pg_engine, account_id="123")
        stage = (
            "warning"
            if initial_state == "warning_sent"
            else ("stop" if initial_state in ("stop_sent", "claimed") else None)
        )
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM ad_alert_state WHERE ad_id = :a"), {"a": ad_id})
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (id, ad_id, cycle_ts, scan_id, currency, spend)
                    VALUES (gen_random_uuid(), :ad_id, now(), :scan_id, 'USD', 10)
                    ON CONFLICT (ad_id, cycle_ts) DO NOTHING
                    """
                ),
                {"ad_id": ad_id, "scan_id": scan_id},
            )
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_alert_state
                        (id, ad_id, alert_state, current_stage, open_state_token,
                         warning_rule_codes, stop_rule_codes, last_scan_id,
                         last_transition_at)
                    VALUES (:id, :a, :st, :stg, :tok,
                            '["w1"]'::jsonb, '["s1"]'::jsonb, :scan_id,
                            NOW() - INTERVAL '1 hour')
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "a": ad_id,
                    "st": initial_state,
                    "stg": stage,
                    "tok": uuid.uuid4(),
                    "scan_id": scan_id,
                },
            )
        await finish_complete_test_scan(pg_engine, scan_id=scan_id, rows_total=1)
        return fb_ad_id

    yield fb_ad_id, _seed

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM task_queue WHERE idempotency_key LIKE :p"), {"p": f"%{fb_ad_id}%"}
        )
        await conn.execute(text("DELETE FROM offers WHERE id = :i"), {"i": offer_id})
        await conn.execute(text("DELETE FROM observer_config WHERE singleton_key = 'default'"))


async def _read_alert_state(pg_engine: AsyncEngine, fb_ad_id: str) -> str:
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT s.alert_state FROM ad_alert_state s
                    JOIN fb_ads a ON a.id = s.ad_id WHERE a.fb_ad_id = :f
                    """
                ),
                {"f": fb_ad_id},
            )
        ).first()
    return row[0] if row else "<none>"


async def _run_one(
    pg_engine,
    payload: MetaMutationPayload,
    *,
    idem: str,
    requested_by: str,
    lane: str,
    monkeypatch,
) -> None:
    """create pending → claim → process_one_task с мок-success dispatch."""
    await create_mutation_task(
        pg_engine,
        payload=payload,
        requested_by=requested_by,
        status="pending",
        idempotency_key=idem,
    )
    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=(lane,))
    assert claim.task is not None

    async def _fake_dispatch(client, p):
        # For bulk_status_change, terminal projection must receive the exact IDs
        # confirmed by Meta rather than the synthetic queue target.
        if p.mutation_kind == "bulk_status_change":
            params = p.params or {}
            ad_ids = params.get("ad_ids") or []
            modified_ids = [str(x).strip() for x in ad_ids if str(x).strip()]
        else:
            modified_ids = [p.target_id]
        result = {
            "success": True,
            "graph_response": {"ok": True},
            "modified_ids": modified_ids,
        }
        if p.mutation_kind == "bulk_status_change":
            result.update(
                {
                    "succeeded": len(modified_ids),
                    "failed": 0,
                    "sub_results": [
                        {"id": object_id, "success": True, "code": 200}
                        for object_id in modified_ids
                    ],
                }
            )
        return result

    import apps.meta_api_worker.main as worker_main

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)
    await process_one_task(pg_engine, claim.task, client=AsyncMock())


# pause_ad success → ad_alert_state переходит stop_sent → disabled
@pytest.mark.asyncio
async def test_pause_ad_success_sets_disabled(pg_engine, ad_with_state, monkeypatch) -> None:
    fb_ad_id, seed = ad_with_state
    await seed("stop_sent")

    payload = MetaMutationPayload(
        mutation_kind="pause_ad", target_id=fb_ad_id, params={}, ad_account_id="123"
    )
    await _run_one(
        pg_engine,
        payload,
        idem=f"auto:pause_ad:{fb_ad_id}:t1",
        requested_by="bot_auto_stop",
        lane="money",
        monkeypatch=monkeypatch,
    )

    assert await _read_alert_state(pg_engine, fb_ad_id) == "disabled"


# activate_ad success → ad_alert_state переходит disabled → normal
@pytest.mark.asyncio
async def test_activate_ad_success_sets_normal(pg_engine, ad_with_state, monkeypatch) -> None:
    fb_ad_id, seed = ad_with_state
    await seed("disabled")

    payload = MetaMutationPayload(
        mutation_kind="activate_ad", target_id=fb_ad_id, params={}, ad_account_id="123"
    )
    await _run_one(
        pg_engine,
        payload,
        idem=f"owner-confirmed:activate_ad:{fb_ad_id}:t1",
        requested_by="operator:web:owner",
        lane="interactive",
        monkeypatch=monkeypatch,
    )

    assert await _read_alert_state(pg_engine, fb_ad_id) == "normal"


# Owner-confirmed bulk activate success → каждый ad из ad_ids переходит в normal
@pytest.mark.asyncio
async def test_owner_confirmed_bulk_activate_sets_normal(
    pg_engine, ad_with_state, monkeypatch
) -> None:
    fb_ad_id, seed = ad_with_state
    await seed("disabled")

    payload = MetaMutationPayload(
        mutation_kind="bulk_status_change",
        target_id="owner-confirmed:1",
        params={"ad_ids": [fb_ad_id], "action": "activate"},
        ad_account_id="123",
    )
    await _run_one(
        pg_engine,
        payload,
        idem=f"owner-confirmed:bulk:{fb_ad_id}",
        requested_by="operator:web:owner",
        lane="bulk",
        monkeypatch=monkeypatch,
    )

    assert await _read_alert_state(pg_engine, fb_ad_id) == "normal"
