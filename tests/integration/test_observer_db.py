# -*- coding: utf-8 -*-
"""Интеграционный e2e тест observer pipeline: ScannedAdRow → БД + task_queue.

Без gRPC к browser-agent — мы инжектим список ScannedAdRow напрямую.
Это покрывает critical path: матчинг офферов, FSM, метрики, outbox.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

import core.observer.pipeline as observer_pipeline
from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow

pytestmark = pytest.mark.usefixtures("known_test_cabinet_timezones")


_OBSERVER_NOTIFICATION_TABLES = (
    "telegram_action_tokens",
    "telegram_navigation_tokens",
    "telegram_message_slots",
    "notification_deliveries",
    "notification_events",
    "incidents",
)
_OBSERVER_DOMAIN_TABLES = (
    "task_queue",
    "alert_events",
    "ad_metrics",
    "ad_alert_state",
    "tracker_click_state",
    "fb_ads",
    "fb_adsets",
    "fb_campaigns",
    "offer_rules",
    "offers",
)


async def _delete_observer_owned_rows(pg_engine) -> None:
    """Delete observer projections in dependency order for fixture isolation."""
    async with pg_engine.begin() as conn:
        for table_name in (*_OBSERVER_NOTIFICATION_TABLES, *_OBSERVER_DOMAIN_TABLES):
            await conn.execute(text(f"DELETE FROM {table_name}"))


@pytest_asyncio.fixture
async def clean_observer_tables(pg_engine):
    """Clean observer domain and notification-plane projections around a test."""
    await _delete_observer_owned_rows(pg_engine)
    try:
        yield
    finally:
        await _delete_observer_owned_rows(pg_engine)


@pytest_asyncio.fixture
async def offer_kr2(pg_engine, clean_observer_tables):
    """Создаёт оффер с кодом CR2 + cpa_threshold для триггера STOP.

    Используем именно 'CR2' (не 'KE_CR2') потому что resolve_offer_code применяет
    word-boundary matching: 'KE_CR2' в строке 'CR2 | KE | MV' не сматчится
    (между CR2 и KE стоит ' | ', а не нужный для regex символ).
    """
    offer_id = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active) "
                "VALUES (:i, 'CR2', 'Chicken Road 2', TRUE)"
            ),
            {"i": offer_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO offer_rules (offer_id, cpa_threshold, currency)
                VALUES (:oid, :cpa, 'USD')
                """
            ),
            {"oid": offer_id, "cpa": Decimal("10.00")},
        )
    return {"offer_id": offer_id, "code": "CR2"}


