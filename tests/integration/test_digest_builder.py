# -*- coding: utf-8 -*-
"""Интеграционные тесты core/telegram/digest_builder.build_digest на реальной БД.

Запросы на partitioned таблицы (alert_events, ad_metrics) с явными границами
по партиционному ключу — проверяем, что агрегаты корректные.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.telegram.digest_builder import build_digest


@pytest_asyncio.fixture
async def clean_digest_tables(pg_engine):
    """Чистит таблицы, по которым агрегируем digest, до и после теста."""

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


@pytest_asyncio.fixture
async def two_ads_world(pg_engine, clean_digest_tables):
    """Создаёт 2 ad'а с офферами CR2 и KE2 — фикстура для всех тестов."""
    offer_a = uuid.uuid4()
    offer_b = uuid.uuid4()
    campaign_a = uuid.uuid4()
    campaign_b = uuid.uuid4()
    adset_a = uuid.uuid4()
    adset_b = uuid.uuid4()
    ad_a = uuid.uuid4()
    ad_b = uuid.uuid4()

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active) VALUES "
                "(:a, 'DIG_A', 'Offer A', TRUE), (:b, 'DIG_B', 'Offer B', TRUE)"
            ),
            {"a": offer_a, "b": offer_b},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES "
                "(:ca, 'DIG_A | KE', :a), (:cb, 'DIG_B | KE', :b)"
            ),
            {"ca": campaign_a, "a": offer_a, "cb": campaign_b, "b": offer_b},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES "
                "(:sa, :ca, 'ADS_A'), (:sb, :cb, 'ADS_B')"
            ),
            {"sa": adset_a, "ca": campaign_a, "sb": adset_b, "cb": campaign_b},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES "
                "(:aa, :sa, :fa, 'AD_A'), (:ab, :sb, :fb, 'AD_B')"
            ),
            {
                "aa": ad_a,
                "sa": adset_a,
                "fa": f"2399{uuid.uuid4().hex[:10]}",
                "ab": ad_b,
                "sb": adset_b,
                "fb": f"2399{uuid.uuid4().hex[:10]}",
            },
        )

    return {
        "offer_a": offer_a,
        "offer_b": offer_b,
        "ad_a": ad_a,
        "ad_b": ad_b,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Окно содержит warning+stop алерты — счётчики совпадают
@pytest.mark.asyncio
async def test_build_digest_counts_alerts_by_stage(pg_engine, two_ads_world) -> None:
    now = _now()
    ad_a = two_ads_world["ad_a"]

    async with pg_engine.begin() as conn:
        # 2 warning, 1 stop в пределах последних 24ч
        await conn.execute(
            text(
                """
                INSERT INTO alert_events (ad_id, stage, state, matched_rule_codes,
                                          metrics_json, created_at)
                VALUES
                    (:a, 'warning', 'warning_sent', CAST('[]' AS JSONB), CAST('{}' AS JSONB), :t1),
                    (:a, 'warning', 'warning_sent', CAST('[]' AS JSONB), CAST('{}' AS JSONB), :t2),
                    (:a, 'stop',    'stop_sent',    CAST('[]' AS JSONB), CAST('{}' AS JSONB), :t3),
                    (:a, 'warning', 'warning_sent', CAST('[]' AS JSONB), CAST('{}' AS JSONB), :t_old)
                """
            ),
            {
                "a": ad_a,
                "t1": now - timedelta(hours=2),
                "t2": now - timedelta(hours=10),
                "t3": now - timedelta(hours=5),
                "t_old": now - timedelta(hours=30),  # вне окна 24ч
            },
        )

    payload = await build_digest(pg_engine, day_start_utc=now)
    assert payload.alerts_warning_count == 2
    assert payload.alerts_stop_count == 1


# Завершённые disable_tasks за окно — успешные/проваленные
@pytest.mark.asyncio
async def test_build_digest_counts_disable_tasks(pg_engine, two_ads_world) -> None:
    now = _now()

    async with pg_engine.begin() as conn:
        for status, completed_at, key_suffix in (
            ("succeeded", now - timedelta(hours=1), "ok1"),
            ("succeeded", now - timedelta(hours=8), "ok2"),
            ("failed", now - timedelta(hours=3), "fail1"),
            # Out of window — не считается
            ("succeeded", now - timedelta(hours=30), "old"),
            # status running — без completed_at, не считается
            ("running", None, "running"),
        ):
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload,
                         requested_by, completed_at, created_at, updated_at)
                    VALUES
                        ('disable', :s, :k, CAST('{}' AS JSONB), 'test', :c,
                         COALESCE(:c, NOW()), COALESCE(:c, NOW()))
                    """
                ),
                {
                    "s": status,
                    "k": f"digest_test_{key_suffix}_{uuid.uuid4().hex[:8]}",
                    "c": completed_at,
                },
            )

    payload = await build_digest(pg_engine, day_start_utc=now)
    assert payload.disable_tasks_succeeded == 2
    assert payload.disable_tasks_failed == 1


# Топ-5 по spend + total_spend_window_usd считается per-ad-per-day (CRIT-1 fix).
# Топ-строки — latest-per-ad (для ранжирования); total — sum дневных итогов.
@pytest.mark.asyncio
async def test_build_digest_top_ads_and_total_spend(pg_engine, two_ads_world) -> None:
    now = _now()
    ad_a = two_ads_world["ad_a"]
    ad_b = two_ads_world["ad_b"]

    async with pg_engine.begin() as conn:
        # ad_a: два снапшота в РАЗНЫХ UTC-днях (now - 10h и now - 1h при now ~07:xx → вчера и сегодня).
        # per-day CTE берёт latest per (ad, day): вчера=60, сегодня=100 → дневные итоги 60+100=160.
        # ad_b: один снапшот сегодня = 50 → дневной итог 50.
        # total = 160 + 50 = 210 (per-day, через cabinet-сброс).
        # out-of-window snapshot (now - 30h) не учитывается.
        for ad_id, ts, spend in (
            (ad_a, now - timedelta(hours=10), Decimal("60.00")),
            (ad_a, now - timedelta(hours=1), Decimal("100.00")),
            (ad_b, now - timedelta(hours=2), Decimal("50.00")),
            # out-of-window snapshot — не учитывается
            (ad_a, now - timedelta(hours=30), Decimal("999.00")),
        ):
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (ad_id, cycle_ts, spend, clicks, leads, cpc, cost_per_lead)
                    VALUES (:a, :t, :s, 10, 1, 0.5, 5.0)
                    """
                ),
                {"a": ad_id, "t": ts, "s": spend},
            )

    payload = await build_digest(pg_engine, day_start_utc=now)
    # total per-day: 60 (ad_a вчера) + 100 (ad_a сегодня) + 50 (ad_b сегодня) = 210
    assert payload.total_spend_window_usd == Decimal("210.00")
    assert len(payload.top_ads_by_spend) == 2
    # Топ-строки: latest-per-ad — ad_a последний=100, ad_b=50
    assert payload.top_ads_by_spend[0].ad_id == ad_a
    assert payload.top_ads_by_spend[0].spend_usd == Decimal("100.00")
    assert payload.top_ads_by_spend[0].offer_code == "DIG_A"
    assert payload.top_ads_by_spend[1].ad_id == ad_b
    assert payload.top_ads_by_spend[1].spend_usd == Decimal("50.00")


