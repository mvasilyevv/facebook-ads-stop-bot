# -*- coding: utf-8 -*-
"""Интеграционные тесты автостарта кабинета по расписанию (money-критично).

Главное — безопасность:
- резолв по выбранным campaign_id owner-scoped: своя выбранная кампания включается,
  своя НЕ выбранная — нет, чужая (без owner-тега) — НЕ включается даже если выбрана,
  пустой список → пусто;
- run_one_tick создаёт ОДНУ pending-задачу bulk_status_change activate и триггерит
  observer scan, повторный тик в тот же день дедуплицируется (already_done).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

import apps.meta_api_worker.main as meta_worker
from apps.cabinet_scheduler.main import run_one_tick
from core.commands.service import CommandService
from core.meta_api.bulk import (
    guarded_autostart_execution_boundary,
    resolve_owner_ads_by_account,
)
from core.meta_api.queue import (
    claim_browser_ready_mutation_task,
    mark_task_succeeded,
)
from core.meta_api.schemas import MetaMutationPayload
from core.observer.enable_grace import EnableGraceUnsafeError
from core.observer.pipeline import process_scan_rows
from core.observer.scan_tasks import claim_observer_scan
from core.observer.writers import reset_alert_state_after_disable_succeeded
from core.scanner.models import ScannedAdRow
from core.scheduler.cabinet_autostart import write_autostart_config
from core.tasks.queue import (
    mark_external_call_started,
    reconcile_stuck_running,
    requeue_unknown_for_reconciliation,
)

pytestmark = pytest.mark.usefixtures(
    "known_test_cabinet_timezones",
    "fresh_browser_readiness",
)


@pytest_asyncio.fixture
async def clean_autostart_tables(pg_engine):
    """Чистит каталог + task_queue + observer/system конфиги до и после теста."""

    async def _trunc():
        async with pg_engine.begin() as conn:
            for t in (
                "command_idempotency_receipts",
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
            await conn.execute(text("DELETE FROM system_config WHERE key = 'cabinet_autostart'"))
            await conn.execute(text("DELETE FROM observer_config WHERE singleton_key = 'default'"))

    await _trunc()
    yield
    await _trunc()


def _row(
    fb_ad_id: str,
    campaign: str,
    campaign_id: str,
    ad_name: str = "AD",
    *,
    delivery_status: str = "ACTIVE",
) -> ScannedAdRow:
    """Минимальная строка скана; campaign_id → fb_campaigns.fb_campaign_id."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=campaign,
        campaign_id=campaign_id,
        adset_id=f"8{campaign_id}",
        adset_name="as",
        ad_name=ad_name,
        delivery_status=delivery_status,
        spend=Decimal("1"),
        budget="",
        reach=100,
        impressions=200,
        clicks=5,
        cpc=None,
        ctr=Decimal("2"),
        cpm=Decimal("2"),
        leads=0,
        registrations=0,
        deposits=0,
        outbound_clicks=5,
        landing_page_views=0,
    )


