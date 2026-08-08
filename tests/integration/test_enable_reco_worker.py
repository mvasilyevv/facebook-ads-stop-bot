# -*- coding: utf-8 -*-
"""Интеграционные тесты enable_recommendation_worker.run_once.

Покрывают SQL-кандидатов, analyzer, PostgreSQL idempotency
и durable PostgreSQL notification outbox без прямого Telegram I/O.
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
    run_once,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest_asyncio.fixture
async def clean_reco_tables(
    pg_engine,
    known_test_cabinet_timezones,
    seeded_telegram_config,
):
    """Чистит таблицы которые трогает worker.

    Создаёт тестового recipient chat_id=123456 для durable owner delivery.
    """
    _RECIPIENT_CHAT_ID = 123456

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "telegram_action_tokens",
                "telegram_message_slots",
                "notification_deliveries",
                "notification_events",
            ):
                await conn.execute(text(f"DELETE FROM {t}"))
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
            await conn.execute(text("DELETE FROM telegram_recipient_preferences"))
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


async def _notification_counts(pg_engine) -> tuple[int, int]:
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM notification_events), "
                    "(SELECT COUNT(*) FROM notification_deliveries)"
                )
            )
        ).one()
    return int(row[0]), int(row[1])


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
            text(
                "INSERT INTO offer_rules (offer_id, cpa_threshold, currency) "
                "VALUES (:i, :cpa, 'USD')"
            ),
            {"i": offer_id, "cpa": Decimal("10.00")},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_campaigns (id, campaign_name, offer_id, ad_account_id) VALUES (:i, :n, :o, '123')"
            ),
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
                        (ad_id, cycle_ts, scan_id, currency, spend, cost_per_lead,
                         clicks, cpc, deposits)
                    VALUES (:aid, :ts, NULL, 'USD', :sp, :cpl, 20, 0.025, 0)
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
                        (task_type, status, idempotency_key, payload, requested_by, lane)
                    VALUES
                        ('meta_api_mutation', 'pending', :ikey,
                         jsonb_build_object(
                             'mutation_kind', 'pause_ad',
                             'target_id', CAST(:fb_ad_id AS TEXT),
                             'ad_account_id', '123',
                             'params', jsonb_build_object()
                         ),
                         'test_curator_guard', 'money')
                    """
                ),
                {
                    "ikey": f"curator-pause-{uuid.uuid4().hex}",
                    "fb_ad_id": stopped_ad["fb_ad_id"],
                },
            )


# Recovered ad → recommendation + one durable owner delivery, no direct TG call.
@pytest.mark.asyncio
async def test_creates_recommendation_for_recovered_ad(pg_engine, stopped_ad, tg_respx):
    counts = await run_once(pg_engine)

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

    async with pg_engine.connect() as conn:
        notification = (
            await conn.execute(
                text(
                    """
                    SELECT e.event_type, e.audience, e.facts, d.state,
                           d.telegram_chat_id
                    FROM notification_events e
                    JOIN notification_deliveries d ON d.event_id = e.id
                    """
                )
            )
        ).one()
    assert notification.event_type == "worker_enable_recommendation"
    assert notification.audience == "owners"
    assert notification.state == "pending"
    assert notification.telegram_chat_id == 123456
    assert "Рекомендация" in notification.facts["title"]
    assert recommendation_id
    assert tg_respx.sent_messages == []


