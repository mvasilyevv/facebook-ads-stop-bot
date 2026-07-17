# -*- coding: utf-8 -*-
"""Интеграционные тесты enable_recommendation_worker.run_once.

Покрывают полный путь: SQL-запрос кандидатов → метрики → analyzer → INSERT
в enable_recommendations + Redis dedup + TG-алерт через respx-mock.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.enable_recommendation_worker.main import (
    fetch_candidates,
    is_recently_recommended,
    mark_recommended,
    run_once,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def clean_reco_tables(pg_engine):
    """Чистит таблицы которые трогает worker.

    Создаёт тестового recipient chat_id=123456 чтобы send_alert → load_active_recipients
    находил кого отправить (send_alert идёт по recipients, не по прямому chat_id).
    """
    _RECIPIENT_CHAT_ID = 123456

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "enable_recommendations",
                "ad_auto_enable_disabled",
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
            # Тест ассертит ТОЧНЫЙ счётчик отправок (send_alert идёт по ВСЕМ
            # активным recipients) → фикстура должна владеть таблицей целиком,
            # иначе чужие recipients от других тестов (shared _test БД) ломают
            # счётчик. Чистим всех, затем сеем своего единственного.
            await conn.execute(text("DELETE FROM telegram_recipients"))
            await conn.execute(text("DELETE FROM observer_config WHERE singleton_key = 'default'"))

    await _truncate()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO observer_config (singleton_key, is_scanning_enabled) "
                "VALUES ('default', TRUE)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO telegram_recipients (chat_id, telegram_user_id, role) "
                "VALUES (:c, 99001, 'owner') "
                "ON CONFLICT (chat_id, telegram_user_id) DO NOTHING"
            ),
            {"c": _RECIPIENT_CHAT_ID},
        )
    yield
    await _truncate()


@pytest_asyncio.fixture
async def stopped_ad(pg_engine, clean_reco_tables):
    """Создаёт фикстуру: оффер CPA=10, ad в state='stop_sent' давно отключённое, метрики «выправились».

    Возвращает dict с ad_id, fb_ad_id, last_transition_at.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    fb_ad_id = f"23001{suffix[:8]}"

    last_transition = _utcnow() - timedelta(hours=2)  # отключено 2 часа назад
    incident_token = uuid.uuid4()
    cycle_recent = _utcnow() - timedelta(minutes=10)
    cycle_older = _utcnow() - timedelta(minutes=30)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, 'TestOffer', TRUE)"
            ),
            {"i": offer_id, "c": f"ER_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:i, :cpa)"),
            {"i": offer_id, "cpa": Decimal("10.00")},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"AD_{suffix}"},
        )
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state
                    (ad_id, alert_state, current_stage, open_state_token, last_transition_at)
                VALUES (:aid, 'stop_sent', 'stop', :tok, :ts)
                """
            ),
            {"aid": ad_id, "tok": incident_token, "ts": last_transition},
        )
        # «Выправленные» метрики после отключения: CPC и no-lead guardrail снова
        # ниже канонических порогов evaluator'а.
        for ts, spend, cpl in (
            (cycle_older, Decimal("1.0"), Decimal("4.0")),
            (cycle_recent, Decimal("0.5"), Decimal("5.0")),
        ):
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics
                        (ad_id, cycle_ts, scan_id, spend, cost_per_lead,
                         clicks, cpc, deposits)
                    VALUES (:aid, :ts, NULL, :sp, :cpl, 20, 0.025, 0)
                    """
                ),
                {"aid": ad_id, "ts": ts, "sp": spend, "cpl": cpl},
            )

    return {
        "ad_id": ad_id,
        "fb_ad_id": fb_ad_id,
        "campaign_id": campaign_id,
        "adset_id": adset_id,
        "offer_id": offer_id,
        "last_transition_at": last_transition,
        "incident_token": incident_token,
    }


