# -*- coding: utf-8 -*-
"""E2E cross-cutting сценарий: ad disabled → enable_reco → callback ereco → enable task.

Сшивка четырёх компонентов:
1. Фикстура создаёт ad в alert_state='stop_sent' + метрики «выправились»
   (повторяет паттерн tests/integration/test_enable_reco_worker.py).
2. `apps/enable_recommendation_worker.run_once` находит кандидата, шлёт TG-алерт
   с inline-кнопкой `ereco:<recommendation_uuid>`.
3. `core/telegram/handlers/alerts.handle_enable_reco_callback` принимает клик
   пользователя и создаёт task_queue запись task_type='meta_api_mutation' (activate_ad).
4. `apps/meta_api_worker.process_one_task` подхватывает её через claim_pending_task
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
from core.meta_api.queue import claim_pending_task
from core.observer.enable_grace import grace_is_active, load_enable_grace_map
from core.telegram.client import TelegramBotClient
from core.telegram.handlers.alerts import handle_enable_reco_callback


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_E2E_RECIPIENT_CHAT_ID = 9000


@pytest_asyncio.fixture
async def clean_enable_reco_pipeline(pg_engine):
    """Чистит все таблицы pipeline'а до/после теста.

    Создаёт тестового recipient chat_id=9000 чтобы send_alert → load_active_recipients
    находил кого отправить (send_alert рассылает по recipients, не берёт chat_id из параметра).
    """

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
    """Создаёт оффер + ad в stop_sent + recovered метрики.

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
            text("INSERT INTO offer_rules (offer_id, cpa_threshold) VALUES (:i, :cpa)"),
            {"i": offer_id, "cpa": Decimal("10.00")},
        )
        await conn.execute(
            text("INSERT INTO fb_campaigns (id, campaign_name, offer_id) VALUES (:i, :n, :o)"),
            {"i": campaign_id, "n": f"CMP_E2E_{suffix}", "o": offer_id},
        )
        await conn.execute(
            text("INSERT INTO fb_adsets (id, campaign_id, adset_name) VALUES (:i, :c, :n)"),
            {"i": adset_id, "c": campaign_id, "n": f"ADSET_E2E_{suffix}"},
        )
        await conn.execute(
            text("INSERT INTO fb_ads (id, adset_id, fb_ad_id, ad_name) VALUES (:i, :a, :f, :n)"),
            {"i": ad_id, "a": adset_id, "f": fb_ad_id, "n": f"AD_E2E_{suffix}"},
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
        # 2 «выправленные» метрики после disable: spend низкий, cost_per_lead в норме
        for ts, spend, cpl in (
            (cycle_older, Decimal("1.0"), Decimal("4.0")),
            (cycle_recent, Decimal("0.5"), Decimal("5.0")),
        ):
            await conn.execute(
                text(
                    """
                    INSERT INTO ad_metrics
                        (ad_id, cycle_ts, scan_id, spend, cost_per_lead, deposits)
                    VALUES (:aid, :ts, NULL, :sp, :cpl, 0)
                    """
                ),
                {"aid": ad_id, "ts": ts, "sp": spend, "cpl": cpl},
            )

    return {
        "ad_id": ad_id,
        "fb_ad_id": fb_ad_id,
        "incident_token": incident_token,
    }


class _FakeTGClient:
    """Минимальный TelegramBotClient: фиксирует answer_callback_query вызовы."""

    def __init__(self) -> None:
        self.acks: list[tuple[str, str]] = []

    async def answer_callback_query(self, cq_id: str, text: str = "") -> None:
        self.acks.append((cq_id, text))


# E2E: stopped ad → run_once создаёт реко + TG-алерт → callback → activate_ad mutation → FSM normal.
@pytest.mark.asyncio
async def test_full_cycle_reco_to_enable_task(
    pg_engine,
    stopped_ad_e2e,
    fake_redis_client,
    tg_respx,
    monkeypatch,
) -> None:
    tg_client = TelegramBotClient("fake-token-e2e")

    # Шаг 1: enable_recommendation_worker.run_once находит ad и шлёт алерт
    counts = await run_once(
        pg_engine,
        redis_client=fake_redis_client,
        tg_client=tg_client,
    )
    assert counts["candidates"] == 1
    assert counts["recommendations"] == 1
    assert counts["alerts_sent"] == 1

    # Шаг 2: кнопка привязана к UUID конкретной рекомендации, не только к ad.
    assert len(tg_respx.sent_messages) == 1
    payload = tg_respx.sent_messages[0]
    keyboard = payload.get("reply_markup", {}).get("inline_keyboard")
    assert keyboard is not None
    btn = keyboard[0][0]
    callback_data = btn["callback_data"]
    assert callback_data.startswith("ereco:")
    recommendation_id = callback_data.split(":", 1)[1]
    uuid.UUID(recommendation_id)

    # Шаг 3: пользователь жмёт inline-кнопку → handle_enable_reco_callback
    cb_tg = _FakeTGClient()
    await handle_enable_reco_callback(
        engine=pg_engine,
        client=cb_tg,
        cq_id="ereco-cb-1",
        recommendation_id=recommendation_id,
        username="reviewer",
        chat_id=_E2E_RECIPIENT_CHAT_ID,
    )
    assert any("принята" in t for _, t in cb_tg.acks)

    # Шаг 4: в task_queue появилась activate_ad mutation задача
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
    assert task_row[1] == "meta_api_mutation"
    assert task_row[2] == "pending"
    assert task_row[3]["target_id"] == stopped_ad_e2e["fb_ad_id"]
    assert task_row[4] == "tg:reviewer"

    # Шаг 5: meta_api_worker исполняет activate_ad через fake dispatch
    async def _fake_dispatch(client, p):
        return {"success": True, "graph_response": {"ok": True}, "modified_ids": [p.target_id]}

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)

    claim = await claim_pending_task(pg_engine)
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

    # Шаг 6: FSM-sync после activate_ad → ad_alert_state переходит в 'normal'
    async with pg_engine.connect() as conn:
        fsm_state = (
            await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))
        ).scalar()
    assert fsm_state == "normal"

    await tg_client.close()


