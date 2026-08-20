# -*- coding: utf-8 -*-
"""Производственный цикл не сканирует браузер без захваченной задачи (#251).

Раньше это утверждение держалось на чтении исходника ``main_loop``: тест искал
в тексте подстроки ``claim_observer_scan(`` и отсутствие ``run_one_cycle(``.
Такая опора ложная — она ломается от переименования и ничего не доказывает:
можно было оставить обе строки на месте и всё равно открыть браузер мимо
очереди.

Здесь свидетель сидит внутри самого скана: в момент обращения к браузеру
проверяется durable-состояние очереди. Скан без живого захвата — это скан,
который никто не может ни отменить, ни ограничить дедлайном, ни отобрать у
зависшего процесса.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

import apps.observer_worker.main as obs_main
from apps.observer_worker.main import ScanCycleOutput, main_loop
from core.ad_account_catalog import ad_account_catalog
from core.scanner.models import SCANNER_METRICS_CONTRACT_REVISION, ScannedAdRow

pytestmark = pytest.mark.usefixtures("known_test_cabinet_timezones")


@pytest_asyncio.fixture
async def scanning_cabinet(pg_engine):
    """Один настроенный кабинет и включённый скан — минимум для боевого цикла."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for table in (
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
                await conn.execute(text(f"DELETE FROM {table}"))

    await _truncate()
    offer_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, 'CR2', 'CR2', TRUE)"),
            {"i": offer_id},
        )
        await ad_account_catalog.replace_offer_accounts(
            conn,
            offer_id=offer_id,
            account_ids=["111"],
        )
        await conn.execute(
            text(
                "INSERT INTO offer_rules (offer_id, cpa_threshold, currency) VALUES (:o, :c, 'USD')"
            ),
            {"o": offer_id, "c": Decimal("10.00")},
        )
        await conn.execute(
            text(
                """
                INSERT INTO observer_config
                    (singleton_key, is_scanning_enabled, interval_seconds, campaign_ids)
                VALUES ('default', TRUE, 1, ARRAY['1001'])
                ON CONFLICT (singleton_key) DO UPDATE
                SET is_scanning_enabled = TRUE,
                    interval_seconds = 1,
                    campaign_ids = ARRAY['1001']
                """
            )
        )
    yield
    await _truncate()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE observer_config SET interval_seconds = 90, "
                "campaign_ids = ARRAY[]::text[] WHERE singleton_key = 'default'"
            )
        )


async def _live_claims(pg_engine) -> int:
    """Сколько задач скана прямо сейчас захвачено с непросроченным lease."""
    async with pg_engine.connect() as conn:
        return int(
            await conn.scalar(
                text(
                    """
                    SELECT count(*) FROM task_queue
                    WHERE task_type = 'observer_scan'
                      AND status = 'running'
                      AND lease_owner IS NOT NULL
                      AND lease_expires_at > clock_timestamp()
                    """
                )
            )
        )


class _WitnessGate:
    """Двойник браузера, который смотрит на очередь в момент обращения к нему."""

    def __init__(self, pg_engine) -> None:
        self._engine = pg_engine
        self.claims_seen: list[int] = []

    async def open_cabinet_tabs(self, ad_account_ids: list[str]) -> list[dict]:
        return [{"ad_account_id": account_id, "opened": True} for account_id in ad_account_ids]

    async def run_one_scan(self, ad_account_id: str, **_kwargs) -> ScanCycleOutput:  # noqa: ARG002
        self.claims_seen.append(await _live_claims(self._engine))
        return ScanCycleOutput(
            rows=[
                ScannedAdRow(
                    fb_ad_id="230011",
                    campaign_id="120200000000002",
                    adset_id="120200000000003",
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
            ],
            metrics_contract_revision=SCANNER_METRICS_CONTRACT_REVISION,
        )


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_browser_is_only_touched_while_a_scan_task_is_claimed(
    pg_engine,
    scanning_cabinet,
    monkeypatch,
) -> None:
    """Каждое обращение к браузеру происходит внутри живого захвата задачи."""

    async def _no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(obs_main, "_wait_for_durable_scan", _no_sleep)

    gate = _WitnessGate(pg_engine)

    async def _gate_factory():
        return gate

    cycles = {"n": 0}

    def _should_continue() -> bool:
        cycles["n"] += 1
        return cycles["n"] <= 2

    await main_loop(gate_factory=_gate_factory, should_continue=_should_continue)

    # Скан вообще состоялся — иначе утверждение о его условиях пустое.
    assert gate.claims_seen, "цикл не дошёл до браузера"
    # И каждый раз он шёл под ровно одной захваченной задачей: не мимо очереди
    # и не под чужим параллельным захватом.
    assert all(seen == 1 for seen in gate.claims_seen), gate.claims_seen