@pytest.mark.asyncio
async def test_recommendation_rolls_back_when_notification_projection_fails(
    pg_engine,
    stopped_ad,
    monkeypatch,
) -> None:
    import apps.enable_recommendation_worker.main as worker

    async def fail_projection(*_args, **_kwargs) -> bool:
        raise RuntimeError("notification projection failed")

    monkeypatch.setattr(worker, "notify_owners_in_transaction", fail_projection)
    with pytest.raises(RuntimeError, match="notification projection failed"):
        await run_once(pg_engine)

    async with pg_engine.connect() as conn:
        recommendation_count, event_count, task_count = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM enable_recommendations),
                        (SELECT COUNT(*) FROM notification_events),
                        (SELECT COUNT(*) FROM task_queue)
                    """
                )
            )
        ).one()

    assert recommendation_count == 0
    assert event_count == 0
    assert task_count == 0


@pytest.mark.asyncio
async def test_auto_promotion_task_and_card_commit_together(
    pg_engine,
    stopped_ad,
    monkeypatch,
) -> None:
    import apps.enable_recommendation_worker.main as worker
    from core.enable_reco.analyzer import RecommendationDecision

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'disabled'
                WHERE ad_id = :ad_id
                """
            ),
            {"ad_id": stopped_ad["ad_id"]},
        )
        await conn.execute(
            text("UPDATE fb_ads SET delivery_status = 'OFF' WHERE id = :ad_id"),
            {"ad_id": stopped_ad["ad_id"]},
        )
        await conn.execute(
            text(
                """
                UPDATE observer_config
                SET auto_enable_recommendations = TRUE
                WHERE singleton_key = 'default'
                """
            )
        )
    monkeypatch.setattr(
        worker,
        "should_recommend",
        lambda **_kwargs: RecommendationDecision(
            recommend=True,
            level="ok",
            skip_reason=None,
            snapshot={},
        ),
    )

    counts = await run_once(pg_engine)

    async with pg_engine.connect() as conn:
        promoted_task_id, task_status, event_count = (
            await conn.execute(
                text(
                    """
                    SELECT er.promoted_to_task_id,
                           task.status,
                           (
                               SELECT COUNT(*)
                               FROM notification_events event
                               WHERE event.event_type = 'worker_enable_recommendation'
                           )
                    FROM enable_recommendations er
                    JOIN task_queue task ON task.id = er.promoted_to_task_id
                    """
                )
            )
        ).one()

    assert counts["recommendations"] == 1
    assert counts["auto_promoted"] == 1
    assert promoted_task_id is not None
    assert task_status == "pending"
    assert event_count == 1


@pytest.mark.asyncio
async def test_auto_promotion_rolls_back_task_when_card_projection_fails(
    pg_engine,
    stopped_ad,
    monkeypatch,
) -> None:
    import apps.enable_recommendation_worker.main as worker
    from core.enable_reco.analyzer import RecommendationDecision

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'disabled'
                WHERE ad_id = :ad_id
                """
            ),
            {"ad_id": stopped_ad["ad_id"]},
        )
        await conn.execute(
            text("UPDATE fb_ads SET delivery_status = 'OFF' WHERE id = :ad_id"),
            {"ad_id": stopped_ad["ad_id"]},
        )
        await conn.execute(
            text(
                """
                UPDATE observer_config
                SET auto_enable_recommendations = TRUE
                WHERE singleton_key = 'default'
                """
            )
        )

    monkeypatch.setattr(
        worker,
        "should_recommend",
        lambda **_kwargs: RecommendationDecision(
            recommend=True,
            level="ok",
            skip_reason=None,
            snapshot={},
        ),
    )

    async def fail_projection(*_args, **_kwargs) -> bool:
        raise RuntimeError("notification projection failed")

    monkeypatch.setattr(worker, "notify_owners_in_transaction", fail_projection)

    with pytest.raises(RuntimeError, match="notification projection failed"):
        await run_once(pg_engine)

    async with pg_engine.connect() as conn:
        recommendation_count, task_count, event_count = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM enable_recommendations),
                        (
                            SELECT COUNT(*) FROM task_queue
                            WHERE requested_by = 'auto_enable_recommendation_worker'
                        ),
                        (SELECT COUNT(*) FROM notification_events)
                    """
                )
            )
        ).one()

    assert recommendation_count == 0
    assert task_count == 0
    assert event_count == 0