# Активные офферы (is_active=true) считаются корректно
@pytest.mark.asyncio
async def test_build_digest_counts_active_offers(pg_engine, two_ads_world) -> None:
    now = _now()

    async with pg_engine.begin() as conn:
        # Добавим ещё одного inactive — он не должен попасть в счётчик
        await conn.execute(
            text("INSERT INTO offers (code, name, is_active) VALUES ('DIG_C', 'Offer C', FALSE)")
        )

    payload = await build_digest(pg_engine, day_start_utc=now)
    # 2 активных из two_ads_world + 0 новых активных
    assert payload.active_offers_count == 2


# Активные ads (state=normal) считаются с учётом ad_alert_state
@pytest.mark.asyncio
async def test_build_digest_counts_active_ads_normal(pg_engine, two_ads_world) -> None:
    now = _now()
    ad_a = two_ads_world["ad_a"]

    async with pg_engine.begin() as conn:
        # ad_a → warning_sent (не считается)
        # ad_b → нет записи в ad_alert_state (COALESCE → normal, считается)
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state (ad_id, alert_state)
                VALUES (:a, 'warning_sent')
                """
            ),
            {"a": ad_a},
        )

    payload = await build_digest(pg_engine, day_start_utc=now)
    assert payload.active_ads_count == 1


# Пустая БД (нет данных) → все нули и пустой top
@pytest.mark.asyncio
async def test_build_digest_empty(pg_engine, clean_digest_tables) -> None:
    now = _now()
    payload = await build_digest(pg_engine, day_start_utc=now)
    assert payload.alerts_warning_count == 0
    assert payload.alerts_stop_count == 0
    assert payload.disable_tasks_succeeded == 0
    assert payload.disable_tasks_failed == 0
    assert payload.top_ads_by_spend == []
    assert payload.total_spend_window_usd == Decimal("0")
    assert payload.active_offers_count == 0
    assert payload.active_ads_count == 0


# Naive datetime запрещён в build_digest
@pytest.mark.asyncio
async def test_build_digest_rejects_naive_datetime(pg_engine, clean_digest_tables) -> None:
    with pytest.raises(ValueError):
        await build_digest(pg_engine, day_start_utc=datetime(2026, 5, 27, 9, 0, 0))


# Регресс (баг дайджеста 06-02): ad со spend=0 НЕ попадает в Топ-5.
# Раньше фильтр был `WHERE spend IS NOT NULL` — ноль не NULL и протекал мусорными
# строками («— · $0.00 · CPC — · CPL —»). Теперь `WHERE spend > 0`: один ad с реальным
# spend, другой с нулём → в топе только первый.
@pytest.mark.asyncio
async def test_build_digest_top_excludes_zero_spend(pg_engine, two_ads_world) -> None:
    now = _now()
    ad_a = two_ads_world["ad_a"]
    ad_b = two_ads_world["ad_b"]

    async with pg_engine.begin() as conn:
        for ad_id, spend in ((ad_a, Decimal("75.00")), (ad_b, Decimal("0.00"))):
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (ad_id, cycle_ts, spend, clicks, leads, cpc, cost_per_lead)
                    VALUES (:a, :t, :s, 0, 0, NULL, NULL)
                    """
                ),
                {"a": ad_id, "t": now - timedelta(hours=1), "s": spend},
            )

    payload = await build_digest(pg_engine, day_start_utc=now)
    # В топе только ad_a (spend>0); ad_b с нулём отфильтрован.
    assert len(payload.top_ads_by_spend) == 1
    assert payload.top_ads_by_spend[0].ad_id == ad_a
    # total_spend — сумма ВСЕХ snapshot'ов (нули не влияют): 75 + 0 = 75.
    assert payload.total_spend_window_usd == Decimal("75.00")


# Регресс: все ad с нулевым spend → Топ-5 пуст (как пустой день), total=0.
# Renderer на таком payload покажет «(нет данных за окно)» + «За окно не было активности».
@pytest.mark.asyncio
async def test_build_digest_top_empty_when_all_zero_spend(pg_engine, two_ads_world) -> None:
    now = _now()
    ad_a = two_ads_world["ad_a"]
    ad_b = two_ads_world["ad_b"]

    async with pg_engine.begin() as conn:
        for ad_id in (ad_a, ad_b):
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics (ad_id, cycle_ts, spend, clicks, leads, cpc, cost_per_lead)
                    VALUES (:a, :t, 0, 0, 0, NULL, NULL)
                    """
                ),
                {"a": ad_id, "t": now - timedelta(hours=1)},
            )

    payload = await build_digest(pg_engine, day_start_utc=now)
    assert payload.top_ads_by_spend == []
    assert payload.total_spend_window_usd == Decimal("0")