# Curator hold: marker появляется только после успешной activation и даёт
# ДОПОЛНИТЕЛЬНЫЙ allowance поверх свежего baseline spend.
@pytest.mark.asyncio
async def test_curator_grace_starts_after_activation_with_incremental_cap(
    pg_engine,
    stopped_ad_e2e,
    fake_redis_client,
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

    callback_tg = _FakeTGClient()
    await handle_enable_reco_callback(
        engine=pg_engine,
        client=callback_tg,
        cq_id="hold-cb",
        recommendation_id=str(rec_id),
        username="reviewer",
        redis_client=fake_redis_client,
    )
    assert any("принята" in text for _, text in callback_tg.acks)
    assert await fake_redis_client.get(f"enable_grace:{stopped_ad_e2e['fb_ad_id']}") is None

    claim = await claim_pending_task(pg_engine)
    assert claim.task is not None
    assert claim.task.payload["params"]["enable_grace"] == {"spend_allowance": "10.00"}

    async def _fake_dispatch(_client, payload):
        return {
            "success": True,
            "graph_response": {"ok": True},
            "modified_ids": [payload.target_id],
        }

    monkeypatch.setattr(worker_main, "dispatch_mutation", _fake_dispatch)
    await process_one_task(
        pg_engine,
        claim.task,
        client=AsyncMock(),
        redis_client=fake_redis_client,
    )

    grace_map = await load_enable_grace_map(fake_redis_client)
    grace = grace_map[stopped_ad_e2e["fb_ad_id"]]
    # Последняя метрика фикстуры: baseline=0.50; allowance=10 → absolute cap=10.50.
    assert grace.baseline_spend == Decimal("0.5")
    assert grace.spend_cap == Decimal("10.50")
    now = _utcnow()
    assert grace_is_active(grace, now=now, spend=Decimal("10.49")) is True
    assert grace_is_active(grace, now=now, spend=Decimal("10.50")) is False
    # Cabinet-day reset / рассинхрон ниже baseline завершает hold fail-safe.
    assert grace_is_active(grace, now=now, spend=Decimal("0.10")) is False


# E2E: повторный клик ereco (двойной тап) → idempotency_key совпал → no-op в БД.
@pytest.mark.asyncio
async def test_double_ereco_callback_does_not_duplicate(
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

    cb_tg = _FakeTGClient()
    # Первый клик создаёт activate_ad task и «расходует» рекомендацию (M-14 follow-up:
    # promoted_to_task_id проставляется — повторный клик отклоняется replay-guard'ом).
    await handle_enable_reco_callback(
        engine=pg_engine,
        client=cb_tg,
        cq_id="cb-1",
        recommendation_id=str(rec_id),
        username="reviewer",
    )
    # Второй клик — рекомендация уже промоутнута → «устарела», без второй задачи.
    await handle_enable_reco_callback(
        engine=pg_engine,
        client=cb_tg,
        cq_id="cb-2",
        recommendation_id=str(rec_id),
        username="reviewer",
    )

    acks = [t for _, t in cb_tg.acks]
    assert any("принята" in a for a in acks)
    assert any("устарел" in a.lower() for a in acks)

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


# M-14 (аудит 2026-07-12): устаревшая ereco-кнопка (нет живой рекомендации) →
# отклоняется «Рекомендация устарела», задача activate_ad НЕ создаётся.
@pytest.mark.asyncio
async def test_ereco_stale_button_rejected(pg_engine, stopped_ad_e2e) -> None:
    # Рекомендацию НЕ создаём (или она уже промоутнута) → кнопка устарела.
    cb_tg = _FakeTGClient()
    await handle_enable_reco_callback(
        engine=pg_engine,
        client=cb_tg,
        cq_id="cb-stale",
        recommendation_id=str(uuid.uuid4()),
        username="reviewer",
    )
    acks = [t for _, t in cb_tg.acks]
    assert any("устарел" in a.lower() for a in acks)

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


# M-14: промоутнутая рекомендация (promoted_to_task_id задан) → кнопка тоже устарела.
@pytest.mark.asyncio
async def test_ereco_promoted_recommendation_rejected(pg_engine, stopped_ad_e2e) -> None:
    async with pg_engine.begin() as conn:
        # Создаём задачу-заглушку, на которую сошлёмся как promoted.
        task_id = (
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by)
                    VALUES ('meta_api_mutation', 'succeeded', :ik, CAST('{}' AS JSONB), 'test')
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

    cb_tg = _FakeTGClient()
    await handle_enable_reco_callback(
        engine=pg_engine,
        client=cb_tg,
        cq_id="cb-promoted",
        recommendation_id=str(rec_id),
        username="reviewer",
    )
    acks = [t for _, t in cb_tg.acks]
    assert any("устарел" in a.lower() for a in acks)
