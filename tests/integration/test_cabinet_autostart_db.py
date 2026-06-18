# -*- coding: utf-8 -*-
"""Интеграционные тесты автостарта кабинета по расписанию (money-критично).

Главное — безопасность:
- резолв по дате owner-scoped: своя кампания с датой включается, своя без даты — нет,
  чужая (без owner-тега) с датой — НЕ включается, пустой список дат → пусто;
- run_one_tick создаёт ОДНУ pending-задачу bulk_status_change activate и триггерит
  observer scan, повторный тик в тот же день дедуплицируется (already_done).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.cabinet_scheduler.main import run_one_tick
from core.meta_api.bulk import resolve_owner_ad_ids_by_dates
from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow
from core.scheduler.cabinet_autostart import autostart_done_key, write_autostart_config


@pytest_asyncio.fixture
async def clean_autostart_tables(pg_engine):
    """Чистит каталог + task_queue + observer/system конфиги до и после теста."""

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
            await conn.execute(text("DELETE FROM system_config WHERE key = 'cabinet_autostart'"))
            await conn.execute(text("DELETE FROM observer_config WHERE singleton_key = 'default'"))

    await _trunc()
    yield
    await _trunc()


def _row(fb_ad_id: str, campaign: str, ad_name: str = "AD") -> ScannedAdRow:
    """Минимальная строка скана (метрики не важны для резолва по дате)."""
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


async def _set_owner_tag(pg_engine, tag: str | None) -> None:
    """Кладёт owner_campaign_tag в observer_config + включает сканирование.

    is_scanning_enabled server_default=FALSE (scanning OFF by default), а autostart-тик
    при выключенном сканировании сразу возвращает 'scanning_paused'. Явно включаем.
    """
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO observer_config (singleton_key, owner_campaign_tag, is_scanning_enabled)
                VALUES ('default', :tag, TRUE)
                ON CONFLICT (singleton_key)
                DO UPDATE SET owner_campaign_tag = :tag, is_scanning_enabled = TRUE
                """
            ),
            {"tag": tag},
        )


# ====================== resolve_owner_ad_ids_by_dates ======================


