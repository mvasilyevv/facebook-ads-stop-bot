# -*- coding: utf-8 -*-
"""Integration: concurrent ingest того же click_id — двухступенчатый дедуп.

HIGH #8 из backend_test_audit_round_8: core/adset_pro/ingest использует двухступенчатый
дедуп (pre-INSERT SELECT + ON CONFLICT DO NOTHING), но гонка SELECT→INSERT→INSERT между
двумя параллельными процессами не была покрыта тестами.

Сценарии:
1. 5 параллельных ingest_postback с одним click_id+event_type+received_at:
   ровно один inserted=True, остальные inserted=False, COUNT(*) в БД = 1.
2. Тот же click_id, разные event_type → не является дублем → 2 записи в БД.
3. Тот же click_id, received_at вне dedup-window (>24h назад) → новая запись.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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


# 5 параллельных ingest одного click_id → ровно 1 inserted=True, COUNT(*) == 1
@pytest.mark.asyncio
async def test_concurrent_same_click_id_dedup(pg_engine, clean_concurrent_events) -> None:
    """5 параллельных ingest с одним click_id → ровно одна запись в БД."""
    click_id = "concurrent-click-001"
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
    event = _make_event(click_id=click_id, received_at=now)

    # Запускаем 5 ingest параллельно — race condition между SELECT и INSERT
    results = await asyncio.gather(
        *[ingest_postback(pg_engine, event) for _ in range(5)],
        return_exceptions=False,
    )

    inserted_count = sum(1 for r in results if r.inserted)
    duplicate_count = sum(1 for r in results if r.is_duplicate)

    # Ровно один должен победить в гонке
    assert inserted_count == 1, (
        f"Ровно один ingest должен вставить запись, вставили: {inserted_count}"
    )
    assert duplicate_count == 4, (
        f"Остальные 4 должны быть is_duplicate=True, дублей: {duplicate_count}"
    )

    # В БД ровно одна строка
    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM adsetpro_postback_events "
                    "WHERE click_id = :cid AND event_type = 'ftd'"
                ),
                {"cid": click_id},
            )
        ).scalar()
    assert count == 1, f"В БД должна быть ровно 1 запись, нашли: {count}"


# Тот же click_id, разные event_type → обе записи вставляются
@pytest.mark.asyncio
async def test_concurrent_different_event_types_not_deduped(
    pg_engine, clean_concurrent_events
) -> None:
    """Тот же click_id но разные event_type — это разные события, оба INSERT'ятся."""
    click_id = "concurrent-click-002"
    now = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)

    ftd_event = _make_event(click_id=click_id, event_type="ftd", received_at=now)
    redep_event = _make_event(click_id=click_id, event_type="redep", received_at=now)

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


# Тот же click_id, received_at за пределами dedup-window (>24h) → новый INSERT
@pytest.mark.asyncio
async def test_ingest_outside_dedup_window_inserts_again(
    pg_engine, clean_concurrent_events
) -> None:
    """Тот же click_id, но received_at >24h от первого → за пределами окна → не дубль."""
    click_id = "concurrent-click-003"

    # Первая запись — вчера
    yesterday = datetime(2026, 5, 27, 12, 0, 0, tzinfo=UTC)
    r1 = await ingest_postback(pg_engine, _make_event(click_id=click_id, received_at=yesterday))
    assert r1.inserted is True

    # Та же записи сегодня — >24h разница → новый click из окна дедупа
    today = datetime(2026, 5, 28, 13, 0, 0, tzinfo=UTC)
    r2 = await ingest_postback(pg_engine, _make_event(click_id=click_id, received_at=today))

    # Должен быть вставлен, т.к. вышел за 24h-окно дедупа
    assert r2.inserted is True, (
        "За пределами 24h dedup-window тот же click_id должен вставиться заново"
    )

    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM adsetpro_postback_events WHERE click_id = :cid"),
                {"cid": click_id},
            )
        ).scalar()
    assert count == 2, f"Два инсерта вне окна → 2 записи, нашли: {count}"