async def _set_owner_tag(pg_engine, tag: str | None, campaign_ids: list[str] | None = None) -> None:
    """Кладёт owner_campaign_tag + allowlist (campaign_ids) в observer_config + scanning ON.

    Allowlist — источник кампаний автостарта (объединён со «слежкой»).
    is_scanning_enabled server_default=FALSE; autostart-тик при выключенном сканировании
    сразу возвращает 'scanning_paused' — поэтому явно включаем.
    """
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO observer_config
                    (singleton_key, owner_campaign_tag, is_scanning_enabled, campaign_ids)
                VALUES ('default', :tag, TRUE, :ids)
                ON CONFLICT (singleton_key)
                DO UPDATE SET owner_campaign_tag = :tag, is_scanning_enabled = TRUE,
                              campaign_ids = :ids
                """
            ),
            {"tag": tag, "ids": campaign_ids or []},
        )


# ====================== resolve_owner_ads_by_account ======================


# Своя выбранная кампания (тег MV) включается; своя НЕ выбранная — нет
@pytest.mark.asyncio
async def test_resolve_by_campaign_selected_only(pg_engine, clean_autostart_tables) -> None:
    selected = _row("111000", "MV | KE | CR2 | 22.05", campaign_id="100")
    other = _row("111001", "MV | KE | CR2 | 18.04", campaign_id="200")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[selected, other], scan_id=1)

    resolution = await resolve_owner_ads_by_account(pg_engine, owner_tag="MV", campaign_ids=["100"])
    assert resolution.ads_by_account == {"123": ("111000",)}
    assert resolution.total == 1


# Чужая кампания (без owner-тега) НЕ включается, даже если её id выбран
@pytest.mark.asyncio
async def test_resolve_by_campaign_excludes_foreign(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111002", "MV | KE | CR2 | 22.05", campaign_id="300")
    foreign = _row("222002", "MZ Artemteam CR2 CBO", campaign_id="301")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine, foreign], scan_id=1)

    resolution = await resolve_owner_ads_by_account(
        pg_engine, owner_tag="MV", campaign_ids=["300", "301"]
    )
    assert resolution.ads_by_account == {"123": ("111002",)}
    assert resolution.total == 1


# Пустой список → пусто (НЕ включаем весь кабинет — безопасность)
@pytest.mark.asyncio
async def test_resolve_by_campaign_empty(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111003", "MV | KE | CR2 | 22.05", campaign_id="400")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)

    resolution = await resolve_owner_ads_by_account(pg_engine, owner_tag="MV", campaign_ids=[])
    assert resolution.ads_by_account == {}
    assert resolution.total == 0


# Не выбранная кампания (id не в списке) → пусто
@pytest.mark.asyncio
async def test_resolve_by_campaign_not_selected(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111004", "MV | CR2 | 22.05", campaign_id="500")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)

    resolution = await resolve_owner_ads_by_account(pg_engine, owner_tag="MV", campaign_ids=["999"])
    assert resolution.ads_by_account == {}
    assert resolution.total == 0


# Несколько выбранных кампаний: попадают все их активные объявления
@pytest.mark.asyncio
async def test_resolve_by_campaign_multiple(pg_engine, clean_autostart_tables) -> None:
    a = _row("111007", "MV | CR2 | 22.05", campaign_id="600")
    b = _row("111008", "MV | CR2 | 25.05", campaign_id="601")
    c = _row("111009", "MV | CR2 | 30.05", campaign_id="602")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[a, b, c], scan_id=1)

    resolution = await resolve_owner_ads_by_account(
        pg_engine, owner_tag="MV", campaign_ids=["600", "601"]
    )
    assert set(resolution.ads_by_account["123"]) == {"111007", "111008"}
    assert resolution.total == 2


# ====================== run_one_tick ======================


# В окне + enabled + своя выбранная кампания → pending bulk activate + scan trigger
@pytest.mark.asyncio
async def test_run_one_tick_starts_cabinet(pg_engine, clean_autostart_tables) -> None:
    # Чужой ad — в СВОЕЙ кампании (701): в Meta у кампании ровно одно имя, два ad'а
    # одной кампании не могут иметь разные campaign_name (идентичность каталога —
    # fb_campaign_id из baseline). Обе кампании в allowlist → проверяем, что
    # owner-scoping исключает чужую даже когда её id выбран.
    mine = _row("111100", "MV | KE | CR2 | 22.05", campaign_id="700")
    foreign = _row("222100", "MZ Artemteam CR2", campaign_id="701")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine, foreign], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["700", "701"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )

    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, now=now)

    assert summary["outcome"] == "started"
    assert summary["ad_count"] == 1
    assert summary["scan_triggered"] is True

    # Создана ровно одна pending bulk_status_change activate; чужой ad НЕ в задаче.
    async with pg_engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT status, payload, requested_by FROM task_queue "
                    "WHERE task_type = 'meta_api_mutation'"
                )
            )
        ).all()
    assert len(rows) == 1, "ровно одна задача автостарта"
    status, payload, requested_by = rows[0]
    assert status == "pending", "автостарт создаёт сразу pending (без draft)"
    assert requested_by == "cabinet_autostart"
    payload_dict = payload if isinstance(payload, dict) else json.loads(payload)
    assert payload_dict["mutation_kind"] == "bulk_status_change"
    assert payload_dict["params"]["action"] == "activate"
    assert payload_dict["params"]["ad_ids"] == ["111100"]
    assert set(payload_dict["params"]["activation_guards"]) == {"111100"}
    assert payload_dict["params"]["activation_guards"]["111100"]["version"] == 1
    assert "222100" not in payload_dict["params"]["ad_ids"], "чужой ad не включаем"

    async with pg_engine.connect() as conn:
        scan = (
            await conn.execute(
                text(
                    "SELECT status, lane, requested_by, payload FROM task_queue "
                    "WHERE task_type = 'observer_scan'"
                )
            )
        ).one()
    assert scan.status == "pending"
    assert scan.lane == "interactive"
    assert scan.requested_by == "cabinet_autostart"
    assert scan.payload["reason"] == "autostart_activation_reconciliation"
    assert scan.payload["dependency_state"] == "waiting"
    assert scan.payload["dependency_task_ids"] == summary["task_ids"]


@pytest.mark.asyncio
async def test_autostart_notification_failure_rolls_back_children_and_scan(
    pg_engine,
    clean_autostart_tables,
    monkeypatch,
) -> None:
    mine = _row("111150", "MV | KE | CR2 | 22.05", campaign_id="750")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["750"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    monkeypatch.setattr(
        "apps.cabinet_scheduler.main.notify_owners_in_transaction",
        AsyncMock(side_effect=RuntimeError("notification projection failed")),
    )

    with pytest.raises(RuntimeError, match="notification projection failed"):
        await run_one_tick(
            engine=pg_engine,
            now=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
        )

    async with pg_engine.connect() as conn:
        count = await conn.scalar(
            text("SELECT COUNT(*) FROM task_queue WHERE requested_by = 'cabinet_autostart'")
        )
    assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_confirmed_autopause_after_bulk_enqueue_prevents_late_activation(
    pg_engine,
    clean_autostart_tables,
) -> None:
    fb_ad_id = "111175"
    mine = _row(fb_ad_id, "MV | KE | CR2 | 22.05", campaign_id="775")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state (ad_id, alert_state, last_transition_at)
                SELECT id, 'normal', NOW()
                FROM fb_ads
                WHERE fb_ad_id = :fb_ad_id
                ON CONFLICT (ad_id) DO UPDATE
                SET alert_state = 'normal',
                    open_state_token = NULL,
                    last_transition_at = NOW()
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["775"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    summary = await run_one_tick(
        engine=pg_engine,
        now=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
    )
    assert summary["outcome"] == "started"

    # Auto-pause is decided after the bulk intent already exists. Its higher
    # priority claim confirms OFF and advances the FSM generation first.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'stop_sent',
                    current_stage = 'stop',
                    open_state_token = :token,
                    last_transition_at = NOW()
                WHERE ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fb_ad_id)
                """
            ),
            {"fb_ad_id": fb_ad_id, "token": uuid.uuid4()},
        )
    pause = await CommandService(pg_engine).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id=fb_ad_id,
        requested_by="bot_auto_stop",
        idempotency_key=f"test-autopause:{uuid.uuid4()}",
    )
    pause_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert pause_claim.task is not None
    assert pause_claim.task.id == pause.task_id
    assert await mark_task_succeeded(
        pg_engine,
        task_id=pause_claim.task.id,
        result={"outcome": "CONFIRMED", "status": "PAUSED"},
        lease_owner=pause_claim.task.lease_owner,
        lease_token=pause_claim.task.lease_token,
    )
    assert await reset_alert_state_after_disable_succeeded(
        pg_engine,
        fb_ad_id=fb_ad_id,
    )

    bulk_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert bulk_claim.task is not None
    assert bulk_claim.task.id == summary["task_ids"][0]
    client = AsyncMock()

    await meta_worker.process_one_task(
        pg_engine,
        bulk_claim.task,
        client=client,
    )

    client.execute_graph_call.assert_not_awaited()
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": bulk_claim.task.id},
            )
        ).one()
    assert row.status == "failed"
    assert row.result["outcome"] == "REJECTED"
    assert row.result["guard_rejected"][fb_ad_id].startswith("fsm_state:disabled")