# Своя кампания (тег MV) с датой 22.05 включается; своя без даты — нет
@pytest.mark.asyncio
async def test_resolve_by_date_owner_with_date(pg_engine, clean_autostart_tables) -> None:
    with_date = _row("111000", "MV | KE | CR2 | 22.05")
    no_date = _row("111001", "MV | KE | CR2 | 18.04")
    await process_scan_rows(pg_engine, rows=[with_date, no_date], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_dates(pg_engine, owner_tag="MV", dates=["22.05"])
    assert ids == ["111000"], "только кампания с датой 22.05"
    assert total == 1


# Чужая кампания (без owner-тега) с датой 22.05 НЕ включается
@pytest.mark.asyncio
async def test_resolve_by_date_excludes_foreign(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111002", "MV | KE | CR2 | 22.05")
    foreign = _row("222002", "22.05 MZ Artemteam CR2 CBO")
    await process_scan_rows(pg_engine, rows=[mine, foreign], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_dates(pg_engine, owner_tag="MV", dates=["22.05"])
    assert ids == ["111002"], "чужая кампания с той же датой не должна попасть"
    assert total == 1


# Пустой список дат → пусто (НЕ включаем весь кабинет — безопасность)
@pytest.mark.asyncio
async def test_resolve_by_date_empty_dates(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111003", "MV | KE | CR2 | 22.05")
    await process_scan_rows(pg_engine, rows=[mine], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_dates(pg_engine, owner_tag="MV", dates=[])
    assert ids == []
    assert total == 0


# Word-boundary: дата "22.05" не матчит "122.05" и "22.057"
@pytest.mark.asyncio
async def test_resolve_by_date_word_boundary(pg_engine, clean_autostart_tables) -> None:
    exact = _row("111004", "MV | CR2 | 22.05")
    prefixed = _row("111005", "MV | CR2 | 122.05")
    suffixed = _row("111006", "MV | CR2 | 22.057")
    await process_scan_rows(pg_engine, rows=[exact, prefixed, suffixed], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_dates(pg_engine, owner_tag="MV", dates=["22.05"])
    assert ids == ["111004"], "только точное совпадение даты, без 122.05 / 22.057"
    assert total == 1


# Несколько дат: кампания подходит если содержит ЛЮБУЮ из них
@pytest.mark.asyncio
async def test_resolve_by_date_multiple_dates(pg_engine, clean_autostart_tables) -> None:
    d22 = _row("111007", "MV | CR2 | 22.05")
    d25 = _row("111008", "MV | CR2 | 25.05")
    other = _row("111009", "MV | CR2 | 30.05")
    await process_scan_rows(pg_engine, rows=[d22, d25, other], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_dates(
        pg_engine, owner_tag="MV", dates=["22.05", "25.05"]
    )
    assert set(ids) == {"111007", "111008"}
    assert total == 2


# ====================== run_one_tick ======================


# В окне + enabled + owner-кампания с датой → pending bulk_status_change activate + scan trigger
@pytest.mark.asyncio
async def test_run_one_tick_starts_cabinet(
    pg_engine, fake_redis_client, clean_autostart_tables, monkeypatch
) -> None:
    mine = _row("111100", "MV | KE | CR2 | 22.05")
    foreign = _row("222100", "22.05 MZ Artemteam CR2")
    await process_scan_rows(pg_engine, rows=[mine, foreign], scan_id=1)
    await _set_owner_tag(pg_engine, "MV")
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0, "dates": ["22.05"]},
    )

    # Перехватываем publish, чтобы проверить scan-trigger.
    published: list[tuple[str, str]] = []
    orig_publish = fake_redis_client.publish

    async def _spy_publish(channel, message):
        published.append((channel, message))
        return await orig_publish(channel, message)

    monkeypatch.setattr(fake_redis_client, "publish", _spy_publish)

    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now)

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
    assert "222100" not in payload_dict["params"]["ad_ids"], "чужой ad не включаем"

    # Scan-trigger опубликован в правильный канал.
    assert any(ch == "fb_agent:observer:trigger" for ch, _ in published)

    # Дедуп-ключ выставлен.
    assert await fake_redis_client.get(autostart_done_key(now)) == "1"


# Повторный тик в тот же день → already_done, дубль-задачи не создаётся
@pytest.mark.asyncio
async def test_run_one_tick_dedup_same_day(
    pg_engine, fake_redis_client, clean_autostart_tables
) -> None:
    mine = _row("111200", "MV | KE | CR2 | 22.05")
    await process_scan_rows(pg_engine, rows=[mine], scan_id=1)
    await _set_owner_tag(pg_engine, "MV")
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0, "dates": ["22.05"]},
    )

    now1 = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    now2 = datetime(2026, 5, 29, 6, 1, 0, tzinfo=timezone.utc)

    first = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now1)
    second = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now2)

    assert first["outcome"] == "started"
    assert second["outcome"] == "already_done"

    # Только одна задача — повторный тик не задвоил enable.
    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'meta_api_mutation'")
            )
        ).scalar_one()
    assert count == 1, "дедуп: повторный тик не создаёт дубль enable-задачи"


# Фича выключена → ничего не делаем (disabled), задач нет, ключ не ставится
@pytest.mark.asyncio
async def test_run_one_tick_disabled(pg_engine, fake_redis_client, clean_autostart_tables) -> None:
    await write_autostart_config(
        pg_engine,
        {"enabled": False, "hour_utc": 6, "minute_utc": 0, "dates": ["22.05"]},
    )
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now)
    assert summary["outcome"] == "disabled"
    assert await fake_redis_client.get(autostart_done_key(now)) is None


# Не в окне (до планового времени) → not_in_window, ключ не ставится
@pytest.mark.asyncio
async def test_run_one_tick_not_in_window(
    pg_engine, fake_redis_client, clean_autostart_tables
) -> None:
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0, "dates": ["22.05"]},
    )
    now = datetime(2026, 5, 29, 5, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now)
    assert summary["outcome"] == "not_in_window"
    assert await fake_redis_client.get(autostart_done_key(now)) is None


# Включено, в окне, но owner-кампаний с датой нет → no_owner_ads, но scan всё равно триггерим
@pytest.mark.asyncio
async def test_run_one_tick_no_owner_ads(
    pg_engine, fake_redis_client, clean_autostart_tables
) -> None:
    foreign = _row("222300", "22.05 MZ Artemteam CR2")
    await process_scan_rows(pg_engine, rows=[foreign], scan_id=1)
    await _set_owner_tag(pg_engine, "MV")
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0, "dates": ["22.05"]},
    )
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now)
    assert summary["outcome"] == "no_owner_ads"

    # Задач нет (чужая кампания не включается).
    async with pg_engine.connect() as conn:
        count = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE task_type = 'meta_api_mutation'")
            )
        ).scalar_one()
    assert count == 0
