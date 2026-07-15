# -*- coding: utf-8 -*-
"""Интеграционный: ingest_postback → adsetpro_postback_events с дедупом.

Использует реальный Postgres (pg_engine fixture) — миграция Волны 3 должна быть
применена (Alembic upgrade head либо apply_schema).
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
            await conn.execute(
                text("DELETE FROM task_queue WHERE task_type='tracker_event_process'")
            )
            await conn.execute(text("DELETE FROM tracker_click_state"))
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
        raw={"sub8": fb_ad_id, "goal": "ftd"},
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


# Дубль сохраняется отдельной audit-строкой is_duplicate=true, но без processing task.
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
    assert r2.event_id is not None

    # Одна бизнес-строка + одна duplicate audit-row.
    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text(
                    "SELECT COUNT(*), COUNT(*) FILTER (WHERE is_duplicate) "
                    "FROM adsetpro_postback_events "
                    "WHERE click_id = 'click-dup-1' AND event_type = 'ftd'"
                )
            )
        ).one()
    assert count == (2, 1)


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
        event_type="redeposit",
        revenue=Decimal("5"),
        currency="USD",
        received_at=now + timedelta(seconds=1),
        provider_event_id="multi-redeposit-1",
        raw={"transaction_id": "multi-redeposit-1"},
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


@pytest.mark.asyncio
async def test_legacy_exact_attribution_normalizes_act_account_prefix(
    pg_engine, fb_ad_fixture, clean_adsetpro_events
) -> None:
    account = str(uuid.uuid4().int % 10**12)
    async with pg_engine.begin() as conn:
        hierarchy = (
            await conn.execute(
                text(
                    """
                    SELECT c.id, c.campaign_name, s.adset_name, a.ad_name
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE a.id = :ad_id
                    """
                ),
                {"ad_id": fb_ad_fixture.ad_id},
            )
        ).one()
        await conn.execute(
            text("UPDATE fb_campaigns SET ad_account_id = :account WHERE id = :id"),
            {"account": f"act_{account}", "id": hierarchy[0]},
        )

    event = PostbackEvent(
        click_id=f"legacy-prefix-{uuid.uuid4().hex}",
        fb_ad_id=None,
        event_type="registration",
        revenue=Decimal("0"),
        currency="USD",
        received_at=datetime.now(UTC),
        raw={
            "sub4": account,
            "sub5": hierarchy[1],
            "sub6": hierarchy[2],
            "sub7": hierarchy[3],
        },
    )

    result = await ingest_postback(pg_engine, event)
    assert result.fb_ad_fk == fb_ad_fixture.ad_id
    assert result.attribution_status == "matched_legacy"


@pytest.mark.asyncio
async def test_ext_sub6_is_never_interpreted_as_fb_ad_id(
    pg_engine, fb_ad_fixture, clean_adsetpro_events
) -> None:
    fb_ad_id = await _get_fb_ad_id_string(pg_engine, fb_ad_fixture.ad_id)
    result = await ingest_postback(
        pg_engine,
        PostbackEvent(
            click_id=f"ext-sub6-{uuid.uuid4().hex}",
            fb_ad_id=None,
            event_type="registration",
            revenue=Decimal("0"),
            currency="USD",
            received_at=datetime.now(UTC),
            raw={"ext_sub6": fb_ad_id},
        ),
    )
    assert result.fb_ad_fk is None
    assert result.attribution_status == "unmatched"


# ─── Redeposit is analytics-only and requires a stable provider id ───────────


def _redep_event(click_id: str, received_at: datetime, raw: dict | None = None) -> PostbackEvent:
    return PostbackEvent(
        click_id=click_id,
        fb_ad_id=None,
        event_type="redeposit",
        revenue=Decimal("25"),
        currency="USD",
        received_at=received_at,
        raw=raw or {},
    )


# Без provider transaction id redeposit игнорируется до записи.
@pytest.mark.asyncio
async def test_ingest_redep_repeat_after_retry_window_inserted(
    pg_engine, clean_adsetpro_events
) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="requires provider_event_id"):
        await ingest_postback(pg_engine, _redep_event("rdp-repeat", now))

    async with pg_engine.connect() as conn:
        cnt = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM adsetpro_postback_events "
                    "WHERE click_id = 'rdp-repeat' AND event_type = 'redeposit'"
                )
            )
        ).scalar_one()
    assert cnt == 0


# Без provider id redeposit не использует эвристическое временное окно.
@pytest.mark.asyncio
async def test_ingest_redep_retry_within_window_deduped(pg_engine, clean_adsetpro_events) -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="requires provider_event_id"):
        await ingest_postback(pg_engine, _redep_event("rdp-retry", now - timedelta(minutes=3)))


# Два redep с РАЗНЫМИ txn-id в пределах минут — оба реальные депозиты, оба вставляются.
@pytest.mark.asyncio
async def test_ingest_redep_distinct_txn_ids_both_inserted(
    pg_engine, clean_adsetpro_events
) -> None:
    now = datetime.now(UTC)
    r1 = await ingest_postback(
        pg_engine,
        _redep_event("rdp-txn", now - timedelta(minutes=2), raw={"transaction_id": "tx-1"}),
    )
    r2 = await ingest_postback(
        pg_engine, _redep_event("rdp-txn", now, raw={"transaction_id": "tx-2"})
    )
    assert r1.inserted is True
    assert r2.inserted is True, "другой txn-id = другой депозит, дедуп не должен глотать"


# Ретрай с ТЕМ ЖЕ txn-id — дубль даже спустя часы (точный дедуп по транзакции).
@pytest.mark.asyncio
async def test_ingest_redep_same_txn_id_deduped_across_hours(
    pg_engine, clean_adsetpro_events
) -> None:
    now = datetime.now(UTC)
    r1 = await ingest_postback(
        pg_engine,
        _redep_event("rdp-sametxn", now - timedelta(hours=3), raw={"transaction_id": "tx-9"}),
    )
    r2 = await ingest_postback(
        pg_engine, _redep_event("rdp-sametxn", now, raw={"transaction_id": "tx-9"})
    )
    assert r1.inserted is True
    assert r2.inserted is False and r2.is_duplicate is True
    assert r2.event_id is not None


# FTD is one-shot for the full retained source+click lifetime.
@pytest.mark.asyncio
async def test_ingest_ftd_still_deduped_within_24h(pg_engine, clean_adsetpro_events) -> None:
    now = datetime.now(UTC)

    def _ftd(received_at: datetime) -> PostbackEvent:
        return PostbackEvent(
            click_id="ftd-24h",
            fb_ad_id=None,
            event_type="ftd",
            revenue=Decimal("50"),
            currency="USD",
            received_at=received_at,
            raw={},
        )

    r1 = await ingest_postback(pg_engine, _ftd(now - timedelta(hours=5)))
    r2 = await ingest_postback(pg_engine, _ftd(now))
    assert r1.inserted is True
    assert r2.inserted is False and r2.is_duplicate is True