# Повторный тик в тот же день → already_done, дубль-задачи не создаётся
@pytest.mark.asyncio
async def test_run_one_tick_dedup_same_day(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111200", "MV | KE | CR2 | 22.05", campaign_id="800")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["800"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )

    now1 = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    now2 = datetime(2026, 5, 29, 6, 1, 0, tzinfo=timezone.utc)

    first = await run_one_tick(engine=pg_engine, now=now1)
    second = await run_one_tick(engine=pg_engine, now=now2)

    assert first["outcome"] == "started"
    assert second["outcome"] == "already_done"

    # Только одна задача — повторный тик не задвоил enable.
    async with pg_engine.connect() as conn:
        counts = (
            await conn.execute(
                text(
                    "SELECT task_type, COUNT(*) FROM task_queue "
                    "GROUP BY task_type ORDER BY task_type"
                )
            )
        ).all()
    assert {row.task_type: int(row.count) for row in counts} == {
        "meta_api_mutation": 1,
        "observer_scan": 1,
    }


@pytest.mark.asyncio
async def test_concurrent_ticks_share_one_guarded_barrier(
    pg_engine,
    clean_autostart_tables,
) -> None:
    mine = _row("111225", "MV | KE | CR2 | 22.05", campaign_id="825")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["825"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc)

    summaries = await asyncio.gather(
        run_one_tick(engine=pg_engine, now=now),
        run_one_tick(engine=pg_engine, now=now),
    )
    assert {summary["outcome"] for summary in summaries} == {"started", "already_done"}

    async with pg_engine.connect() as conn:
        children = (
            await conn.execute(
                text("SELECT id FROM task_queue WHERE task_type = 'meta_api_mutation' ORDER BY id")
            )
        ).all()
        scans = (
            await conn.execute(
                text("SELECT payload FROM task_queue WHERE task_type = 'observer_scan' ORDER BY id")
            )
        ).all()
    assert len(children) == 1
    assert len(scans) == 1
    assert scans[0].payload["dependency_state"] == "waiting"
    assert scans[0].payload["dependency_task_ids"] == [int(children[0].id)]


@pytest.mark.asyncio
async def test_guarded_bulk_crash_reconciles_before_barrier_scan(
    pg_engine,
    clean_autostart_tables,
) -> None:
    fb_ad_id = "111250"
    mine = _row(fb_ad_id, "MV | KE | CR2 | 22.05", campaign_id="850")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["850"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    summary = await run_one_tick(
        engine=pg_engine,
        now=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
    )
    child_id = int(summary["task_ids"][0])
    first_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
        lease_seconds=5,
    )
    assert first_claim.task is not None
    assert first_claim.task.id == child_id
    payload = MetaMutationPayload.from_dict(first_claim.task.payload)

    # The boundary committed, but the process died before it could persist a
    # response or terminal FSM projection.
    async with guarded_autostart_execution_boundary(
        pg_engine,
        task=first_claim.task,
        payload=payload,
    ) as execution:
        assert execution.external_started is True
        assert execution.executable_ad_ids == (fb_ad_id,)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue "
                "SET lease_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = :task_id"
            ),
            {"task_id": child_id},
        )

    assert await reconcile_stuck_running(pg_engine, stuck_after_seconds=0) == 1
    async with pg_engine.connect() as conn:
        recovered = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": child_id},
            )
        ).one()
    assert recovered.status == "retrying"
    assert recovered.result["reconcile_required"] is True
    assert recovered.result["bulk_execution_ad_ids"] == [fb_ad_id]
    assert await claim_observer_scan(pg_engine, worker_id=uuid.uuid4()) is None

    reconciliation_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert reconciliation_claim.task is not None
    assert reconciliation_claim.task.id == child_id
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value=[
            {
                "code": 200,
                "body": '{"status":"ACTIVE","effective_status":"ACTIVE"}',
            }
        ]
    )
    await meta_worker.process_one_task(
        pg_engine,
        reconciliation_claim.task,
        client=client,
    )

    async with pg_engine.connect() as conn:
        terminal = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": child_id},
            )
        ).one()
    assert terminal.status == "succeeded"
    assert terminal.result["confirmed_ids"] == [fb_ad_id]
    assert terminal.result["unknown_ids"] == []
    released_scan = await claim_observer_scan(pg_engine, worker_id=uuid.uuid4())
    assert released_scan is not None
    assert released_scan.payload["dependency_state"] == "ready"


