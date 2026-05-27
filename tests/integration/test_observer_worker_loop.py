# -*- coding: utf-8 -*-
"""Интеграционный тест observer_worker_v2 main loop через fake gate + fakeredis.

Покрывает: begin/finish scan_run, run_one_cycle, paused/empty/error outcomes,
Redis heartbeat и pubsub событие. Не требует ни browser-agent, ни Vision.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.observer_worker.main import (
    ScanCycleOutput,
    main_loop,
    run_one_cycle,
)
from core.scanner.models import ScannedAdRow


def _row(fb_ad_id: str = "230011", **overrides) -> ScannedAdRow:
    defaults = dict(
        fb_ad_id=fb_ad_id,
        campaign_name="CR2 | KE | MV | promo",
        adset_name="EQ_KE",
        ad_name="Av01",
        delivery_status="ACTIVE",
        spend=Decimal("3.0"),
        reach=1000,
        impressions=2000,
        clicks=50,
        cpc=Decimal("0.05"),
        ctr=Decimal("2.5"),
        leads=10,
        registrations=5,
        deposits=2,
        outbound_clicks=30,
        landing_page_views=20,
    )
    defaults.update(overrides)
    return ScannedAdRow(**defaults)


class _FakeGate:
    """Fake ScannerGate — программируемый ScanCycleOutput."""

    def __init__(self, output: ScanCycleOutput | Exception):
        self._output = output
        self.calls = 0

    async def run_one_scan(self) -> ScanCycleOutput:
        self.calls += 1
        if isinstance(self._output, Exception):
            raise self._output
        return self._output


@pytest_asyncio.fixture
async def clean_obs_tables(pg_engine):
    """Чистит scan_runs/observer-таблицы/task_queue/offers до и после."""

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
                "scan_runs",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def offer_cr2(pg_engine, clean_obs_tables):
    offer_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, 'CR2', 'CR2', TRUE)"),
            {"i": offer_id},
        )
        await conn.execute(
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:o, :c)"),
            {"o": offer_id, "c": Decimal("10.00")},
        )
    return offer_id


@pytest_asyncio.fixture
async def ensure_observer_config_enabled(pg_engine):
    """Гарантирует что singleton observer_config есть и is_scanning_enabled=true."""
    async with pg_engine.begin() as conn:
        # apply_v2_schema создал строку с дефолтами; проверим что есть и принудительно включим
        await conn.execute(
            text(
                """
                INSERT INTO observer_config (singleton_key, is_scanning_enabled)
                VALUES ('default', TRUE)
                ON CONFLICT (singleton_key) DO UPDATE
                SET is_scanning_enabled = TRUE,
                    interval_seconds = 1,
                    jitter_seconds = 0
                """
            )
        )
    yield
    # после теста возвращаем дефолтные значения
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE observer_config SET interval_seconds = 90, jitter_seconds = 15 "
                "WHERE singleton_key = 'default'"
            )
        )


# Сценарий: один цикл с одной строкой → success outcome, scan_run финализирован
@pytest.mark.asyncio
async def test_run_one_cycle_happy_path(
    pg_engine, ensure_observer_config_enabled, offer_cr2, fake_redis_client
) -> None:
    gate = _FakeGate(ScanCycleOutput(rows=[_row()], total_passes=1))

    summary = await run_one_cycle(pg_engine, gate=gate, redis_client=fake_redis_client)

    assert gate.calls == 1
    assert summary["outcome"] == "success"
    assert summary["scan_id"] is not None
    assert summary["rows_total"] == 1

    async with pg_engine.connect() as conn:
        # scan_run завершён
        sr = (
            await conn.execute(
                text(
                    "SELECT outcome, rows_total, duration_ms FROM scan_runs "
                    "WHERE id = :i ORDER BY started_at DESC LIMIT 1"
                ),
                {"i": summary["scan_id"]},
            )
        ).first()
        assert sr[0] == "success"
        assert sr[1] == 1
        assert sr[2] is not None and sr[2] >= 0


# Сценарий: is_scanning_enabled=false → outcome='paused', gate не вызывается
@pytest.mark.asyncio
async def test_paused_when_scanning_disabled(
    pg_engine, clean_obs_tables, fake_redis_client
) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO observer_config (singleton_key, is_scanning_enabled) "
                "VALUES ('default', FALSE) "
                "ON CONFLICT (singleton_key) DO UPDATE SET is_scanning_enabled = FALSE"
            )
        )

    gate = _FakeGate(ScanCycleOutput(rows=[_row()]))
    summary = await run_one_cycle(pg_engine, gate=gate, redis_client=fake_redis_client)
    assert summary["outcome"] == "paused"
    assert summary["scan_id"] is None
    assert gate.calls == 0


# Сценарий: gate вернул пустой результат → outcome='empty', scan_run с error_message
@pytest.mark.asyncio
async def test_empty_scan(
    pg_engine, ensure_observer_config_enabled, offer_cr2, fake_redis_client
) -> None:
    gate = _FakeGate(ScanCycleOutput(rows=[], empty_reason="cabinet was reset"))
    summary = await run_one_cycle(pg_engine, gate=gate, redis_client=fake_redis_client)
    assert summary["outcome"] == "empty"
    async with pg_engine.connect() as conn:
        sr = (
            await conn.execute(
                text(
                    "SELECT outcome, error_message FROM scan_runs "
                    "WHERE id = :i ORDER BY started_at DESC LIMIT 1"
                ),
                {"i": summary["scan_id"]},
            )
        ).first()
    assert sr[0] == "empty"
    assert sr[1] == "cabinet was reset"


# Сценарий: gate упал с исключением → outcome='error', error_message заполнен
@pytest.mark.asyncio
async def test_gate_raises(
    pg_engine, ensure_observer_config_enabled, offer_cr2, fake_redis_client
) -> None:
    gate = _FakeGate(ConnectionError("browser-agent unreachable"))
    summary = await run_one_cycle(pg_engine, gate=gate, redis_client=fake_redis_client)
    assert summary["outcome"] == "error"
    assert "ConnectionError" in summary["error"]


# Сценарий: Redis heartbeat записан + pubsub событие отправлено
@pytest.mark.asyncio
async def test_redis_heartbeat_and_pubsub(
    pg_engine, ensure_observer_config_enabled, offer_cr2, fake_redis_client
) -> None:
    # Подпишемся на канал ДО запуска цикла
    pubsub = fake_redis_client.pubsub()
    await pubsub.subscribe("fb_agent:scan:finished")
    await pubsub.get_message(timeout=0.5)  # drain subscribe-message

    gate = _FakeGate(ScanCycleOutput(rows=[_row()]))
    await run_one_cycle(pg_engine, gate=gate, redis_client=fake_redis_client)

    # Heartbeat
    raw = await fake_redis_client.get("observer:runtime")
    assert raw is not None
    import json as _json

    payload = _json.loads(raw)
    assert payload["worker_status"] in ("idle", "scanning")

    # Pubsub событие
    msg = await pubsub.get_message(timeout=2.0)
    assert msg is not None
    assert msg["type"] == "message"
    assert msg["channel"] == "fb_agent:scan:finished"
    event = _json.loads(msg["data"])
    assert event["rows_total"] == 1
    assert event["outcome"] == "success"

    await pubsub.unsubscribe("fb_agent:scan:finished")
    await pubsub.aclose()


# Сценарий: main_loop с лимитом итераций (через should_continue) — graceful exit
@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_main_loop_runs_n_cycles_and_exits(
    pg_engine, ensure_observer_config_enabled, offer_cr2, fake_redis_client
) -> None:
    iterations = {"n": 0}

    def _should_continue() -> bool:
        iterations["n"] += 1
        return iterations["n"] <= 2  # ровно 2 итерации

    gate = _FakeGate(ScanCycleOutput(rows=[_row(fb_ad_id="23A001")]))

    async def _gate_factory():
        return gate

    async def _redis_factory():
        return fake_redis_client

    async def _tg_factory():
        return None  # без TG в этом тесте

    await main_loop(
        gate_factory=_gate_factory,
        redis_factory=_redis_factory,
        tg_client_factory=_tg_factory,
        should_continue=_should_continue,
    )

    # Должен был сделать минимум один scan (второй не успеет дойти до sleep'а)
    assert gate.calls >= 1