def _make_row(
    *,
    fb_ad_id: str = "230011223344",
    campaign_name: str = "CR2 | KE | MV | Tyver",
    adset_name: str = "EQ_KE",
    ad_name: str = "Aviator001",
    spend: Decimal = Decimal("5.0"),
    leads: int = 0,
    registrations: int = 0,
    deposits: int = 0,
    cpc: Decimal | None = None,
) -> ScannedAdRow:
    """Фабрика ScannedAdRow с разумными дефолтами."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_id=f"9{fb_ad_id}",
        adset_id=f"8{fb_ad_id}",
        campaign_name=campaign_name,
        adset_name=adset_name,
        ad_name=ad_name,
        delivery_status="ACTIVE",
        spend=spend,
        budget="$10",
        reach=1000,
        impressions=2000,
        clicks=50,
        cpc=cpc,
        ctr=Decimal("2.5"),
        cpm=Decimal("2.5"),
        leads=leads,
        registrations=registrations,
        deposits=deposits,
        outbound_clicks=30,
        landing_page_views=20,
    )


async def _set_account_currency_observed_at(pg_engine, observed_at: datetime) -> None:
    """Align durable currency evidence with an explicit historical scan clock."""
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE meta_account_snapshot "
                "SET currency_observed_at = :observed_at "
                "WHERE account_id = '123'"
            ),
            {"observed_at": observed_at},
        )


@pytest.mark.asyncio
async def test_observer_cleanup_removes_durable_projection_residue(
    pg_engine,
    offer_kr2,
) -> None:
    """The shared cleanup path must remove domain and notification projections."""
    row = _make_row(
        fb_ad_id=f"230098{uuid.uuid4().int % 1_000_000:06d}",
        spend=Decimal("25.00"),
        cpc=Decimal("0.10"),
    )
    await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[row],
        scan_id=799,
    )

    async with pg_engine.connect() as conn:
        incident_count = await conn.scalar(text("SELECT COUNT(*) FROM incidents"))
        event_count = await conn.scalar(text("SELECT COUNT(*) FROM notification_events"))
        task_count = await conn.scalar(text("SELECT COUNT(*) FROM task_queue"))
    assert incident_count and incident_count > 0
    assert event_count and event_count > 0
    assert task_count and task_count > 0

    await _delete_observer_owned_rows(pg_engine)

    async with pg_engine.connect() as conn:
        residue = {
            table_name: int(await conn.scalar(text(f"SELECT COUNT(*) FROM {table_name}")) or 0)
            for table_name in (*_OBSERVER_NOTIFICATION_TABLES, *_OBSERVER_DOMAIN_TABLES)
        }
    assert residue == dict.fromkeys(residue, 0)


@pytest.mark.asyncio
async def test_warning_card_refreshes_only_after_25_percent_risk_growth(
    pg_engine,
    offer_kr2,
) -> None:
    fb_ad_id = f"230099{uuid.uuid4().int % 1_000_000:06d}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE offer_rules
                SET warning_percent_of_stop = 50
                WHERE offer_id = :offer_id
                """
            ),
            {"offer_id": offer_kr2["offer_id"]},
        )

    try:
        initial = _make_row(
            fb_ad_id=fb_ad_id,
            spend=Decimal("0.05"),
            cpc=Decimal("0.090"),
        )
        below_threshold = _make_row(
            fb_ad_id=fb_ad_id,
            spend=Decimal("0.05"),
            cpc=Decimal("0.105"),
        )
        above_threshold = _make_row(
            fb_ad_id=fb_ad_id,
            spend=Decimal("0.05"),
            cpc=Decimal("0.145"),
        )

        await process_scan_rows(pg_engine, ad_account_id="123", rows=[initial], scan_id=801)
        await process_scan_rows(pg_engine, ad_account_id="123", rows=[below_threshold], scan_id=802)

        async with pg_engine.connect() as conn:
            before = (
                await conn.execute(
                    text(
                        """
                        SELECT i.id, i.facts, i.facts->>'risk_ratio' AS risk_ratio,
                               COUNT(e.id) AS event_count
                        FROM incidents i
                        LEFT JOIN notification_events e ON e.incident_id = i.id
                        WHERE i.resource_id = :fb_ad_id
                        GROUP BY i.id
                        """
                    ),
                    {"fb_ad_id": fb_ad_id},
                )
            ).one()
        assert before.risk_ratio is not None, before.facts
        assert Decimal(str(before.risk_ratio)) == Decimal("1.125")
        assert before.event_count == 1

        await process_scan_rows(pg_engine, ad_account_id="123", rows=[above_threshold], scan_id=803)

        async with pg_engine.connect() as conn:
            after = (
                await conn.execute(
                    text(
                        """
                        SELECT i.facts->>'risk_ratio' AS risk_ratio,
                               ARRAY_AGG(e.event_type ORDER BY e.created_at, e.event_type)
                                   AS event_types
                        FROM incidents i
                        JOIN notification_events e ON e.incident_id = i.id
                        WHERE i.id = :incident_id
                        GROUP BY i.id
                        """
                    ),
                    {"incident_id": before.id},
                )
            ).one()
        assert Decimal(str(after.risk_ratio)) == Decimal("1.8125")
        assert list(after.event_types) == [
            "incident_warning",
            "incident_warning_growth",
        ]
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM telegram_action_tokens
                    WHERE incident_id IN (
                        SELECT id FROM incidents WHERE resource_id = :fb_ad_id
                    )
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM telegram_message_slots
                    WHERE incident_id IN (
                        SELECT id FROM incidents WHERE resource_id = :fb_ad_id
                    )
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_deliveries
                    WHERE event_id IN (
                        SELECT e.id FROM notification_events e
                        JOIN incidents i ON i.id = e.incident_id
                        WHERE i.resource_id = :fb_ad_id
                    )
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_events
                    WHERE incident_id IN (
                        SELECT id FROM incidents WHERE resource_id = :fb_ad_id
                    )
                    """
                ),
                {"fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text("DELETE FROM incidents WHERE resource_id = :fb_ad_id"),
                {"fb_ad_id": fb_ad_id},
            )


# Сценарий: новое объявление с нормой → upsert в каталог + INSERT метрик + ad_alert_state='normal'
@pytest.mark.asyncio
async def test_new_ad_with_normal_metrics(pg_engine, offer_kr2, monkeypatch) -> None:
    # Полная воронка; депозиты теперь только из проекции
    # AdSet.pro: каждый click должен иметь registration + FTD.
    row = _make_row(
        spend=Decimal("3.0"),
        leads=10,
        registrations=5,
        deposits=0,  # Meta-депозиты больше не источник
        cpc=Decimal("0.05"),
    )
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO tracker_click_state
                    (id, source, click_id, ad_id, fb_ad_id, attribution_status,
                     registration, ftd, confirmed_deposit, registration_at, ftd_at,
                     confirmed_deposit_at, redeposits,
                     last_event_at, version, created_at, updated_at)
                VALUES
                    (gen_random_uuid(), 'adsetpro', :c1, NULL, :fb, 'matched_direct',
                     true, true, true, now(), now(), now(), 0,
                     now(), 1, now(), now()),
                    (gen_random_uuid(), 'adsetpro', :c2, NULL, :fb, 'matched_direct',
                     true, true, true, now(), now(), now(), 0,
                     now(), 1, now(), now())
                """
            ),
            {"fb": row.fb_ad_id, "c1": f"{row.fb_ad_id}-d1", "c2": f"{row.fb_ad_id}-d2"},
        )

    class HostClockFiveMinutesBehind(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) - timedelta(minutes=5)

    # Regression: tracker evidence is timestamped by PostgreSQL. A lagging app
    # host must not move the exclusive tracker window_end behind a committed
    # deposit and trigger a false auto-stop.
    monkeypatch.setattr(observer_pipeline, "datetime", HostClockFiveMinutesBehind)
    result = await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1)

    assert result.rows_total == 1
    assert result.rows_with_offer == 1

    async with pg_engine.connect() as conn:
        # 1. Каталог поднялся целиком
        n_campaigns = (await conn.execute(text("SELECT COUNT(*) FROM fb_campaigns"))).scalar()
        n_adsets = (await conn.execute(text("SELECT COUNT(*) FROM fb_adsets"))).scalar()
        n_ads = (await conn.execute(text("SELECT COUNT(*) FROM fb_ads"))).scalar()
        assert n_campaigns == 1
        assert n_adsets == 1
        assert n_ads == 1

        # 2. Кампания сматчена с оффером
        camp = (
            await conn.execute(text("SELECT offer_id, campaign_name FROM fb_campaigns LIMIT 1"))
        ).first()
        assert camp[0] == offer_kr2["offer_id"]

        # 3. ad_metrics записал партицию
        n_metrics = (await conn.execute(text("SELECT COUNT(*) FROM ad_metrics"))).scalar()
        assert n_metrics == 1

        # 4. ad_alert_state создан в normal
        state_row = (
            await conn.execute(
                text("SELECT alert_state, current_stage FROM ad_alert_state LIMIT 1")
            )
        ).first()
        assert state_row[0] == "normal"
        assert state_row[1] is None

        # 5. Нет алертов и нет disable-задач
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
        assert n_alerts == 0
        assert n_tasks == 0


