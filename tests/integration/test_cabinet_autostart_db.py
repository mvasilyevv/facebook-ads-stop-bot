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

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.cabinet_scheduler.main import run_one_tick
from core.meta_api.bulk import resolve_owner_ad_ids_by_campaign_ids
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


def _row(fb_ad_id: str, campaign: str, campaign_id: str, ad_name: str = "AD") -> ScannedAdRow:
    """Минимальная строка скана; campaign_id → fb_campaigns.fb_campaign_id."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_name=campaign,
        campaign_id=campaign_id,
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


# ====================== resolve_owner_ad_ids_by_campaign_ids ======================


# Своя выбранная кампания (тег MV) включается; своя НЕ выбранная — нет
@pytest.mark.asyncio
async def test_resolve_by_campaign_selected_only(pg_engine, clean_autostart_tables) -> None:
    selected = _row("111000", "MV | KE | CR2 | 22.05", campaign_id="C100")
    other = _row("111001", "MV | KE | CR2 | 18.04", campaign_id="C200")
    await process_scan_rows(pg_engine, rows=[selected, other], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        pg_engine, owner_tag="MV", campaign_ids=["C100"]
    )
    assert ids == ["111000"], "только выбранная кампания C100"
    assert total == 1


# Чужая кампания (без owner-тега) НЕ включается, даже если её id выбран
@pytest.mark.asyncio
async def test_resolve_by_campaign_excludes_foreign(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111002", "MV | KE | CR2 | 22.05", campaign_id="C300")
    foreign = _row("222002", "MZ Artemteam CR2 CBO", campaign_id="C301")
    await process_scan_rows(pg_engine, rows=[mine, foreign], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        pg_engine, owner_tag="MV", campaign_ids=["C300", "C301"]
    )
    assert ids == ["111002"], "чужая кампания не должна попасть даже при выборе"
    assert total == 1


# Пустой список → пусто (НЕ включаем весь кабинет — безопасность)
@pytest.mark.asyncio
async def test_resolve_by_campaign_empty(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111003", "MV | KE | CR2 | 22.05", campaign_id="C400")
    await process_scan_rows(pg_engine, rows=[mine], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        pg_engine, owner_tag="MV", campaign_ids=[]
    )
    assert ids == []
    assert total == 0


# Не выбранная кампания (id не в списке) → пусто
@pytest.mark.asyncio
async def test_resolve_by_campaign_not_selected(pg_engine, clean_autostart_tables) -> None:
    mine = _row("111004", "MV | CR2 | 22.05", campaign_id="C500")
    await process_scan_rows(pg_engine, rows=[mine], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        pg_engine, owner_tag="MV", campaign_ids=["C999"]
    )
    assert ids == []
    assert total == 0


# Несколько выбранных кампаний: попадают все их активные объявления
@pytest.mark.asyncio
async def test_resolve_by_campaign_multiple(pg_engine, clean_autostart_tables) -> None:
    a = _row("111007", "MV | CR2 | 22.05", campaign_id="C600")
    b = _row("111008", "MV | CR2 | 25.05", campaign_id="C601")
    c = _row("111009", "MV | CR2 | 30.05", campaign_id="C602")
    await process_scan_rows(pg_engine, rows=[a, b, c], scan_id=1)

    ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        pg_engine, owner_tag="MV", campaign_ids=["C600", "C601"]
    )
    assert set(ids) == {"111007", "111008"}
    assert total == 2


# ====================== run_one_tick ======================


# В окне + enabled + своя выбранная кампания → pending bulk activate + scan trigger
@pytest.mark.asyncio
async def test_run_one_tick_starts_cabinet(
    pg_engine, fake_redis_client, clean_autostart_tables, monkeypatch
) -> None:
    # Чужой ad — в СВОЕЙ кампании (C701): в Meta у кампании ровно одно имя, два ad'а
    # одной кампании не могут иметь разные campaign_name (идентичность каталога —
    # fb_campaign_id, миграция 0020). Обе кампании в allowlist → проверяем, что
    # owner-scoping исключает чужую даже когда её id выбран.
    mine = _row("111100", "MV | KE | CR2 | 22.05", campaign_id="C700")
    foreign = _row("222100", "MZ Artemteam CR2", campaign_id="C701")
    await process_scan_rows(pg_engine, rows=[mine, foreign], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["C700", "C701"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
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
    mine = _row("111200", "MV | KE | CR2 | 22.05", campaign_id="C800")
    await process_scan_rows(pg_engine, rows=[mine], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["C800"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
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
        {"enabled": False, "hour_utc": 6, "minute_utc": 0},
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
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime(2026, 5, 29, 5, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now)
    assert summary["outcome"] == "not_in_window"
    assert await fake_redis_client.get(autostart_done_key(now)) is None


# Включено, в окне, но кампаний не выбрано → no_campaigns, ключ ставится (не дёргаем каждый тик)
@pytest.mark.asyncio
async def test_run_one_tick_no_campaigns(
    pg_engine, fake_redis_client, clean_autostart_tables
) -> None:
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime(2026, 5, 29, 6, 0, 0, tzinfo=timezone.utc)
    summary = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now)
    assert summary["outcome"] == "no_campaigns"
    assert await fake_redis_client.get(autostart_done_key(now)) == "1"


# Включено, в окне, но выбранная кампания чужая (нет owner-тега) → no_owner_ads, scan триггерим
@pytest.mark.asyncio
async def test_run_one_tick_no_owner_ads(
    pg_engine, fake_redis_client, clean_autostart_tables
) -> None:
    foreign = _row("222300", "MZ Artemteam CR2", campaign_id="C900")
    await process_scan_rows(pg_engine, rows=[foreign], scan_id=1)
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["C900"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
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


# ====================== R-money: фильтр свежести last_seen_at ======================


# Свежий ад (last_seen_at недавно) включается, протухший (давно не виден) — исключён.
# Защита от реактивации давно снятых ads: is_active=TRUE монотонно-истинный.
@pytest.mark.asyncio
async def test_resolve_by_campaign_freshness_filter(pg_engine, clean_autostart_tables) -> None:
    fresh = _row("111900", "MV | KE | CR2 | 22.05", campaign_id="C950")
    stale = _row("111901", "MV | KE | CR2 | 22.05", campaign_id="C950")
    await process_scan_rows(pg_engine, rows=[fresh, stale], scan_id=1)

    # Протухшему аду откатываем last_seen_at на 5 дней назад (старый cabinet-день).
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE fb_ads SET last_seen_at = NOW() - INTERVAL '5 days' "
                "WHERE fb_ad_id = '111901'"
            )
        )

    since = datetime.now(timezone.utc) - timedelta(hours=48)
    ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        pg_engine, owner_tag="MV", campaign_ids=["C950"], since=since
    )
    assert ids == ["111900"], "только свежий ад; протухший НЕ реактивируем"
    assert total == 1


# Без since (None) фильтр свежести выключен — обратная совместимость (включаются оба).
@pytest.mark.asyncio
async def test_resolve_by_campaign_no_since_returns_all(pg_engine, clean_autostart_tables) -> None:
    fresh = _row("111902", "MV | KE | CR2 | 22.05", campaign_id="C960")
    stale = _row("111903", "MV | KE | CR2 | 22.05", campaign_id="C960")
    await process_scan_rows(pg_engine, rows=[fresh, stale], scan_id=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE fb_ads SET last_seen_at = NOW() - INTERVAL '5 days' "
                "WHERE fb_ad_id = '111903'"
            )
        )

    ids, total = await resolve_owner_ad_ids_by_campaign_ids(
        pg_engine, owner_tag="MV", campaign_ids=["C960"]
    )
    assert set(ids) == {"111902", "111903"}, "без since оба ада (фильтр выключен)"
    assert total == 2


# run_one_tick автостарта НЕ включает протухший ад (передаёт since в резолв).
@pytest.mark.asyncio
async def test_run_one_tick_excludes_stale_ad(
    pg_engine, fake_redis_client, clean_autostart_tables
) -> None:
    fresh = _row("111904", "MV | KE | CR2 | 29.05", campaign_id="C970")
    stale = _row("111905", "MV | KE | CR2 | 29.05", campaign_id="C970")
    await process_scan_rows(pg_engine, rows=[fresh, stale], scan_id=1)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE fb_ads SET last_seen_at = NOW() - INTERVAL '5 days' "
                "WHERE fb_ad_id = '111905'"
            )
        )
    await _set_owner_tag(pg_engine, "MV", campaign_ids=["C970"])
    await write_autostart_config(
        pg_engine,
        {"enabled": True, "hour_utc": 6, "minute_utc": 0},
    )
    now = datetime.now(timezone.utc).replace(hour=6, minute=0, second=0, microsecond=0)
    summary = await run_one_tick(engine=pg_engine, redis_client=fake_redis_client, now=now)
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
