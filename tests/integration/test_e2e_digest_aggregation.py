# -*- coding: utf-8 -*-
"""E2E cross-cutting сценарий: observer pipeline → digest_builder.

Сшивка двух подсистем:
1. `core/observer/pipeline.process_scan_rows` создаёт реальные alert_events,
   ad_metrics и task_queue.meta_api_mutation pause_ad записи (полная воронка observer'а).
2. `core/telegram/digest_builder.build_digest` агрегирует partitioned-таблицы
   по окну за «вчера» и возвращает структуру для TG-рендерера.

Цель — убедиться что счётчики дайджеста совпадают с тем, что реально
произвёл observer + meta_api_worker: ни одной фантомной записи, ни одного
пропущенного события. _count_disable_tasks считает task_type='meta_api_mutation'
с mutation_kind='pause_ad' (новый канал) и legacy task_type='disable'.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

import apps.meta_api_worker.main as worker_main
from apps.meta_api_worker.main import process_one_task
from core.meta_api.queue import claim_pending_task
from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow
from core.telegram.digest_builder import build_digest


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def clean_digest_e2e(pg_engine):
    """Полная очистка таблиц pipeline + offers/catalog."""

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


async def _seed_offer(pg_engine, *, code: str, cpa: Decimal) -> uuid.UUID:
    offer_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, TRUE)"),
            {"i": offer_id, "c": code, "n": f"Digest E2E {code}"},
        )
        await conn.execute(
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:o, :cpa)"),
            {"o": offer_id, "cpa": cpa},
        )
    return offer_id


def _row(*, code: str, fb_ad_id: str, spend: Decimal, deposits: int) -> ScannedAdRow:
    """ScannedAdRow с заданным spend/deposits, остальное безопасно-нейтрально."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=f"{code} | KE | promo",
        adset_name="ADS_E2E",
        ad_name=f"AD_{fb_ad_id[-4:]}",
        delivery_status="ACTIVE",
        spend=spend,
        leads=0 if deposits == 0 else 5,
        registrations=0 if deposits == 0 else 3,
        deposits=deposits,
        cpc=Decimal("0.10"),
        ctr=Decimal("2.0"),
    )


# E2E: 1 scan создаёт alert_events + ad_metrics + pause_ad mutation task,
# потом process_one_task доводит её до succeeded → digest считает disable_tasks_succeeded.
@pytest.mark.asyncio
async def test_digest_aggregates_observer_pipeline_output(
    pg_engine,
    clean_digest_e2e,
    monkeypatch,
) -> None:
    # 2 разных оффера, 2 ad'а:
    # - ad_stop: STOP-инцидент (spend=25, deposits=0 → fast-stop)
    # - ad_ok:  норма (spend=2, deposits=2)
    await _seed_offer(pg_engine, code="DGST_S", cpa=Decimal("10"))
    await _seed_offer(pg_engine, code="DGST_O", cpa=Decimal("10"))

    fb_stop = f"230077{uuid.uuid4().hex[:6]}"
    fb_ok = f"230088{uuid.uuid4().hex[:6]}"

    stop_row = _row(code="DGST_S", fb_ad_id=fb_stop, spend=Decimal("25"), deposits=0)
    ok_row = _row(code="DGST_O", fb_ad_id=fb_ok, spend=Decimal("2"), deposits=2)

    # Шаг 1: один scan-цикл → один stop_event + два snapshot'а метрик +
    # одна pause_ad mutation задача (pending) в outbox.
    result = await process_scan_rows(pg_engine, rows=[stop_row, ok_row], scan_id=100)
    assert result.alerts_stop == 1
    assert result.disable_tasks_created == 1

    # Шаг 2: meta_api_worker доводит pause_ad mutation до succeeded →
    # попадёт в счётчик disable_tasks_succeeded в digest.
    async def _fake_dispatch(client, p):
        return {"success": True, "graph_response": {"ok": True}}

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)

    claim = await claim_pending_task(pg_engine)
    assert not claim.queue_empty
    assert claim.task is not None

    await process_one_task(pg_engine, claim.task, client=AsyncMock())

    # Шаг 3: дайджест за окно «сейчас+1ч» (window=24h backwards) — должен поймать всё.
    digest = await build_digest(
        pg_engine,
        day_start_utc=_utcnow() + timedelta(hours=1),
        window_hours=24,
        top_limit=5,
    )

    # Счётчики событий — ровно те что мы реально создали observer'ом
    assert digest.alerts_stop_count == 1
    assert digest.alerts_warning_count == 0
    # pause_ad выполнен через meta_api_worker → попал в succeeded
    assert digest.disable_tasks_succeeded == 1
    assert digest.disable_tasks_failed == 0
    # Оба оффера активны
    assert digest.active_offers_count == 2
    # active_ads_normal — только ad_ok в normal (ad_stop в disabled после FSM-sync)
    assert digest.active_ads_count == 1

    # Топ-N: оба ad'а с spend > 0, отсортированы desc
    assert len(digest.top_ads_by_spend) == 2
    spends = [row.spend_usd for row in digest.top_ads_by_spend]
    assert spends == sorted(spends, reverse=True)
    top1 = digest.top_ads_by_spend[0]
    assert top1.spend_usd == Decimal("25")
    assert top1.fb_ad_id == fb_stop
    # offer_code должен попасть из catalog join
    assert top1.offer_code == "DGST_S"

    # Total spend = сумма последних snapshot'ов = 25 + 2 = 27
    assert digest.total_spend_24h_usd == Decimal("27")


# E2E: пустой период — никаких алертов/задач → нули по всем счётчикам
@pytest.mark.asyncio
async def test_digest_empty_window_returns_zeros(
    pg_engine,
    clean_digest_e2e,
) -> None:
    # Никакого observer'а. Просто проверяем что build_digest корректно отдаёт
    # пустые поля без падений на partitioned-запросах.
    digest = await build_digest(
        pg_engine,
        day_start_utc=_utcnow() + timedelta(hours=1),
        window_hours=24,
    )
    assert digest.alerts_stop_count == 0
    assert digest.alerts_warning_count == 0
    assert digest.disable_tasks_succeeded == 0
    assert digest.disable_tasks_failed == 0
    assert digest.active_offers_count == 0
    assert digest.active_ads_count == 0
    assert digest.top_ads_by_spend == []
    assert digest.total_spend_24h_usd == Decimal("0")


# E2E: окно «вчера» — данные из сегодня в него не попадают (partition pruning).
@pytest.mark.asyncio
async def test_digest_yesterday_window_excludes_today(
    pg_engine,
    clean_digest_e2e,
) -> None:
    # Создаём данные сегодня
    await _seed_offer(pg_engine, code="WINDOW_T", cpa=Decimal("10"))
    fb_id = f"230066{uuid.uuid4().hex[:6]}"
    row = _row(code="WINDOW_T", fb_ad_id=fb_id, spend=Decimal("25"), deposits=0)
    await process_scan_rows(pg_engine, rows=[row], scan_id=200)

    # Запрашиваем дайджест за окно «вчера» (закончилось вчера в начале дня)
    yesterday_end = _utcnow() - timedelta(hours=24)
    digest = await build_digest(
        pg_engine,
        day_start_utc=yesterday_end,
        window_hours=24,
    )

    # alert_events за вчера = 0 (наша stop-запись попала в сегодня)
    assert digest.alerts_stop_count == 0
    assert digest.alerts_warning_count == 0
    # offers активны всегда — это глобальный счётчик
    assert digest.active_offers_count == 1