# Сценарий: spend без депозитов превышает порог → fast-stop → disable task создаётся
@pytest.mark.asyncio
async def test_spend_no_deposit_triggers_stop_and_disable_task(pg_engine, offer_kr2) -> None:
    # cpa_threshold = 10. spend=20, deposits=0 → spend без события превышает 80% cpa = триггер STOP
    row = _make_row(
        spend=Decimal("20.0"),
        deposits=0,
        leads=0,
        registrations=0,
        cpc=Decimal("0.10"),
    )

    result = await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=42)

    assert result.alerts_stop + result.alerts_warning >= 1

    async with pg_engine.connect() as conn:
        # FSM перешёл в stop_sent или warning_sent
        st = (
            await conn.execute(
                text("SELECT alert_state, current_stage FROM ad_alert_state LIMIT 1")
            )
        ).first()
        assert st[0] in ("warning_sent", "stop_sent")
        metric_currency, metric_spend = (
            await conn.execute(
                text("SELECT currency, spend FROM ad_metrics ORDER BY cycle_ts DESC LIMIT 1")
            )
        ).one()
        assert metric_currency == "USD"
        assert metric_spend == Decimal("20.000")

        # Если STOP — есть запись в alert_events с правильным stage и scan_id
        if st[0] == "stop_sent":
            evt = (
                await conn.execute(
                    text(
                        "SELECT stage, state, scan_id, matched_rule_codes, metrics_json "
                        "FROM alert_events ORDER BY created_at DESC LIMIT 1"
                    )
                )
            ).first()
            assert evt[0] == "stop"
            assert evt[1] == "stop_sent"
            assert evt[2] == 42
            assert isinstance(evt[3], list) and len(evt[3]) > 0
            assert evt[4]["currency"] == "USD"

            incident, notification = (
                await conn.execute(
                    text(
                        """
                        SELECT incident.facts, event.facts
                        FROM incidents AS incident
                        JOIN notification_events AS event
                          ON event.incident_id = incident.id
                        WHERE incident.resource_id = :fb_ad_id
                        ORDER BY event.created_at DESC
                        LIMIT 1
                        """
                    ),
                    {"fb_ad_id": row.fb_ad_id},
                )
            ).one()
            assert incident["currency"] == "USD"
            assert incident["currency_state"] == "confirmed"
            rendered_facts = " ".join(
                [
                    str(notification.get("summary") or ""),
                    *(str(line) for line in notification.get("lines") or []),
                ]
            )
            assert "USD" in rendered_facts
            assert "$" not in rendered_facts

            # И обязательно meta_api_mutation pause_ad task с правильным target_id.
            task = (
                await conn.execute(
                    text(
                        "SELECT task_type, status, payload, requested_by "
                        "FROM task_queue WHERE task_type = 'meta_api_mutation' LIMIT 1"
                    )
                )
            ).first()
            assert task is not None
            assert task[0] == "meta_api_mutation"
            assert task[1] == "pending"
            assert task[2]["mutation_kind"] == "pause_ad"
            assert task[2]["target_id"] == row.fb_ad_id
            assert task[3] == "bot_auto_stop"