@pytest.mark.asyncio
async def test_newer_confirmed_pause_wins_ambiguous_autostart_reconciliation(
    pg_engine,
    clean_autostart_tables,
) -> None:
    fb_ad_id = "111275"
    mine = _row(fb_ad_id, "MV | KE | CR2 | 22.05", campaign_id="875")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state (ad_id, alert_state, last_transition_at)
                SELECT id, 'normal', NOW()
                FROM fb_ads
                WHERE fb_ad_id = :fb_ad_id
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["875"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    summary = await run_one_tick(
        engine=pg_engine,
        now=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
    )
    child_id = int(summary["task_ids"][0])
    bulk_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert bulk_claim.task is not None
    bulk_payload = MetaMutationPayload.from_dict(bulk_claim.task.payload)
    async with guarded_autostart_execution_boundary(
        pg_engine,
        task=bulk_claim.task,
        payload=bulk_payload,
    ) as execution:
        assert execution.external_started is True

    # A newer auto-pause generation confirms after the activation response was
    # lost. The late ACTIVE read must never normalize disabled back to normal.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'stop_sent',
                    current_stage = 'stop',
                    open_state_token = :token,
                    last_transition_at = NOW()
                WHERE ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fb_ad_id)
                """
            ),
            {"fb_ad_id": fb_ad_id, "token": uuid.uuid4()},
        )
    pause = await CommandService(pg_engine).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id=fb_ad_id,
        requested_by="bot_auto_stop",
        idempotency_key=f"test-newer-pause:{uuid.uuid4()}",
    )
    pause_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert pause_claim.task is not None
    assert pause_claim.task.id == pause.task_id
    assert await mark_task_succeeded(
        pg_engine,
        task_id=pause_claim.task.id,
        result={"outcome": "CONFIRMED", "status": "PAUSED"},
        lease_owner=pause_claim.task.lease_owner,
        lease_token=pause_claim.task.lease_token,
    )
    assert await reset_alert_state_after_disable_succeeded(
        pg_engine,
        fb_ad_id=fb_ad_id,
    )

    # Make the compensation immediately runnable in this test; production uses
    # the original absolute external deadline plus a five-second drain margin.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET result = jsonb_set(
                    result,
                    '{bulk_external_deadline_at}',
                    to_jsonb('2020-01-01T00:00:00+00:00'::text)
                )
                WHERE id = :task_id
                """
            ),
            {"task_id": child_id},
        )
    assert await requeue_unknown_for_reconciliation(
        pg_engine,
        task=bulk_claim.task,
        error="ambiguous activation response",
    )
    reconciliation_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert reconciliation_claim.task is not None
    assert reconciliation_claim.task.id == child_id
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value=[
            {
                "code": 200,
                "body": '{"status":"ACTIVE","effective_status":"ACTIVE"}',
            }
        ]
    )
    await meta_worker.process_one_task(
        pg_engine,
        reconciliation_claim.task,
        client=client,
    )

    async with pg_engine.connect() as conn:
        old_task = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": child_id},
            )
        ).one()
        fsm_state = await conn.scalar(
            text(
                "SELECT state.alert_state "
                "FROM ad_alert_state AS state "
                "JOIN fb_ads AS ad ON ad.id = state.ad_id "
                "WHERE ad.fb_ad_id = :fb_ad_id"
            ),
            {"fb_ad_id": fb_ad_id},
        )
        compensation = (
            await conn.execute(
                text(
                    """
                    SELECT id, status, payload
                    FROM task_queue
                    WHERE requested_by = 'bot_auto_stop'
                      AND payload #>> '{params,supersedes_autostart_task_id}' = :source_id
                    """
                ),
                {"source_id": str(child_id)},
            )
        ).one()
        scan_payload = await conn.scalar(
            text("SELECT payload FROM task_queue WHERE task_type = 'observer_scan'")
        )
    assert old_task.status == "failed"
    assert old_task.result["outcome"] == "UNKNOWN"
    assert old_task.result["modified_ids"] == []
    assert old_task.result["safety_compensation_ids"] == [fb_ad_id]
    assert fsm_state == "disabled"
    assert compensation.status == "pending"
    assert compensation.payload["params"] == {
        "requested_via": "bot_auto_stop",
        "safety_compensation": "autostart_reconciliation",
        "supersedes_autostart_task_id": child_id,
    }
    assert scan_payload["dependency_task_ids"] == [child_id, int(compensation.id)]
    assert await claim_observer_scan(pg_engine, worker_id=uuid.uuid4()) is None
    compensation_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert compensation_claim.task is not None
    assert compensation_claim.task.id == int(compensation.id)
    assert await mark_task_succeeded(
        pg_engine,
        task_id=compensation_claim.task.id,
        result={"outcome": "CONFIRMED", "status": "PAUSED"},
        lease_owner=compensation_claim.task.lease_owner,
        lease_token=compensation_claim.task.lease_token,
    )
    assert await claim_observer_scan(pg_engine, worker_id=uuid.uuid4()) is not None


