# -*- coding: utf-8 -*-
"""E2E: disabled ad → recommendation outbox → canonical promotion → enable task.

Сшивка четырёх компонентов:
1. Фикстура создаёт подтверждённо выключленный ad + метрики «выправились»
   (повторяет паттерн tests/integration/test_enable_reco_worker.py).
2. `apps/enable_recommendation_worker.run_once` находит кандидата и ставит
   owner notification в PostgreSQL outbox.
3. Канонический promotion service revalidates рекомендацию и создаёт
   task_queue запись task_type='meta_api_mutation' (activate_ad).
4. `apps/meta_api_worker.process_one_task` подхватывает её через readiness-gated claim
   и доводит до status='succeeded', FSM → 'normal'.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text

import apps.meta_api_worker.main as worker_main
from apps.enable_recommendation_worker.main import run_once
from apps.meta_api_worker.main import process_one_task
from core.enable_reco.confirmation import (
    RecommendationAlreadyPromotedError,
    RecommendationNotFoundError,
    promote_enable_recommendation,
)
from core.meta_api.queue import claim_browser_ready_mutation_task
from core.observer.enable_grace import EnableGrace, grace_is_active

pytestmark = pytest.mark.usefixtures(
    "known_test_cabinet_timezones",
    "fresh_browser_readiness",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_E2E_RECIPIENT_CHAT_ID = 9000


@pytest_asyncio.fixture
async def clean_enable_reco_pipeline(pg_engine, seeded_telegram_config):
    """Чистит все таблицы pipeline'а до/после теста.

    Создаёт тестового recipient chat_id=9000 для durable owner delivery.
    """

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
            await conn.execute(
                text(
                    "DELETE FROM telegram_recipient_preferences "
                    "WHERE recipient_id IN "
                    "(SELECT id FROM telegram_recipients WHERE chat_id = :c)"
                ),
                {"c": _E2E_RECIPIENT_CHAT_ID},
            )
            await conn.execute(
                text("DELETE FROM telegram_recipients WHERE chat_id = :c"),
                {"c": _E2E_RECIPIENT_CHAT_ID},
            )
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
                "VALUES (:c, 99002, 'owner') "
                "ON CONFLICT (chat_id, telegram_user_id) DO NOTHING"
            ),
            {"c": _E2E_RECIPIENT_CHAT_ID},
        )
    yield
    await _truncate()


@pytest_asyncio.fixture
async def stopped_ad_e2e(pg_engine, clean_enable_reco_pipeline) -> dict[str, Any]:
    """Создаёт оффер + подтверждённо выключленный ad + recovered метрики.

    Возвращает dict с ad_id, fb_ad_id для дальнейшего assert'а в тесте.
    """
    offer_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    adset_id = uuid.uuid4()
    ad_id = uuid.uuid4()
    suffix = uuid.uuid4().hex[:8]
    fb_ad_id = f"230099{suffix[:6]}"

    last_transition = _utcnow() - timedelta(hours=2)
    incident_token = uuid.uuid4()
    cycle_recent = _utcnow() - timedelta(minutes=10)
    cycle_older = _utcnow() - timedelta(minutes=30)

    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, 'E2E offer', TRUE)"
            ),
            {"i": offer_id, "c": f"E2EREC_{suffix}"},
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
            {"i": campaign_id, "n": f"CMP_E2E_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_E2E_{suffix}"},
        )
        await conn.execute(
            text(
                "INSERT INTO fb_ads "
                "(id, adset_id, fb_ad_id, ad_name, delivery_status) "
                "VALUES (:i, :a, :f, :n, 'OFF')"
            ),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"AD_E2E_{suffix}"},
        )
        await conn.execute(
            text(
                """
                INSERT INTO ad_alert_state
                    (ad_id, alert_state, current_stage, open_state_token, last_transition_at)
                VALUES (:aid, 'disabled', 'stop', :tok, :ts)
                """
            ),
            {"aid": ad_id, "tok": incident_token, "ts": last_transition},
        )
        # 2 «выправленные» метрики после disable: CPC и no-lead guardrail
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
        "incident_token": incident_token,
    }


# E2E: recommendation + durable card → canonical operator promotion → confirmed activation.
@pytest.mark.asyncio
async def test_full_cycle_reco_to_enable_task(
    pg_engine,
    stopped_ad_e2e,
    monkeypatch,
) -> None:
    # Шаг 1: worker находит ad и commit-ит recommendation + owner outbox card.
    counts = await run_once(pg_engine)
    assert counts["candidates"] == 1
    assert counts["recommendations"] == 1
    assert counts["alerts_sent"] == 1

    async with pg_engine.connect() as conn:
        recommendation_id = str(await conn.scalar(text("SELECT id FROM enable_recommendations")))
        card = (
            await conn.execute(
                text(
                    """
                    SELECT e.event_type, e.audience, d.state, d.telegram_chat_id
                    FROM notification_events e
                    JOIN notification_deliveries d ON d.event_id = e.id
                    """
                )
            )
        ).one()
    uuid.UUID(recommendation_id)
    assert card == (
        "worker_enable_recommendation",
        "owners",
        "pending",
        _E2E_RECIPIENT_CHAT_ID,
    )

    # Шаг 2: тот же canonical service, что используют operator UI/TMA, revalidates intent.
    promotion = await promote_enable_recommendation(
        pg_engine,
        recommendation_id=recommendation_id,
        requested_by="operator:reviewer",
    )

    # Шаг 3: в task_queue появилась activate_ad mutation задача.
    async with pg_engine.connect() as conn:
        task_row = (
            await conn.execute(
                text(
                    """
                    SELECT id, task_type, status, payload, requested_by
                    FROM task_queue
                    WHERE task_type = 'meta_api_mutation'
                      AND payload->>'mutation_kind' = 'activate_ad'
                    """
                )
            )
        ).first()
    assert task_row is not None
    enable_task_id = int(task_row[0])
    assert enable_task_id == promotion.task_id
    assert task_row[1] == "meta_api_mutation"
    assert task_row[2] == "pending"
    assert task_row[3]["target_id"] == stopped_ad_e2e["fb_ad_id"]
    assert task_row[4] == "operator:reviewer"

    # Шаг 4: meta_api_worker исполняет activate_ad через fake dispatch.
    async def _fake_dispatch(client, p):
        return {"success": True, "graph_response": {"ok": True}, "modified_ids": [p.target_id]}

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)

    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
    assert not claim.queue_empty
    assert claim.task is not None
    assert claim.task.id == enable_task_id

    fake_client = AsyncMock()
    await process_one_task(pg_engine, claim.task, client=fake_client)

    async with pg_engine.connect() as conn:
        final = (
            await conn.execute(
                text("SELECT status, result FROM task_queue WHERE id = :i"),
                {"i": enable_task_id},
            )
        ).first()
    assert final[0] == "succeeded"
    assert final[1]["success"] is True

    # Шаг 5: FSM-sync после activate_ad → ad_alert_state переходит в 'normal'.
    async with pg_engine.connect() as conn:
        fsm_state = (
            await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))
        ).scalar()
    assert fsm_state == "normal"


# Curator hold: marker появляется только после успешной activation и даёт
# ДОПОЛНИТЕЛЬНЫЙ allowance поверх свежего baseline spend.
@pytest.mark.asyncio
async def test_curator_grace_is_durable_and_keeps_absolute_cap(
    pg_engine,
    stopped_ad_e2e,
    monkeypatch,
) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE fb_ads SET delivery_status = 'OFF' WHERE id = :aid"),
            {"aid": stopped_ad_e2e["ad_id"]},
        )
        await conn.execute(
            text("UPDATE ad_alert_state SET alert_state = 'disabled' WHERE ad_id = :aid"),
            {"aid": stopped_ad_e2e["ad_id"]},
        )
        rec_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO enable_recommendations
                        (ad_id, snapshot_metrics, recommendation_level,
                         live_batch_started_at, idempotency_key)
                    VALUES
                        (:aid,
                         jsonb_build_object(
                             'hold_until_cpl', true,
                             'grace_spend_cap', '10.00',
                             'incident_open_state_token', CAST(:tok AS text)
                         ),
                         'warning', NOW(), :ik)
                    RETURNING id
                    """
                ),
                {
                    "aid": stopped_ad_e2e["ad_id"],
                    "tok": str(stopped_ad_e2e["incident_token"]),
                    "ik": f"hold-{uuid.uuid4().hex}",
                },
            )
        ).scalar_one()

    promotion = await promote_enable_recommendation(
        pg_engine,
        recommendation_id=str(rec_id),
        requested_by="operator:reviewer",
    )
    assert promotion.task_id > 0

    claim = await claim_browser_ready_mutation_task(pg_engine, lanes=("money",))
    assert claim.task is not None
    assert claim.task.payload["params"]["enable_grace"] == {"spend_cap": "10.00"}

    async def _fake_dispatch(_client, payload):
        return {
            "success": True,
            "graph_response": {"ok": True},
            "modified_ids": [payload.target_id],
        }

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)
    await process_one_task(pg_engine, claim.task, client=AsyncMock())

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT enable_grace_until,
                           enable_grace_spend_cap,
                           enable_grace_baseline_spend,
                           enable_grace_cabinet_day_start,
                           enable_grace_currency,
                           enable_grace_currency_exponent
                    FROM ad_alert_state
                    WHERE ad_id = :ad_id
                    """
                ),
                {"ad_id": stopped_ad_e2e["ad_id"]},
            )
        ).one()
    grace = EnableGrace(
        until=row.enable_grace_until,
        spend_cap=row.enable_grace_spend_cap,
        baseline_spend=row.enable_grace_baseline_spend,
        cabinet_day_start=row.enable_grace_cabinet_day_start,
        currency=row.enable_grace_currency,
        currency_exponent=row.enable_grace_currency_exponent,
    )
    # Последняя метрика фикстуры: baseline=0.50; absolute CPA cap stays 10.00.
    assert grace.baseline_spend == Decimal("0.5")
    assert grace.spend_cap == Decimal("10.00")
    now = _utcnow()
    assert (
        grace_is_active(
            grace,
            now=now,
            spend=Decimal("9.99"),
            cabinet_day_start=grace.cabinet_day_start,
            currency="USD",
            currency_exponent=2,
        )
        is True
    )
    assert (
        grace_is_active(
            grace,
            now=now,
            spend=Decimal("10.00"),
            cabinet_day_start=grace.cabinet_day_start,
            currency="USD",
            currency_exponent=2,
        )
        is False
    )
    # Cabinet-day reset / рассинхрон ниже baseline завершает hold fail-safe.
    assert (
        grace_is_active(
            grace,
            now=now,
            spend=Decimal("0.10"),
            cabinet_day_start=grace.cabinet_day_start,
            currency="USD",
            currency_exponent=2,
        )
        is False
    )


# E2E: повторное promotion intent не создаёт вторую задачу.
@pytest.mark.asyncio
async def test_duplicate_promotion_does_not_create_second_task(
    pg_engine,
    stopped_ad_e2e,
) -> None:
    # M-14: ereco-кнопка требует живую (не промоутнутую) рекомендацию — создаём её.
    async with pg_engine.begin() as conn:
        rec_id = (
            await conn.execute(
                text(
                    """
                INSERT INTO enable_recommendations
                    (ad_id, snapshot_metrics, recommendation_level,
                     live_batch_started_at, idempotency_key)
                VALUES (
                    :aid,
                    jsonb_build_object('incident_open_state_token', CAST(:tok AS text)),
                    'ok', NOW(), :ik
                )
                RETURNING id
                """
                ),
                {
                    "aid": stopped_ad_e2e["ad_id"],
                    "tok": str(stopped_ad_e2e["incident_token"]),
                    "ik": f"ereco-{uuid.uuid4().hex[:10]}",
                },
            )
        ).scalar_one()

    # Первый intent создаёт activate_ad task и расходует рекомендацию.
    await promote_enable_recommendation(
        pg_engine,
        recommendation_id=str(rec_id),
        requested_by="operator:reviewer",
    )
    with pytest.raises(RecommendationAlreadyPromotedError):
        await promote_enable_recommendation(
            pg_engine,
            recommendation_id=str(rec_id),
            requested_by="operator:reviewer",
        )

    # Рекомендация промоутнута на созданную задачу (replay-guard сработал по делу).
    async with pg_engine.connect() as conn:
        promoted = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM enable_recommendations "
                    "WHERE promoted_to_task_id IS NOT NULL"
                )
            )
        ).scalar()
    assert promoted == 1

    async with pg_engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM task_queue "
                    "WHERE task_type = 'meta_api_mutation' "
                    "AND payload->>'mutation_kind' = 'activate_ad'"
                )
            )
        ).scalar()
    assert n == 1


# Missing recommendation is rejected and creates no activation task.
@pytest.mark.asyncio
async def test_missing_recommendation_is_rejected(pg_engine, stopped_ad_e2e) -> None:
    with pytest.raises(RecommendationNotFoundError):
        await promote_enable_recommendation(
            pg_engine,
            recommendation_id=str(uuid.uuid4()),
            requested_by="operator:reviewer",
        )

    async with pg_engine.connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM task_queue "
                    "WHERE task_type = 'meta_api_mutation' "
                    "AND payload->>'mutation_kind' = 'activate_ad' "
                    "AND payload->>'target_id' = :fb"
                ),
                {"fb": stopped_ad_e2e["fb_ad_id"]},
            )
        ).scalar()
    assert n == 0


# A recommendation with promoted_to_task_id is rejected by the canonical service.
@pytest.mark.asyncio
async def test_promoted_recommendation_is_rejected(pg_engine, stopped_ad_e2e) -> None:
    async with pg_engine.begin() as conn:
        # Создаём задачу-заглушку, на которую сошлёмся как promoted.
        task_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by, lane)
                    VALUES (
                        'meta_api_mutation', 'succeeded', :ik,
                        CAST('{"mutation_kind":"activate_ad","target_id":"promo-test-ad","ad_account_id":"123"}' AS JSONB),
                        'test', 'money'
                    )
                    RETURNING id
                    """
                ),
                {"ik": f"promo-{uuid.uuid4().hex[:10]}"},
            )
        ).scalar()
        rec_id = (
            await conn.execute(
                text(
                    """
                INSERT INTO enable_recommendations
                    (ad_id, snapshot_metrics, recommendation_level,
                     live_batch_started_at, idempotency_key, promoted_to_task_id)
                VALUES (
                    :aid,
                    jsonb_build_object('incident_open_state_token', CAST(:tok AS text)),
                    'ok', NOW(), :ik, :tid
                )
                RETURNING id
                """
                ),
                {
                    "aid": stopped_ad_e2e["ad_id"],
                    "tok": str(stopped_ad_e2e["incident_token"]),
                    "ik": f"ereco-{uuid.uuid4().hex[:10]}",
                    "tid": task_id,
                },
            )
        ).scalar_one()

    with pytest.raises(RecommendationAlreadyPromotedError):
        await promote_enable_recommendation(
            pg_engine,
            recommendation_id=str(rec_id),
            requested_by="operator:reviewer",
        )