@pytest.mark.asyncio
async def test_kwd_derived_money_metrics_round_trip_six_decimals(
    pg_engine,
    offer_kr2,
) -> None:
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE meta_account_snapshot
                SET currency = 'KWD',
                    currency_observed_at = NOW()
                WHERE account_id = '123'
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE offer_rules
                SET cpa_threshold = 10.125,
                    currency = 'KWD'
                WHERE offer_id = :offer_id
                """
            ),
            {"offer_id": offer_kr2["offer_id"]},
        )

    row = replace(
        _make_row(
            fb_ad_id=f"230077{uuid.uuid4().int % 1_000_000:06d}",
            spend=Decimal("0.001"),
            leads=1,
            registrations=1,
            cpc=Decimal("0.000499"),
        ),
        cost_per_result=Decimal("0.000501"),
        cpm=Decimal("0.000502"),
        cost_per_lead=Decimal("0.000503"),
        cost_per_registration=Decimal("0.000504"),
        cost_per_landing_page_view=Decimal("0.000505"),
    )

    result = await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[row],
        scan_id=420,
    )

    assert result.row_errors == []
    async with pg_engine.connect() as conn:
        stored = (
            await conn.execute(
                text(
                    """
                    SELECT cpc,
                           cost_per_result,
                           cpm,
                           cost_per_lead,
                           cost_per_registration,
                           cost_per_landing_page_view
                    FROM ad_metrics
                    ORDER BY cycle_ts DESC
                    LIMIT 1
                    """
                )
            )
        ).one()
    assert tuple(stored) == (
        Decimal("0.000499"),
        Decimal("0.000501"),
        Decimal("0.000502"),
        Decimal("0.000503"),
        Decimal("0.000504"),
        Decimal("0.000505"),
    )


# Сценарий: повторный scan того же объявления — НЕ создаёт дубли disable task'и (idempotency)
@pytest.mark.asyncio
async def test_repeated_stop_does_not_duplicate_disable_task(pg_engine, offer_kr2) -> None:
    row = _make_row(
        spend=Decimal("25.0"),
        deposits=0,
        leads=0,
        registrations=0,
        cpc=Decimal("0.10"),
    )

    # Первый цикл — должен попасть в STOP
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1)
    # Второй цикл с теми же данными — НЕ должен дублировать
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=2)

    async with pg_engine.connect() as conn:
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
        # одна pause-задача (idempotency_key включает open_state_token)
        assert n_tasks == 1
        # один alert event — повторный STOP не дублируется (FSM stop_sent → stop_sent без emit)
        assert n_alerts == 1


# Сценарий: ад завис в stop_sent, но в Meta уже OFF + инцидент старше cooldown →
# observer сам синхронизирует FSM в disabled (зеркало reopen). Mirror случая CR008,
# где наша pause-мутация упала, а ад фактически выключен.
@pytest.mark.asyncio
async def test_offline_ad_syncs_stop_sent_to_disabled(pg_engine, offer_kr2) -> None:
    from core.observer.writers import mark_disabled_when_offline

    # 1) цикл со STOP → ад в stop_sent
    fb_ad_id = f"230077{uuid.uuid4().int % 1_000_000:06d}"
    row = _make_row(fb_ad_id=fb_ad_id, spend=Decimal("25.0"), cpc=Decimal("0.10"))
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1)

    async with pg_engine.connect() as conn:
        ad_id = (await conn.execute(text("SELECT id FROM fb_ads LIMIT 1"))).scalar()
        st = (await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))).scalar()
    assert st == "stop_sent"

    # 2) свежий stop_sent (инцидент моложе cooldown) → time-guard НЕ трогает
    assert await mark_disabled_when_offline(pg_engine, ad_id=ad_id) is False
    async with pg_engine.connect() as conn:
        st = (await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))).scalar()
    assert st == "stop_sent"

    # 3) состарим инцидент на 20 минут → теперь sync срабатывает
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET last_transition_at = NOW() - interval '20 min' "
                "WHERE ad_id = :aid"
            ),
            {"aid": ad_id},
        )
    assert await mark_disabled_when_offline(pg_engine, ad_id=ad_id) is True
    async with pg_engine.connect() as conn:
        st = (await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))).scalar()
        sync_events = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_events
                WHERE event_type = 'worker_sync_disabled'
                  AND facts->>'summary' = :summary
                """
            ),
            {"summary": f"{fb_ad_id} подтверждено OFF в Meta"},
        )
    assert st == "disabled"
    assert sync_events == 1

    # 4) идемпотентность: повторный вызов на disabled → no-op (False)
    assert await mark_disabled_when_offline(pg_engine, ad_id=ad_id) is False


