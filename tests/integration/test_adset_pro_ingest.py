# -*- coding: utf-8 -*-
"""Интеграционный: ingest_postback → adsetpro_postback_events с дедупом.

Использует реальный Postgres (pg_engine fixture) — миграция Волны 3 должна быть
применена (Alembic upgrade head либо apply_v2_schema).
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


@pytest_asyncio.fixture
async def clean_adsetpro_events(pg_engine):
    """Чистит adsetpro_postback_events до и после теста, чтобы не нести шум между тестами."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM adsetpro_postback_events"))

    await _truncate()
    yield
    await _truncate()


async def _get_fb_ad_id_string(pg_engine, ad_uuid: uuid.UUID) -> str:
    """Достать строковый fb_ad_id (VARCHAR) для UUID из fb_ad_fixture."""
    async with pg_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT fb_ad_id FROM fb_ads WHERE id = :i"),
            {"i": ad_uuid},
        )
        return result.scalar_one()


# Полный flow: новый postback → INSERT, fb_ad_fk резолвится через fb_ads.fb_ad_id.
@pytest.mark.asyncio
async def test_ingest_inserts_and_resolves_fb_ad_fk(
    pg_engine, fb_ad_fixture, clean_adsetpro_events
) -> None:
    fb_ad_id = await _get_fb_ad_id_string(pg_engine, fb_ad_fixture.ad_id)
    event = PostbackEvent(
        click_id="click-resolve-1",
        fb_ad_id=fb_ad_id,
        event_type="ftd",
        revenue=Decimal("25.00"),
        currency="USD",
        received_at=datetime.now(UTC),
        raw={"sub6": fb_ad_id, "goal": "ftd"},
    )

    result = await ingest_postback(pg_engine, event)

    assert result.inserted is True
    assert result.is_duplicate is False
    assert result.event_id is not None
    assert result.fb_ad_fk == fb_ad_fixture.ad_id

    # Проверяем, что строка действительно в БД с правильным fb_ad_fk.
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT fb_ad_fk, event_type, currency, signature_valid "
                    "FROM adsetpro_postback_events WHERE id = :i"
                ),
                {"i": result.event_id},
            )
        ).first()
    assert row.fb_ad_fk == fb_ad_fixture.ad_id
    assert row.event_type == "ftd"
    assert row.currency == "USD"
    assert row.signature_valid is True


# Дубль (тот же click_id + event_type в окне 24h) → is_duplicate=True, без второго INSERT'а.
@pytest.mark.asyncio
async def test_ingest_marks_duplicate_within_window(
    pg_engine, fb_ad_fixture, clean_adsetpro_events
) -> None:
    fb_ad_id = await _get_fb_ad_id_string(pg_engine, fb_ad_fixture.ad_id)
    base_payload = {
        "click_id": "click-dup-1",
        "fb_ad_id": fb_ad_id,
        "event_type": "ftd",
        "revenue": Decimal("12.00"),
        "currency": "USD",
        "raw": {"x": 1},
    }
    first = PostbackEvent(received_at=datetime.now(UTC), **base_payload)
    r1 = await ingest_postback(pg_engine, first)
    assert r1.inserted is True

    # Второй раз тот же click_id + event_type через 5 минут — должен быть дубль.
    second = PostbackEvent(
        received_at=datetime.now(UTC) + timedelta(minutes=5),
        **base_payload,
    )
    r2 = await ingest_postback(pg_engine, second)
    assert r2.inserted is False
    assert r2.is_duplicate is True
    assert r2.event_id is None

    # В БД только одна запись для (click_id='click-dup-1', event_type='ftd').
    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM adsetpro_postback_events "
                    "WHERE click_id = 'click-dup-1' AND event_type = 'ftd'"
                )
            )
        ).scalar()
    assert count == 1


# Без fb_ad_id в payload → fb_ad_fk остаётся NULL, запись всё равно вставлена.
@pytest.mark.asyncio
async def test_ingest_without_fb_ad_id_leaves_fk_null(pg_engine, clean_adsetpro_events) -> None:
    event = PostbackEvent(
        click_id="click-nofb-1",
        fb_ad_id=None,
        event_type="reg",
        revenue=Decimal(0),
        currency="USD",
        received_at=datetime.now(UTC),
        raw={"goal": "reg"},
    )
    result = await ingest_postback(pg_engine, event)

    assert result.inserted is True
    assert result.fb_ad_fk is None

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT fb_ad_fk FROM adsetpro_postback_events WHERE id = :i"),
                {"i": result.event_id},
            )
        ).first()
    assert row.fb_ad_fk is None


# Разные event_type на одном click_id (например ftd + redep) — это РАЗНЫЕ события,
# обе строки должны быть вставлены, дубля нет.
@pytest.mark.asyncio
async def test_ingest_distinct_event_types_not_deduped(
    pg_engine, fb_ad_fixture, clean_adsetpro_events
) -> None:
    fb_ad_id = await _get_fb_ad_id_string(pg_engine, fb_ad_fixture.ad_id)
    now = datetime.now(UTC)

    ftd = PostbackEvent(
        click_id="click-multi-1",
        fb_ad_id=fb_ad_id,
        event_type="ftd",
        revenue=Decimal("10"),
        currency="USD",
        received_at=now,
        raw={},
    )
    redep = PostbackEvent(
        click_id="click-multi-1",
        fb_ad_id=fb_ad_id,
        event_type="redep",
        revenue=Decimal("5"),
        currency="USD",
        received_at=now + timedelta(seconds=1),
        raw={},
    )

    r1 = await ingest_postback(pg_engine, ftd)
    r2 = await ingest_postback(pg_engine, redep)

    assert r1.inserted is True
    assert r2.inserted is True

    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM adsetpro_postback_events WHERE click_id = 'click-multi-1'"
                )
            )
        ).scalar()
    assert count == 2


# Неизвестный fb_ad_id (нет в fb_ads) → fb_ad_fk остаётся NULL, не падаем.
@pytest.mark.asyncio
async def test_ingest_unknown_fb_ad_id_no_match(pg_engine, clean_adsetpro_events) -> None:
    event = PostbackEvent(
        click_id="click-unknown-1",
        fb_ad_id="2399999999999999",  # такой fb_ad_id вряд ли есть
        event_type="ftd",
        revenue=Decimal("8"),
        currency="USD",
        received_at=datetime.now(UTC),
        raw={},
    )
    result = await ingest_postback(pg_engine, event)
    assert result.inserted is True
    assert result.fb_ad_fk is None
