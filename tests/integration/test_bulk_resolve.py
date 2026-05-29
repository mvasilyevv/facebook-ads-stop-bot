# -*- coding: utf-8 -*-
"""Интеграционные тесты owner-scoped резолва для массовых mutations (/pause /resume).

Главное — безопасность: массовое отключение по offer-коду НЕ должно задеть чужие
кампании в общем кабинете. resolve_owner_ad_ids фильтрует по owner-тегу.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.meta_api.bulk import resolve_owner_ad_ids
from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow
from core.telegram.handlers.bulk import handle_bulk_toggle


class _FakeClient:
    """Минимальный фейк TelegramBotClient — пишет отправленные сообщения."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_message(self, **kwargs) -> None:
        self.sent.append(kwargs)


@pytest_asyncio.fixture
async def setup_offer_cr2(pg_engine):
    """Чистит наблюдательные таблицы + создаёт оффер CR2."""

    async def _trunc():
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

    await _trunc()
    oid = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, 'CR2', 'cr', TRUE)"),
            {"i": oid},
        )
        await conn.execute(
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:i, 3.00)"),
            {"i": oid},
        )
    yield
    await _trunc()


def _row(fb_ad_id: str, campaign: str, ad_name: str) -> ScannedAdRow:
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=campaign,
        adset_name="as",
        ad_name=ad_name,
        delivery_status="ACTIVE",
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


# Owner-scoped резолв: при owner_tag=MV чужая кампания с тем же offer-кодом отсеивается
@pytest.mark.asyncio
async def test_resolve_owner_ad_ids_filters_foreign(pg_engine, setup_offer_cr2) -> None:
    mine = _row("111000", "MV | KE | CR2 | adset.pro", "KE_CR2_CR001")
    foreign = _row("222000", "14.05 MZ Artemteam CR2 CBO", "FW3-5")
    # owner_tag=None при scan → обе кампании попадают в каталог
    await process_scan_rows(pg_engine, rows=[mine, foreign], scan_id=1)

    ids, total = await resolve_owner_ad_ids(pg_engine, offer_code="CR2", owner_tag="MV")
    assert ids == ["111000"], "должна остаться только MV-кампания"
    assert total == 1


# Без owner-тега резолвер возвращает все объявления оффера (фильтр выключен)
@pytest.mark.asyncio
async def test_resolve_no_owner_returns_all(pg_engine, setup_offer_cr2) -> None:
    mine = _row("111001", "MV | KE | CR2", "KE_CR2_CR001")
    other = _row("222001", "ABC | GH | CR2", "GH_CR2_CR001")
    await process_scan_rows(pg_engine, rows=[mine, other], scan_id=1)

    ids, total = await resolve_owner_ad_ids(pg_engine, offer_code="CR2", owner_tag=None)
    assert set(ids) == {"111001", "222001"}
    assert total == 2


# Мультитег: owner_tag="MV,ABC" → обе свои кампании (MV и ABC) попадают
@pytest.mark.asyncio
async def test_resolve_owner_multitag(pg_engine, setup_offer_cr2) -> None:
    mv = _row("111002", "MV | KE | CR2", "KE_CR2_CR001")
    abc = _row("222002", "ABC | GH | CR2", "GH_CR2_CR001")
    foreign = _row("333002", "MZ Artemteam CR2", "x")
    await process_scan_rows(pg_engine, rows=[mv, abc, foreign], scan_id=1)

    ids, total = await resolve_owner_ad_ids(pg_engine, offer_code="CR2", owner_tag="MV,ABC")
    assert set(ids) == {"111002", "222002"}, "MV и ABC — свои, MZ — чужая"
    assert total == 2


# /pause через handler: создаёт DRAFT bulk_status_change, чужой ad НЕ попадает в задачу
@pytest.mark.asyncio
async def test_handle_bulk_toggle_creates_owner_scoped_draft(pg_engine, setup_offer_cr2) -> None:
    mine = _row("111003", "MV | KE | CR2", "KE_CR2_CR001")
    foreign = _row("222003", "MZ Artemteam CR2", "x")
    await process_scan_rows(pg_engine, rows=[mine, foreign], scan_id=1)
    # owner_tag=MV в observer_config
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO observer_config (singleton_key, owner_campaign_tag) "
                "VALUES ('default', 'MV') "
                "ON CONFLICT (singleton_key) DO UPDATE SET owner_campaign_tag = 'MV'"
            )
        )

    client = _FakeClient()
    await handle_bulk_toggle(
        engine=pg_engine,
        client=client,
        chat_id=555,
        message_id=1,
        thread_id=None,
        username="mark",
        command="pause",
        args_text="CR2",
    )

    # DRAFT создан, action=pause
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT status, payload FROM task_queue "
                    "WHERE task_type = 'meta_api_mutation' ORDER BY created_at DESC LIMIT 1"
                )
            )
        ).first()
    assert row is not None, "draft-задача должна быть создана"
    assert row[0] == "draft"
    payload_str = str(row[1])
    assert "pause" in payload_str
    assert "111003" in payload_str, "мой ad в задаче"
    assert "222003" not in payload_str, "чужой ad НЕ должен попасть в массовую паузу"

    # Превью с кнопками ✅/❌ отправлено
    assert client.sent, "должно прийти превью черновика"
    assert "inline_keyboard" in client.sent[-1].get("reply_markup", {})