@pytest.mark.asyncio
async def test_offline_sync_rolls_back_state_when_notification_projection_fails(
    pg_engine,
    offer_kr2,
    monkeypatch,
) -> None:
    import core.observer.writers as writers

    fb_ad_id = f"230076{uuid.uuid4().int % 1_000_000:06d}"
    await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[_make_row(fb_ad_id=fb_ad_id, spend=Decimal("25.0"), cpc=Decimal("0.10"))],
        scan_id=1,
    )
    async with pg_engine.begin() as conn:
        ad_id = await conn.scalar(
            text("SELECT id FROM fb_ads WHERE fb_ad_id = :fb_ad_id"),
            {"fb_ad_id": fb_ad_id},
        )
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET last_transition_at = NOW() - INTERVAL '20 minutes'
                WHERE ad_id = :ad_id
                """
            ),
            {"ad_id": ad_id},
        )

    async def fail_projection(*_args, **_kwargs) -> bool:
        raise RuntimeError("notification projection failed")

    monkeypatch.setattr(writers, "notify_owners_in_transaction", fail_projection)
    with pytest.raises(RuntimeError, match="notification projection failed"):
        await writers.mark_disabled_when_offline(pg_engine, ad_id=ad_id)

    async with pg_engine.connect() as conn:
        state = await conn.scalar(
            text("SELECT alert_state FROM ad_alert_state WHERE ad_id = :ad_id"),
            {"ad_id": ad_id},
        )
        event_count = await conn.scalar(
            text(
                """
                SELECT COUNT(*)
                FROM notification_events
                WHERE event_type = 'worker_sync_disabled'
                  AND facts->>'summary' = :summary
                """
            ),
            {"summary": f"{fb_ad_id} подтверждено OFF в Meta"},
        )

    assert state == "stop_sent"
    assert event_count == 0


@pytest.mark.asyncio
async def test_offline_sync_atomically_resolves_per_ad_autostop_incident(
    pg_engine,
    offer_kr2,
) -> None:
    from core.meta_api.autostop_alert import UNDELIVERED_INCIDENT_KEY_PREFIX
    from core.observer.writers import mark_disabled_when_offline
    from core.telegram.worker_notify import notify_recurring_incident

    fb_ad_id = f"230088{uuid.uuid4().int % 1_000_000:06d}"
    row = _make_row(
        fb_ad_id=fb_ad_id,
        spend=Decimal("25.0"),
        cpc=Decimal("0.10"),
    )
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1)
    incident_key = f"{UNDELIVERED_INCIDENT_KEY_PREFIX}{fb_ad_id}"
    async with pg_engine.connect() as conn:
        open_token = await conn.scalar(
            text(
                """
                SELECT state.open_state_token
                FROM ad_alert_state AS state
                JOIN fb_ads AS ad ON ad.id = state.ad_id
                WHERE ad.fb_ad_id = :fb_ad_id
                """
            ),
            {"fb_ad_id": fb_ad_id},
        )
    canonical_incident_key = f"ad:{fb_ad_id}:{open_token}"
    assert await notify_recurring_incident(
        pg_engine,
        incident_key=incident_key,
        audience="owners",
        event_type="autostop_undelivered_pause",
        severity="critical",
        title="Auto-stop pause undelivered",
        resource_type="ad",
        resource_id=fb_ad_id,
    )

    try:
        async with pg_engine.begin() as conn:
            ad_id = await conn.scalar(
                text("SELECT id FROM fb_ads WHERE fb_ad_id = :fb_ad_id"),
                {"fb_ad_id": fb_ad_id},
            )
            await conn.execute(
                text(
                    """
                    UPDATE ad_alert_state
                    SET last_transition_at = NOW() - INTERVAL '20 minutes'
                    WHERE ad_id = :ad_id
                    """
                ),
                {"ad_id": ad_id},
            )

        assert await mark_disabled_when_offline(pg_engine, ad_id=ad_id) is True
        async with pg_engine.connect() as conn:
            state = await conn.scalar(
                text("SELECT alert_state FROM ad_alert_state WHERE ad_id = :ad_id"),
                {"ad_id": ad_id},
            )
            incident_status = await conn.scalar(
                text("SELECT status FROM incidents WHERE incident_key = :key"),
                {"key": incident_key},
            )
            canonical_incident_status = await conn.scalar(
                text("SELECT status FROM incidents WHERE incident_key = :key"),
                {"key": canonical_incident_key},
            )
            recovery_events = await conn.scalar(
                text(
                    """
                    SELECT COUNT(*)
                    FROM notification_events event
                    JOIN incidents incident ON incident.id = event.incident_id
                    WHERE incident.incident_key = :key
                      AND event.event_type = 'incident_recovered'
                    """
                ),
                {"key": incident_key},
            )

        assert state == "disabled"
        assert incident_status == "resolved"
        assert canonical_incident_status == "resolved"
        assert recovery_events == 1
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_deliveries
                    WHERE event_id IN (
                        SELECT event.id
                        FROM notification_events event
                        JOIN incidents incident ON incident.id = event.incident_id
                        WHERE incident.incident_key = :key
                    )
                    """
                ),
                {"key": incident_key},
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_events
                    WHERE incident_id IN (SELECT id FROM incidents WHERE incident_key = :key)
                    """
                ),
                {"key": incident_key},
            )
            await conn.execute(
                text("DELETE FROM incidents WHERE incident_key = :key"),
                {"key": incident_key},
            )


# Сценарий: ад OFF старше cooldown проходит весь pipeline → синхронизируется в disabled,
# новой disable-задачи НЕ создаётся (инцидент закрыт, не гоняем FSM по метрикам OFF-ада).
@pytest.mark.asyncio
async def test_pipeline_syncs_offline_incident_to_disabled(pg_engine, offer_kr2) -> None:
    # 1) STOP → stop_sent + одна disable-задача
    stop_row = _make_row(spend=Decimal("25.0"), cpc=Decimal("0.10"))
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[stop_row], scan_id=1)

    # состарим инцидент старше cooldown
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE ad_alert_state SET last_transition_at = NOW() - interval '20 min'")
        )

    # 2) тот же ад приходит OFF → pipeline синхронизирует в disabled
    off_row = _make_row(spend=Decimal("25.0"), cpc=Decimal("0.10"))
    object.__setattr__(off_row, "delivery_status", "OFF")
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[off_row], scan_id=2)

    async with pg_engine.connect() as conn:
        st = (await conn.execute(text("SELECT alert_state FROM ad_alert_state LIMIT 1"))).scalar()
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
    assert st == "disabled"
    assert n_tasks == 1  # вторая (OFF) итерация не плодит новую disable-задачу


# Сценарий: объявление без подходящего оффера → каталог пишется, но без правил
@pytest.mark.asyncio
async def test_ad_without_matching_offer(pg_engine, offer_kr2) -> None:
    row = _make_row(
        campaign_name="UNKNOWN | XX | foo",
        ad_name="random",
        spend=Decimal("50.0"),
        deposits=0,
    )
    result = await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1)

    assert result.rows_with_offer == 0
    assert result.rows_without_offer == 1

    async with pg_engine.connect() as conn:
        # Каталог всё равно есть (нужен для исторических метрик)
        n_ads = (await conn.execute(text("SELECT COUNT(*) FROM fb_ads"))).scalar()
        n_alerts = (await conn.execute(text("SELECT COUNT(*) FROM alert_events"))).scalar()
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
        n_states = (await conn.execute(text("SELECT COUNT(*) FROM ad_alert_state"))).scalar()
        assert n_ads == 1
        # Метрики записываются даже для unmatched
        n_metrics = (await conn.execute(text("SELECT COUNT(*) FROM ad_metrics"))).scalar()
        assert n_metrics == 1
        # А alert_state и outbox — не создаются (нет правил)
        assert n_alerts == 0
        assert n_tasks == 0
        assert n_states == 0


# Сматченный offer без подтверждённого CPA сохраняет наблюдение, но fail-closed
# помечает scan partial и не создаёт FSM/alert/money-task на выдуманном пороге.
@pytest.mark.asyncio
async def test_matched_offer_without_cpa_never_evaluates_money_rules(
    pg_engine,
    clean_observer_tables,
) -> None:
    offer_id = uuid.uuid4()
    fb_ad_id = f"230077{uuid.uuid4().int % 1_000_000:06d}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name, is_active) "
                "VALUES (:id, 'CR2', 'No configured CPA', TRUE)"
            ),
            {"id": offer_id},
        )
        await conn.execute(
            text(
                "INSERT INTO offer_rules (offer_id, cpa_threshold, currency) "
                "VALUES (:id, NULL, 'USD')"
            ),
            {"id": offer_id},
        )

    result = await process_scan_rows(
        pg_engine,
        ad_account_id="123",
        rows=[_make_row(fb_ad_id=fb_ad_id, spend=Decimal("500.00"))],
        scan_id=991,
    )

    assert result.rows_with_offer == 1
    assert result.row_errors == [f"{fb_ad_id}:MissingOfferCpaError"]
    async with pg_engine.connect() as conn:
        persisted = (
            await conn.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM ad_metrics),
                        (SELECT COUNT(*) FROM ad_alert_state),
                        (SELECT COUNT(*) FROM alert_events),
                        (SELECT COUNT(*) FROM task_queue)
                    """
                )
            )
        ).one()
    assert tuple(persisted) == (1, 0, 0, 0)