@pytest.mark.asyncio
async def test_terminal_task_and_fsm_projection_roll_back_together(
    pg_engine,
    clean_autostart_tables,
    monkeypatch,
) -> None:
    fb_ad_id = "111290"
    mine = _row(fb_ad_id, "MV | KE | CR2 | 22.05", campaign_id="890")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state
                    (ad_id, alert_state, current_stage, open_state_token,
                     last_transition_at)
                SELECT id, 'stop_sent', 'stop', :token, NOW()
                FROM fb_ads
                WHERE fb_ad_id = :fb_ad_id
                """
            ),
            {"fb_ad_id": fb_ad_id, "token": uuid.uuid4()},
        )
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["890"])
    receipt = await CommandService(pg_engine).enqueue_ad_action(
        action_kind="pause_ad",
        fb_ad_id=fb_ad_id,
        requested_by="operator_test",
        idempotency_key=f"atomic-fsm:{uuid.uuid4()}",
    )
    claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert claim.task is not None
    assert claim.task.id == receipt.task_id

    async def crash_after_fsm_write(conn, payload, result):
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'disabled', updated_at = NOW()
                WHERE ad_id = (SELECT id FROM fb_ads WHERE fb_ad_id = :fb_ad_id)
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
        raise RuntimeError("simulated crash before terminal commit")

    monkeypatch.setattr(
        meta_worker,
        "sync_fsm_after_mutation",
        crash_after_fsm_write,
    )
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(return_value={"success": True})
    await meta_worker.process_one_task(pg_engine, claim.task, client=client)

    async with pg_engine.connect() as conn:
        task_row = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": receipt.task_id},
            )
        ).one()
        fsm_state = await conn.scalar(
            text(
                "SELECT state.alert_state "
                "FROM ad_alert_state AS state "
                "JOIN fb_ads AS ad ON ad.id = state.ad_id "
                "WHERE ad.fb_ad_id = :fb_ad_id"
            ),
            {"fb_ad_id": fb_ad_id},
        )
    assert task_row.status == "retrying"
    assert task_row.result["outcome"] == "UNKNOWN"
    assert task_row.result["reconcile_required"] is True
    assert fsm_state == "stop_sent"


@pytest.mark.asyncio
async def test_confirmed_active_without_grace_commits_pause_compensation(
    pg_engine,
    clean_autostart_tables,
    monkeypatch,
) -> None:
    fb_ad_id = "111295"
    # The normal activation is valid against the catalog's confirmed OFF.
    # Reconciliation later proves that Meta applied ACTIVE before the response
    # was lost, while grace persistence still fails and requires compensation.
    mine = _row(
        fb_ad_id,
        "MV | KE | CR2 | 22.05",
        campaign_id="895",
        delivery_status="OFF",
    )
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine], scan_id=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state (ad_id, alert_state, last_transition_at)
                SELECT id, 'disabled', NOW()
                FROM fb_ads
                WHERE fb_ad_id = :fb_ad_id
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["895"])
    activation = await CommandService(pg_engine).enqueue_ad_action(
        action_kind="activate_ad",
        fb_ad_id=fb_ad_id,
        requested_by="operator_test",
        idempotency_key=f"grace-activation:{uuid.uuid4()}",
        params={"enable_grace": {"spend_cap": "10.00"}},
    )
    first_claim = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert first_claim.task is not None
    assert first_claim.task.id == activation.task_id
    assert await mark_external_call_started(
        pg_engine,
        task_id=activation.task_id,
        target_lock_key=fb_ad_id,
        lease_owner=first_claim.task.lease_owner,
        lease_token=first_claim.task.lease_token,
    )
    assert await requeue_unknown_for_reconciliation(
        pg_engine,
        task=first_claim.task,
        error="activation response lost",
    )
    verification = await claim_browser_ready_mutation_task(
        pg_engine,
        lanes=("money",),
        worker_id=uuid.uuid4(),
    )
    assert verification.task is not None
    monkeypatch.setattr(
        meta_worker,
        "_prepare_enable_grace_for_payload",
        AsyncMock(side_effect=EnableGraceUnsafeError("fresh snapshot missing")),
    )
    client = AsyncMock()
    client.execute_graph_call = AsyncMock(
        return_value={"status": "ACTIVE", "effective_status": "ACTIVE"}
    )
    await meta_worker.process_one_task(
        pg_engine,
        verification.task,
        client=client,
    )

    async with pg_engine.connect() as conn:
        original = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :task_id"),
                {"task_id": activation.task_id},
            )
        ).one()
        compensation = (
            await conn.execute(
                text(
                    """
                    SELECT id, status, lane, payload
                    FROM task_queue
                    WHERE payload #>> '{params,supersedes_activation_task_id}'
                        = :source_id
                    """
                ),
                {"source_id": str(activation.task_id)},
            )
        ).one()
        fsm_state = await conn.scalar(
            text(
                "SELECT state.alert_state "
                "FROM ad_alert_state AS state "
                "JOIN fb_ads AS ad ON ad.id = state.ad_id "
                "WHERE ad.fb_ad_id = :fb_ad_id"
            ),
            {"fb_ad_id": fb_ad_id},
        )
    assert original.status == "failed"
    assert original.result["outcome"] == "UNKNOWN"
    assert original.result["reason"] == "enable_grace_compensation_pending"
    assert original.result["compensation_task_id"] == int(compensation.id)
    assert compensation.status == "pending"
    assert compensation.lane == "money"
    assert compensation.payload["mutation_kind"] == "pause_ad"
    assert compensation.payload["params"] == {
        "requested_via": "bot_auto_stop",
        "safety_compensation": "activation_without_grace",
        "supersedes_activation_task_id": activation.task_id,
    }
    assert fsm_state == "stop_sent"


# Фича выключена → ничего не делаем (disabled), задач нет, ключ не ставится
@pytest.mark.asyncio
async def test_run_one_tick_disabled(pg_engine, clean_autostart_tables) -> None:
    await _set_owner_tag(pg_engine, "MV", campaign_ids=[])
    await write_autostart_config(
        pg_engine,
        {"enabled": False, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, now=now)
    assert summary["outcome"] == "disabled"


# Не в окне (до планового времени) → not_in_window, ключ не ставится
@pytest.mark.asyncio
async def test_run_one_tick_not_in_window(pg_engine, clean_autostart_tables) -> None:
    await _set_owner_tag(pg_engine, "MV", campaign_ids=[])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime(2026, 5, 29, 5, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, now=now)
    assert summary["outcome"] == "not_in_window"


# Включено, в окне, но кампаний не выбрано → fail-safe no_campaigns без задач.
@pytest.mark.asyncio
async def test_run_one_tick_no_campaigns(pg_engine, clean_autostart_tables) -> None:
    await _set_owner_tag(pg_engine, "MV", campaign_ids=[])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, now=now)
    assert summary["outcome"] == "no_campaigns"


# Включено, в окне, но выбранная кампания чужая (нет owner-тега) → no_owner_ads, scan триггерим
@pytest.mark.asyncio
async def test_run_one_tick_no_owner_ads(pg_engine, clean_autostart_tables) -> None:
    foreign = _row("222300", "MZ Artemteam CR2", campaign_id="900")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[foreign], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["900"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, now=now)
    assert summary["outcome"] == "no_owner_ads"

    # Задач нет (чужая кампания не включается).
    async with pg_engine.connect() as conn:
        counts = (
            await conn.execute(
                text("SELECT task_type, COUNT(*) FROM task_queue GROUP BY task_type")
            )
        ).all()
    assert {row.task_type: int(row.count) for row in counts} == {"observer_scan": 1}


# ====================== R-money: фильтр свежести last_seen_at ======================


# Свежий ад (last_seen_at недавно) включается, протухший (давно не виден) — исключён.
# Защита от реактивации давно снятых ads: is_active=TRUE монотонно-истинный.
@pytest.mark.asyncio
async def test_resolve_by_campaign_freshness_filter(pg_engine, clean_autostart_tables) -> None:
    fresh = _row("111900", "MV | KE | CR2 | 22.05", campaign_id="950")
    stale = _row("111901", "MV | KE | CR2 | 22.05", campaign_id="950")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[fresh, stale], scan_id=1)

    # Протухшему аду откатываем last_seen_at на 5 дней назад (старый cabinet-день).
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE fb_ads SET last_seen_at = NOW() - INTERVAL '5 days' "
                "WHERE fb_ad_id = '111901'"
            )
        )

    since = datetime.now(timezone.utc) - timedelta(hours=48)
    resolution = await resolve_owner_ads_by_account(
        pg_engine, owner_tag="MV", campaign_ids=["950"], since=since
    )
    assert resolution.ads_by_account == {"123": ("111900",)}
    assert resolution.total == 1


# Без явно заданной границы функция возвращает весь подтверждённый каталог.
@pytest.mark.asyncio
async def test_resolve_by_campaign_no_since_returns_all(pg_engine, clean_autostart_tables) -> None:
    fresh = _row("111902", "MV | KE | CR2 | 22.05", campaign_id="960")
    stale = _row("111903", "MV | KE | CR2 | 22.05", campaign_id="960")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[fresh, stale], scan_id=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE fb_ads SET last_seen_at = NOW() - INTERVAL '5 days' "
                "WHERE fb_ad_id = '111903'"
            )
        )

    resolution = await resolve_owner_ads_by_account(pg_engine, owner_tag="MV", campaign_ids=["960"])
    assert set(resolution.ads_by_account["123"]) == {"111902", "111903"}
    assert resolution.total == 2


# run_one_tick автостарта НЕ включает протухший ад (передаёт since в резолв).
@pytest.mark.asyncio
async def test_run_one_tick_excludes_stale_ad(pg_engine, clean_autostart_tables) -> None:
    fresh = _row("111904", "MV | KE | CR2 | 29.05", campaign_id="970")
    stale = _row("111905", "MV | KE | CR2 | 29.05", campaign_id="970")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[fresh, stale], scan_id=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE fb_ads SET last_seen_at = NOW() - INTERVAL '5 days' "
                "WHERE fb_ad_id = '111905'"
            )
        )
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["970"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0)
    summary = await run_one_tick(engine=pg_engine, now=now)
    assert summary["outcome"] == "started"
    assert summary["ad_count"] == 1, "только свежий ад поднят"

    # В payload задачи — только свежий ad_id.
    async with pg_engine.connect() as conn:
        payload = (
            await conn.execute(
                text(
                    "SELECT payload FROM task_queue WHERE task_type = 'meta_api_mutation' "
                    "ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).scalar_one()
    payload_str = str(payload)
    assert "111904" in payload_str, "свежий ад в задаче"
    assert "111905" not in payload_str, "протухший ад НЕ должен попасть в autostart-activate"
