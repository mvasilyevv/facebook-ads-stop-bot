# -*- coding: utf-8 -*-
"""Integration: concurrent ingest того же click_id — двухступенчатый дедуп.

HIGH #8 из backend_test_audit_round_8: core/adset_pro/ingest использует двухступенчатый
дедуп (pre-INSERT SELECT + ON CONFLICT DO NOTHING), но гонка SELECT→INSERT→INSERT между
двумя параллельными процессами не была покрыта тестами.

Сценарии:
1. 100 параллельных ingest_postback с одним click_id+event_type:
   ровно один inserted=True, остальные audit-строки с
   is_duplicate=TRUE, и ровно одна задача обработки.
2. Тот же click_id, разные event_type → не является дублем → 2 записи в БД.
3. One-shot дедуп не истекает внутри retention: поздний повтор остаётся дублем.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.adset_pro import PostbackEvent
from core.adset_pro.ingest import ingest_postback


@pytest_asyncio.fixture
async def clean_concurrent_events(pg_engine):
    """Чистит adsetpro_postback_events до и после теста."""

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


def _make_event(
    *,
    click_id: str,
    event_type: str = "ftd",
    received_at: datetime | None = None,
) -> PostbackEvent:
    """Фабрика PostbackEvent с минимальными полями."""
    return PostbackEvent(
        click_id=click_id,
        fb_ad_id=None,  # нет нужды резолвить fb_ad_fk в этих тестах
        event_type=event_type,
        revenue=Decimal("10.00"),
        currency="USD",
        received_at=received_at or datetime.now(UTC),
        raw={"goal": event_type},
    )


# 100 параллельных ingest одного click_id → 1 canonical + 99 audit rows.
@pytest.mark.asyncio
async def test_concurrent_same_click_id_dedup(pg_engine, clean_concurrent_events) -> None:
    """100 concurrent deliveries create one fact, 99 audit rows and one task."""
    click_id = "concurrent-click-001"
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
    events = [
        _make_event(click_id=click_id, received_at=now + timedelta(microseconds=index))
        for index in range(100)
    ]

    results = await asyncio.gather(
        *(ingest_postback(pg_engine, event) for event in events),
        return_exceptions=False,
    )

    inserted_count = sum(1 for r in results if r.inserted)
    duplicate_count = sum(1 for r in results if r.is_duplicate)

    # Ровно один должен победить в гонке
    assert inserted_count == 1, (
        f"Ровно один ingest должен вставить запись, вставили: {inserted_count}"
    )
    assert duplicate_count == 99, (
        f"Остальные 99 должны быть is_duplicate=True, дублей: {duplicate_count}"
    )

    async with pg_engine.connect() as conn:
        counts = (
            await conn.execute(
                text(
                    "SELECT COUNT(*), "
                    "COUNT(*) FILTER (WHERE is_duplicate = FALSE), "
                    "COUNT(*) FILTER (WHERE is_duplicate = TRUE) "
                    "FROM adsetpro_postback_events "
                    "WHERE click_id = :cid AND event_type = 'ftd'"
                ),
                {"cid": click_id},
            )
        ).one()
        task_count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE task_type='tracker_event_process'")
            )
        ).scalar_one()
    assert tuple(counts) == (100, 1, 99)
    assert task_count == 1


# Тот же click_id, разные event_type → обе записи вставляются
@pytest.mark.asyncio
async def test_concurrent_different_event_types_not_deduped(
    pg_engine, clean_concurrent_events
) -> None:
    """Тот же click_id но разные event_type — это разные события, оба INSERT'ятся."""
    click_id = "concurrent-click-002"
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)

    ftd_event = _make_event(click_id=click_id, event_type="ftd", received_at=now)
    redep_event = _make_event(click_id=click_id, event_type="registration", received_at=now)

    r1, r2 = await asyncio.gather(
        ingest_postback(pg_engine, ftd_event),
        ingest_postback(pg_engine, redep_event),
    )

    assert r1.inserted is True
    assert r2.inserted is True

    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM adsetpro_postback_events WHERE click_id = :cid"),
                {"cid": click_id},
            )
        ).scalar()
    assert count == 2, f"Два разных event_type должны дать 2 записи, нашли: {count}"


# One-shot event remains duplicate regardless of delivery delay within retention.
@pytest.mark.asyncio
async def test_ingest_outside_dedup_window_inserts_again(
    pg_engine, clean_concurrent_events
) -> None:
    """Поздний повтор FTD — duplicate audit-row, не новая бизнес-конверсия."""
    click_id = "concurrent-click-003"

    # Первая запись — вчера
    yesterday = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    r1 = await ingest_postback(pg_engine, _make_event(click_id=click_id, received_at=yesterday))
    assert r1.inserted is True

    # Та же записи сегодня — >24h разница → новый click из окна дедупа
    today = datetime(2026, 5, 28, 13, 0, 0, tzinfo=UTC)
    r2 = await ingest_postback(pg_engine, _make_event(click_id=click_id, received_at=today))

    assert r2.inserted is False
    assert r2.is_duplicate is True

    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM adsetpro_postback_events WHERE click_id = :cid"),
                {"cid": click_id},
            )
        ).scalar()
    assert count == 2, f"business row + duplicate audit-row expected, got: {count}"