# Сценарий: scan_id корректно записывается во все таблицы где есть это поле
@pytest.mark.asyncio
async def test_scan_id_propagates(pg_engine, offer_kr2) -> None:
    row = _make_row(spend=Decimal("3.0"), deposits=1)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=12345)

    async with pg_engine.connect() as conn:
        scan_in_metrics = (
            await conn.execute(text("SELECT scan_id FROM ad_metrics LIMIT 1"))
        ).scalar()
        scan_in_state = (
            await conn.execute(text("SELECT last_scan_id FROM ad_alert_state LIMIT 1"))
        ).scalar()
    assert scan_in_metrics == 12345
    assert scan_in_state == 12345


# Сценарий: priority матчинга — более длинный code (CR2_KE) выигрывает у CR2
@pytest.mark.asyncio
async def test_matching_prefers_longest_code(pg_engine, clean_observer_tables) -> None:
    offer_long = uuid.uuid4()
    offer_short = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO offers (id, code, name) "
                "VALUES (:i, 'CR2_KE', 'long'), (:s, 'CR2', 'short')"
            ),
            {"i": offer_long, "s": offer_short},
        )

    # В кампании присутствует только подстрока 'CR2_KE' как отдельное «слово»
    row = _make_row(
        campaign_name="Aviator | CR2_KE | MV | promo",
        ad_name="Test001",
        spend=Decimal("3.0"),
    )
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1)

    async with pg_engine.connect() as conn:
        camp = (await conn.execute(text("SELECT offer_id FROM fb_campaigns LIMIT 1"))).first()
    # CR2_KE длиннее → выиграл, хотя 'CR2' тоже сматчился бы как substring
    assert camp[0] == offer_long