async def _make_curator_only_candidate(
    engine,
    stopped_ad: dict,
    *,
    alert_state: str,
    delivery_status: str,
    with_unfinished_pause: bool = False,
) -> None:
    """Оставить только curator-сигнал: 108 impressions, CTR 3.7%, recovery нет."""
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET alert_state = :state, current_stage = 'stop' "
                "WHERE ad_id = :aid"
            ),
            {"state": alert_state, "aid": stopped_ad["ad_id"]},
        )
        await conn.execute(
            text("UPDATE fb_ads SET delivery_status = :status WHERE id = :aid"),
            {"status": delivery_status, "aid": stopped_ad["ad_id"]},
        )
        await conn.execute(
            text(
                """
                UPDATE ad_metrics
                SET spend = 25, cost_per_lead = 40, cost_per_registration = 40,
                    registrations = 0, deposits = 0, impressions = 108, ctr = 3.7
                WHERE ad_id = :aid
                """
            ),
            {"aid": stopped_ad["ad_id"]},
        )
        if with_unfinished_pause:
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by)
                    VALUES
                        ('meta_api_mutation', 'pending', :ikey,
                         jsonb_build_object(
                             'mutation_kind', 'pause_ad',
                             'target_id', CAST(:fb_ad_id AS TEXT),
                             'params', jsonb_build_object()
                         ),
                         'test_curator_guard')
                    """
                ),
                {
                    "ikey": f"curator-pause-{uuid.uuid4().hex}",
                    "fb_ad_id": stopped_ad["fb_ad_id"],
                },
            )


# Сценарий: stop_sent ад с «выправленными» метриками → создаётся enable_recommendation + TG-алерт
@pytest.mark.asyncio
async def test_creates_recommendation_for_recovered_ad(
    pg_engine, stopped_ad, fake_redis_client, tg_respx
):
    from core.telegram.client import TelegramBotClient

    tg_client = TelegramBotClient("fake-token")

    counts = await run_once(
        pg_engine,
        redis_client=fake_redis_client,
        tg_client=tg_client,
    )

    assert counts["candidates"] == 1
    assert counts["recommendations"] == 1
    assert counts["alerts_sent"] == 1
    assert counts["skipped_decision"] == 0

    # Запись появилась в БД
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT ad_id, recommendation_level, idempotency_key, id "
                    "FROM enable_recommendations LIMIT 1"
                )
            )
        ).first()
        assert row is not None
        assert row[0] == stopped_ad["ad_id"]
        assert row[1] in ("ok", "warning")
        assert row[2].startswith("enable_reco:")
        recommendation_id = str(row[3])

    # TG-алерт отправлен с inline-кнопкой
    assert len(tg_respx.sent_messages) == 1
    payload = tg_respx.sent_messages[0]
    assert payload["chat_id"] == "123456"
    keyboard = payload.get("reply_markup", {}).get("inline_keyboard")
    assert keyboard is not None
    btn = keyboard[0][0]
    assert btn["callback_data"] == f"ereco:{recommendation_id}"

    # Redis-дедуп ключ стоит
    assert await fake_redis_client.get(f"enable_reco:last:{stopped_ad['ad_id']}") == "1"

    await tg_client.close()


# Curator hold разрешён только для disabled + OFF без незавершённой pause_ad.
@pytest.mark.asyncio
async def test_curator_candidate_requires_disabled_off_without_pause(
    pg_engine, stopped_ad, fake_redis_client, tg_respx
) -> None:
    from core.telegram.client import TelegramBotClient

    await _make_curator_only_candidate(
        pg_engine,
        stopped_ad,
        alert_state="disabled",
        delivery_status="OFF",
    )
    tg_client = TelegramBotClient("fake-token")
    counts = await run_once(
        pg_engine,
        redis_client=fake_redis_client,
        tg_client=tg_client,
    )

    assert counts["recommendations"] == 1
    async with pg_engine.connect() as conn:
        snapshot = (
            await conn.execute(text("SELECT snapshot_metrics FROM enable_recommendations LIMIT 1"))
        ).scalar()
    assert snapshot["hold_until_cpl"] is True
    assert len(tg_respx.sent_messages) == 1
    await tg_client.close()


@pytest.mark.parametrize(
    ("alert_state", "delivery_status", "with_unfinished_pause"),
    [
        ("stop_sent", "OFF", False),
        ("disabled", "ACTIVE", False),
        ("disabled", "OFF", True),
    ],
)
@pytest.mark.asyncio
async def test_curator_candidate_rejected_when_safety_precondition_missing(
    pg_engine,
    stopped_ad,
    fake_redis_client,
    tg_respx,
    alert_state,
    delivery_status,
    with_unfinished_pause,
) -> None:
    from core.telegram.client import TelegramBotClient

    await _make_curator_only_candidate(
        pg_engine,
        stopped_ad,
        alert_state=alert_state,
        delivery_status=delivery_status,
        with_unfinished_pause=with_unfinished_pause,
    )
    tg_client = TelegramBotClient("fake-token")
    counts = await run_once(
        pg_engine,
        redis_client=fake_redis_client,
        tg_client=tg_client,
    )

    assert counts["candidates"] == 1
    assert counts["recommendations"] == 0
    assert counts["skipped_decision"] == 1
    assert len(tg_respx.sent_messages) == 0
    await tg_client.close()


# Per-ad opt-out не скрывает ручную рекомендацию, но запрещает auto-promotion.
@pytest.mark.asyncio
async def test_auto_enable_opt_out_keeps_manual_recommendation(
    pg_engine, stopped_ad, fake_redis_client, tg_respx
):
    from core.telegram.client import TelegramBotClient

    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE ad_alert_state SET alert_state = 'disabled' WHERE ad_id = :aid"),
            {"aid": stopped_ad["ad_id"]},
        )
        await conn.execute(
            text("UPDATE fb_ads SET delivery_status = 'OFF' WHERE id = :aid"),
            {"aid": stopped_ad["ad_id"]},
        )
        await conn.execute(
            text(
                "INSERT INTO ad_auto_enable_disabled (ad_id, cabinet_day_started_at, reason) "
                "VALUES (:aid, :ts, 'user_opt_out')"
            ),
            {"aid": stopped_ad["ad_id"], "ts": _utcnow()},
        )
        await conn.execute(
            text(
                "UPDATE observer_config SET auto_enable_recommendations = true "
                "WHERE singleton_key = 'default'"
            )
        )

    cands = await fetch_candidates(pg_engine, limit=10)
    assert len(cands) == 1

    tg_client = TelegramBotClient("fake-token")
    try:
        counts = await run_once(
            pg_engine,
            redis_client=fake_redis_client,
            tg_client=tg_client,
        )
        assert counts["recommendations"] == 1
        assert counts["auto_promotion_failed"] == 1
        async with pg_engine.connect() as conn:
            promoted, task_count = (
                await conn.execute(
                    text(
                        "SELECT er.promoted_to_task_id, "
                        "(SELECT COUNT(*) FROM task_queue tq "
                        " WHERE tq.requested_by = 'auto_enable_recommendation_worker') "
                        "FROM enable_recommendations er LIMIT 1"
                    )
                )
            ).one()
        assert promoted is None
        assert task_count == 0
        assert tg_respx.sent_messages[0]["reply_markup"]["inline_keyboard"]
    finally:
        await tg_client.close()
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE observer_config SET auto_enable_recommendations = false "
                    "WHERE singleton_key = 'default'"
                )
            )


# Сценарий: повторный run в течение 6 часов — не дублирует благодаря Redis dedup
@pytest.mark.asyncio
async def test_dedups_within_window(pg_engine, stopped_ad, fake_redis_client, tg_respx):
    from core.telegram.client import TelegramBotClient

    tg_client = TelegramBotClient("fake-token")

    counts_1 = await run_once(
        pg_engine,
        redis_client=fake_redis_client,
        tg_client=tg_client,
    )
    assert counts_1["recommendations"] == 1
    assert counts_1["alerts_sent"] == 1

    counts_2 = await run_once(
        pg_engine,
        redis_client=fake_redis_client,
        tg_client=tg_client,
    )
    # Второй прогон — дедуп срабатывает: ни одной новой рекомендации, ни одного алёрта
    assert counts_2["recommendations"] == 0
    assert counts_2["alerts_sent"] == 0
    assert counts_2["skipped_dedup"] == 1

    # Всего одна запись в БД
    async with pg_engine.connect() as conn:
        n = (await conn.execute(text("SELECT COUNT(*) FROM enable_recommendations"))).scalar()
    assert n == 1

    # Один TG-вызов
    assert len(tg_respx.sent_messages) == 1

    await tg_client.close()


# Сценарий: ад отключён давно, но метрик после disable нет → analyzer пропустит
@pytest.mark.asyncio
async def test_skips_when_no_metrics_after_disable(
    pg_engine, stopped_ad, fake_redis_client, tg_respx
):
    # Удаляем все метрики у ad
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM ad_metrics WHERE ad_id = :aid"),
            {"aid": stopped_ad["ad_id"]},
        )

    from core.telegram.client import TelegramBotClient

    tg_client = TelegramBotClient("fake-token")
    counts = await run_once(
        pg_engine,
        redis_client=fake_redis_client,
        tg_client=tg_client,
    )
    assert counts["candidates"] == 1
    assert counts["recommendations"] == 0
    assert counts["skipped_decision"] == 1
    assert len(tg_respx.sent_messages) == 0

    await tg_client.close()


# Сценарий: ад snoozed_until в будущем → analyzer пропустит
@pytest.mark.asyncio
async def test_skips_snoozed_ad(pg_engine, stopped_ad, fake_redis_client, tg_respx):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE ad_alert_state SET snoozed_until = :until WHERE ad_id = :aid"),
            {"until": _utcnow() + timedelta(hours=2), "aid": stopped_ad["ad_id"]},
        )

    from core.telegram.client import TelegramBotClient

    tg_client = TelegramBotClient("fake-token")
    counts = await run_once(
        pg_engine,
        redis_client=fake_redis_client,
        tg_client=tg_client,
    )
    assert counts["candidates"] == 1
    assert counts["skipped_decision"] == 1
    assert counts["recommendations"] == 0
    assert len(tg_respx.sent_messages) == 0

    await tg_client.close()


# Сценарий: проверяем хелперы Redis dedup напрямую
@pytest.mark.asyncio
async def test_redis_dedup_helpers(fake_redis_client):
    ad_id = uuid.uuid4()
    assert await is_recently_recommended(fake_redis_client, ad_id) is False
    assert await mark_recommended(fake_redis_client, ad_id) is True
    assert await is_recently_recommended(fake_redis_client, ad_id) is True
    # Второй mark — NX не сработает
    assert await mark_recommended(fake_redis_client, ad_id) is False