# Curator hold разрешён только для disabled + OFF без незавершённой pause_ad.
@pytest.mark.asyncio
async def test_curator_candidate_requires_disabled_off_without_pause(
    pg_engine, stopped_ad, tg_respx
) -> None:
    await _make_curator_only_candidate(
        pg_engine,
        stopped_ad,
        alert_state="disabled",
        delivery_status="OFF",
    )
    counts = await run_once(pg_engine)

    assert counts["recommendations"] == 1
    async with pg_engine.connect() as conn:
        snapshot = (
            await conn.execute(text("SELECT snapshot_metrics FROM enable_recommendations LIMIT 1"))
        ).scalar()
    assert snapshot["hold_until_cpl"] is True
    assert await _notification_counts(pg_engine) == (1, 1)
    assert tg_respx.sent_messages == []


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
    tg_respx,
    alert_state,
    delivery_status,
    with_unfinished_pause,
) -> None:
    await _make_curator_only_candidate(
        pg_engine,
        stopped_ad,
        alert_state=alert_state,
        delivery_status=delivery_status,
        with_unfinished_pause=with_unfinished_pause,
    )
    counts = await run_once(pg_engine)

    assert counts["candidates"] == 1
    assert counts["recommendations"] == 0
    assert counts["skipped_decision"] == 1
    assert await _notification_counts(pg_engine) == (0, 0)
    assert tg_respx.sent_messages == []


# Per-ad opt-out не скрывает ручную рекомендацию, но запрещает auto-promotion.
@pytest.mark.asyncio
async def test_auto_enable_opt_out_keeps_manual_recommendation(pg_engine, stopped_ad, tg_respx):
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

    try:
        counts = await run_once(pg_engine)
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
        assert await _notification_counts(pg_engine) == (1, 1)
        assert tg_respx.sent_messages == []
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE observer_config SET auto_enable_recommendations = false "
                    "WHERE singleton_key = 'default'"
                )
            )


# Повторный run не дублирует благодаря PostgreSQL idempotency key.
@pytest.mark.asyncio
async def test_postgres_idempotency_prevents_duplicate_recommendation(
    pg_engine, stopped_ad, tg_respx
):
    counts_1 = await run_once(pg_engine)
    assert counts_1["recommendations"] == 1
    assert counts_1["alerts_sent"] == 1

    counts_2 = await run_once(pg_engine)
    # Второй прогон: insert conflict, ни новой рекомендации, ни event.
    assert counts_2["recommendations"] == 0
    assert counts_2["alerts_sent"] == 0
    assert counts_2["skipped_existing"] == 1

    # Всего одна запись в БД
    async with pg_engine.connect() as conn:
        n = (await conn.execute(text("SELECT COUNT(*) FROM enable_recommendations"))).scalar()
    assert n == 1

    assert await _notification_counts(pg_engine) == (1, 1)
    assert tg_respx.sent_messages == []


# Сценарий: ад отключён давно, но метрик после disable нет → analyzer пропустит
@pytest.mark.asyncio
async def test_skips_when_no_metrics_after_disable(pg_engine, stopped_ad, tg_respx):
    # Удаляем все метрики у ad
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM ad_metrics WHERE ad_id = :aid"),
            {"aid": stopped_ad["ad_id"]},
        )

    counts = await run_once(pg_engine)
    assert counts["candidates"] == 1
    assert counts["recommendations"] == 0
    assert counts["skipped_decision"] == 1
    assert await _notification_counts(pg_engine) == (0, 0)
    assert tg_respx.sent_messages == []


# Сценарий: ад snoozed_until в будущем → analyzer пропустит
@pytest.mark.asyncio
async def test_skips_snoozed_ad(pg_engine, stopped_ad, tg_respx):
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE ad_alert_state SET snoozed_until = :until WHERE ad_id = :aid"),
            {"until": _utcnow() + timedelta(hours=2), "aid": stopped_ad["ad_id"]},
        )

    counts = await run_once(pg_engine)
    assert counts["candidates"] == 1
    assert counts["skipped_decision"] == 1
    assert counts["recommendations"] == 0
    assert await _notification_counts(pg_engine) == (0, 0)
    assert tg_respx.sent_messages == []