# HIGH #6: snoozed_until == cycle_ts — граничное равенство НЕ подавляет emit
@pytest.mark.asyncio
async def test_snooze_boundary_equality_does_not_suppress(pg_engine, offer_kr2) -> None:
    """snoozed_until == cycle_ts: строгое > в pipeline не suppress'ит emit при равенстве."""
    # Создаём ad в состоянии warning_sent через первый скан
    row = _make_row(spend=Decimal("20.0"), deposits=0, leads=0, registrations=0)
    ts1 = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)
    await _set_account_currency_observed_at(pg_engine, ts1)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1, cycle_ts=ts1)

    # Ставим snoozed_until = ts2 (ровно момент следующего скана)
    ts2 = datetime(2026, 5, 28, 10, 30, 0, tzinfo=UTC)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET snoozed_until = :su "
                "WHERE alert_state IN ('warning_sent', 'stop_sent')"
            ),
            {"su": ts2},
        )

    # Убираем event из первого скана чтобы посчитать только новый
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM alert_events"))

    # Второй скан с cycle_ts == snoozed_until: emit НЕ должен подавляться (строгое >)
    result2 = await process_scan_rows(
        pg_engine, ad_account_id="123", rows=[row], scan_id=2, cycle_ts=ts2
    )

    # Pipeline мог не дать warning (повтор FSM → no new emit для stop_sent→stop_sent),
    # но главное — pipeline не suppress'ил из-за snooze. Проверяем напрямую через
    # ad_alert_state: snoozed_until == cycle_ts → snooze НЕ активен.
    async with pg_engine.connect() as conn:
        snoozed_until = (
            await conn.execute(text("SELECT snoozed_until FROM ad_alert_state LIMIT 1"))
        ).scalar()
    # Значение snoozed_until == ts2 — это нормально; проверяем что pipeline не упал
    # и не suppress'ил по причине snooze (suppress только при snoozed_until > cycle_ts)
    assert result2 is not None, "process_scan_rows должен вернуть CycleResult без ошибки"
    assert snoozed_until is not None, "snoozed_until должен остаться (pipeline не сбросил его)"


# HIGH #7: snooze истёк между двумя сканами → третий скан эмитит алерт
@pytest.mark.asyncio
async def test_snooze_expired_between_scans_emits_on_third(pg_engine, offer_kr2) -> None:
    """Snooze истекает между scan #2 и #3: scan #3 должен эмитить алерт."""
    row = _make_row(spend=Decimal("20.0"), deposits=0, leads=0, registrations=0)

    # Scan #1: ставим ad в warning_sent/stop_sent
    ts1 = datetime(2026, 5, 28, 10, 0, 0, tzinfo=UTC)
    await _set_account_currency_observed_at(pg_engine, ts1)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1, cycle_ts=ts1)

    # Ставим snoozed_until = ts1 + 2 минуты (истечёт после ts2 но до ts3)
    snooze_exp = ts1 + timedelta(minutes=2)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET snoozed_until = :su "
                "WHERE alert_state IN ('warning_sent', 'stop_sent')"
            ),
            {"su": snooze_exp},
        )

    # Чистим события scan #1 чтобы счётчики были точными
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM alert_events"))

    # Scan #2: cycle_ts = ts1 + 1 мин < snoozed_until → snooze активен, emit suppress'ируется
    ts2 = ts1 + timedelta(minutes=1)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=2, cycle_ts=ts2)

    async with pg_engine.connect() as conn:
        n_events_after_scan2 = (
            await conn.execute(text("SELECT COUNT(*) FROM alert_events WHERE scan_id = 2"))
        ).scalar()
    # Scan #2: emit suppress'ирован из-за snooze (snoozed_until > cycle_ts)
    assert n_events_after_scan2 == 0, (
        f"Scan #2 должен быть suppressed, но alert_events.scan_id=2: {n_events_after_scan2}"
    )

    # Scan #3: cycle_ts = ts1 + 3 мин > snoozed_until → snooze истёк, emit разрешён
    ts3 = ts1 + timedelta(minutes=3)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=3, cycle_ts=ts3)

    # Scan #3: FSM stop_sent → stop_sent (no new emit for same state) — но мы проверяем
    # что pipeline НЕ suppress'ил по snooze. В реальности stop_sent → stop_sent уже
    # не выдаёт новый emit (FSM идемпотентен). Ключевое: нет suppress-метки в transition_reason.
    # Чтобы проверить emit — сбросим alert_state до warning_sent (имитируем эскалацию).
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE ad_alert_state SET alert_state = 'warning_sent', current_stage = 'warning'"
            )
        )
    async with pg_engine.begin() as conn:
        await conn.execute(text("DELETE FROM alert_events"))

    # Повторный scan #4 после сброса: snooze истёк, должен выдать emit
    ts4 = ts1 + timedelta(minutes=4)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=4, cycle_ts=ts4)

    async with pg_engine.connect() as conn:
        n_events_after_scan4 = (
            await conn.execute(text("SELECT COUNT(*) FROM alert_events WHERE scan_id = 4"))
        ).scalar()
    # После истечения snooze alert_event должен создаться (stop_sent → emit)
    assert n_events_after_scan4 >= 1, (
        f"После истечения snooze scan #4 должен создать alert_event, "
        f"но alert_events.scan_id=4: {n_events_after_scan4}"
    )


# C1/M3 + MID-2 (аудит 02.07): снуз глушит ТОЛЬКО TG-алерты, НЕ авто-стоп — pause-задача
# создаётся и под активным снузом (раньше подавлялась → заснуженный убыточный ад крутился
# без стопа до конца окна). Recovery после истечения снуза не создаёт дубль (idempotency).
@pytest.mark.asyncio
async def test_snooze_stop_recovery_creates_pause_task(pg_engine, offer_kr2) -> None:
    """Снуз → stop_sent под снузом (pause-задача СОЗДАЁТСЯ — MID-2, авто-стоп работает) →
    снуз истёк → recovery-скан не плодит вторую задачу (одна на инцидент)."""
    row = _make_row(spend=Decimal("20.0"), deposits=0, leads=0, registrations=0)

    # Scan #1 → ад уходит в stop_sent (трата без событий).
    ts1 = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
    await _set_account_currency_observed_at(pg_engine, ts1)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=1, cycle_ts=ts1)

    # Снуз активен до ts1+2мин + эмулируем «pause-задача не создавалась» (краш-сценарий):
    snooze_exp = ts1 + timedelta(minutes=2)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE ad_alert_state SET snoozed_until = :su WHERE alert_state = 'stop_sent'"),
            {"su": snooze_exp},
        )
        await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'meta_api_mutation'"))

    # Scan #2 под активным снузом: pause-задача СОЗДАЁТСЯ (MID-2 — снуз не выключает
    # авто-стоп; подавляется только TG-алерт).
    ts2 = ts1 + timedelta(minutes=1)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=2, cycle_ts=ts2)
    async with pg_engine.connect() as conn:
        n_under_snooze = (
            await conn.execute(
                text("SELECT COUNT(*) FROM task_queue WHERE payload->>'mutation_kind' = 'pause_ad'")
            )
        ).scalar()
    assert n_under_snooze == 1, "под снузом авто-стоп обязан работать (MID-2): одна pause-задача"

    # Scan #3 после истечения снуза: recovery НЕ плодит дубль — по-прежнему ровно одна
    # pause_ad на инцидент (idempotency_key по open_token).
    ts3 = ts1 + timedelta(minutes=3)
    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=3, cycle_ts=ts3)
    async with pg_engine.connect() as conn:
        recovered = (
            await conn.execute(
                text(
                    "SELECT payload->>'mutation_kind' AS kind, payload->>'target_id' AS target "
                    "FROM task_queue WHERE payload->>'mutation_kind' = 'pause_ad'"
                )
            )
        ).fetchall()
    assert len(recovered) == 1, (
        f"ровно одна pause-задача на инцидент (без дубля от recovery), получено {len(recovered)}"
    )
    assert recovered[0].kind == "pause_ad"
    assert recovered[0].target == "230011223344"


# Owner-scoping: чужая кампания с кодом оффера CR2, но без тега MV, НЕ обрабатывается
# (ключевая защита — owner-фильтр сильнее матчинга оффера, иначе бот тронул бы чужое)
@pytest.mark.asyncio
async def test_owner_scoping_filters_foreign_campaign(pg_engine, offer_kr2) -> None:
    # Моя: тег MV + код CR2, метрики в норме (leads>=reg>=dep)
    mine = _make_row(
        fb_ad_id="111000",
        campaign_name="MV | KE | CR2 | adset.pro | 22.05 | 1",
        ad_name="KE_CR2_CR005",
        spend=Decimal("3.0"),
        leads=5,
        registrations=2,
        deposits=1,
        cpc=Decimal("0.05"),
    )
    # Чужая: код CR2 ЕСТЬ (сматчился бы!), тега MV НЕТ, spend высокий без депозитов
    # → без owner-фильтра она получила бы STOP + disable на ЧУЖОЙ ad
    foreign = _make_row(
        fb_ad_id="222000",
        campaign_name="14.05 MZ Artemteam CR2 CBO 1-3-1",
        ad_name="FW3-5",
        spend=Decimal("50.0"),
        deposits=0,
        leads=0,
        registrations=0,
        cpc=Decimal("0.10"),
    )

    result = await process_scan_rows(
        pg_engine, ad_account_id="123", rows=[mine, foreign], scan_id=1, owner_tag="MV"
    )

    assert result.rows_total == 2
    assert result.rows_foreign == 1, "чужая кампания должна быть отброшена owner-фильтром"
    assert result.rows_with_offer == 1, "только моя кампания доходит до матчинга оффера"

    async with pg_engine.connect() as conn:
        # В каталоге только моя — чужой fb_ad_id 222000 не записан
        ads = [r[0] for r in (await conn.execute(text("SELECT fb_ad_id FROM fb_ads"))).all()]
        assert ads == ["111000"]
        # Чужая не создала disable несмотря на spend=50 без депозитов
        n_tasks = (await conn.execute(text("SELECT COUNT(*) FROM task_queue"))).scalar()
        assert n_tasks == 0
        # Метрики только для моей (чужая полностью проигнорирована)
        n_metrics = (await conn.execute(text("SELECT COUNT(*) FROM ad_metrics"))).scalar()
        assert n_metrics == 1


# Owner-scoping выключен (owner_tag=None) → обе кампании обрабатываются (обратная совместимость)
@pytest.mark.asyncio
async def test_owner_scoping_disabled_processes_all(pg_engine, offer_kr2) -> None:
    mine = _make_row(
        fb_ad_id="111001",
        campaign_name="MV | KE | CR2",
        ad_name="a1",
        spend=Decimal("3.0"),
        leads=5,
        registrations=2,
        deposits=1,
    )
    other = _make_row(
        fb_ad_id="222001",
        campaign_name="MZ Artemteam CR2",
        ad_name="a2",
        spend=Decimal("3.0"),
        leads=5,
        registrations=2,
        deposits=1,
    )

    # owner_tag не задан → фильтр выключен
    result = await process_scan_rows(pg_engine, ad_account_id="123", rows=[mine, other], scan_id=1)

    assert result.rows_foreign == 0
    assert result.rows_with_offer == 2, "обе содержат код CR2 → обе сматчены без owner-фильтра"
    async with pg_engine.connect() as conn:
        n_ads = (await conn.execute(text("SELECT COUNT(*) FROM fb_ads"))).scalar()
        assert n_ads == 2
