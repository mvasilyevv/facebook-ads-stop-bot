# -*- coding: utf-8 -*-
"""E2E: observer FSM atomically creates an incident and durable notification.

Telegram transport is intentionally outside the observer transaction. These
tests cover outbox fan-out, scan idempotency and serialized delivery claims.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.observer.pipeline import process_scan_rows
from core.scanner.models import ScannedAdRow
from core.telegram.gateway import telegram_credential_fingerprint
from core.telegram.notifications import claim_notification_delivery as _claim_notification_delivery

pytestmark = pytest.mark.usefixtures(
    "known_test_cabinet_timezones",
    "authoritative_telegram_config",
)

# chat_id тестового recipient'а (личка, не супергруппа)
_RECIPIENT_CHAT_ID = 98765432
_BOT_GENERATION = 4242
_BOT_FINGERPRINT = telegram_credential_fingerprint("integration-telegram-authority-token")


async def claim_notification_delivery(engine, **kwargs):
    return await _claim_notification_delivery(
        engine,
        gateway_generation=_BOT_GENERATION,
        credential_fingerprint=_BOT_FINGERPRINT,
        **kwargs,
    )


@pytest_asyncio.fixture
async def clean_alert_e2e(pg_engine):
    """Чистит observer + notification plane в FK-safe порядке."""

    async def _truncate():
        async with pg_engine.begin() as conn:
            for t in (
                "telegram_action_tokens",
                "telegram_message_slots",
                "notification_deliveries",
                "notification_events",
                "task_queue",
                "incidents",
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

    await _truncate()
    yield
    await _truncate()


@pytest_asyncio.fixture
async def seeded_recipient_e2e(pg_engine, clean_alert_e2e):
    """Сеет одного owner: observer incidents адресованы owners."""
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO telegram_recipients "
                "(id, chat_id, telegram_user_id, role) "
                "VALUES (gen_random_uuid(), :c, :c, 'owner')"
            ),
            {"c": _RECIPIENT_CHAT_ID},
        )


@pytest_asyncio.fixture
async def offer_alert_e2e(pg_engine, clean_alert_e2e, seeded_recipient_e2e):
    """Оффер с CPA=10 для fast-stop."""
    offer_id = uuid.uuid4()
    code = f"ALR{uuid.uuid4().hex[:4].upper()}"
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO offers (id, code, name, is_active) VALUES (:i, :c, :n, TRUE)"),
            {"i": offer_id, "c": code, "n": f"Alert E2E {code}"},
        )
        await conn.execute(
            text(
                "INSERT INTO offer_rules (offer_id, cpa_threshold, currency) "
                "VALUES (:o, :cpa, 'USD')"
            ),
            {"o": offer_id, "cpa": Decimal("10.00")},
        )
    return {"offer_id": offer_id, "code": code}


def _stop_row(*, code: str, fb_ad_id: str) -> ScannedAdRow:
    """ScannedAdRow с метриками для FSM-STOP (spend без deposits)."""
    return ScannedAdRow(
        fb_ad_id=fb_ad_id,
        campaign_id=f"9{fb_ad_id}",
        adset_id=f"8{fb_ad_id}",
        campaign_name=f"{code} | KE | promo",
        adset_name="ADS_E2E",
        ad_name="AD_e2e_alert",
        delivery_status="ACTIVE",
        spend=Decimal("25.00"),
        leads=0,
        registrations=0,
        deposits=0,
        cpc=Decimal("0.10"),
        ctr=Decimal("2.5"),
        impressions=3,  # >= guardrail_min_impressions=3 (иначе guardrail-стоп подавлен)
    )


# E2E: scan commit содержит FSM alert, incident, event и recipient delivery.
@pytest.mark.asyncio
async def test_scan_emits_incident_and_durable_delivery_once(
    pg_engine,
    offer_alert_e2e,
) -> None:
    fb_ad_id = f"230055{uuid.uuid4().int % 1_000_000:06d}"
    row = _stop_row(code=offer_alert_e2e["code"], fb_ad_id=fb_ad_id)

    cycle_result = await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=500)
    assert cycle_result.alerts_stop == 1

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT e.event_type, e.severity, e.audience, e.actions,
                           i.status, d.state, d.telegram_chat_id
                    FROM notification_events e
                    JOIN incidents i ON i.id = e.incident_id
                    JOIN notification_deliveries d ON d.event_id = e.id
                    """
                )
            )
        ).one()
    assert row.event_type == "incident_stop"
    assert row.severity == "critical"
    assert row.audience == "owners"
    assert row.status == "open"
    assert row.state == "pending"
    assert row.telegram_chat_id == _RECIPIENT_CHAT_ID
    assert row.actions[0]["kind"] == "pause_ad"
    assert row.actions[0]["target_id"] == fb_ad_id


# Повторный scan того же STOP не создаёт второй incident/event/delivery/task.
@pytest.mark.asyncio
async def test_two_scans_do_not_duplicate_incident_delivery(
    pg_engine,
    offer_alert_e2e,
) -> None:
    fb_ad_id = f"230056{uuid.uuid4().int % 1_000_000:06d}"
    row = _stop_row(code=offer_alert_e2e["code"], fb_ad_id=fb_ad_id)

    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=600)
    second = await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=601)
    assert second.alerts_stop == 0

    async with pg_engine.connect() as conn:
        counts = {
            table: await conn.scalar(text(f"SELECT COUNT(*) FROM {table}"))
            for table in (
                "alert_events",
                "incidents",
                "notification_events",
                "notification_deliveries",
                "task_queue",
            )
        }
    assert counts == {table: 1 for table in counts}


# Два delivery workers не могут одновременно claim-ить одну карточку.
@pytest.mark.asyncio
async def test_delivery_claim_is_serialized(
    pg_engine,
    offer_alert_e2e,
) -> None:
    fb_ad_id = f"230057{uuid.uuid4().int % 1_000_000:06d}"
    row = _stop_row(code=offer_alert_e2e["code"], fb_ad_id=fb_ad_id)

    await process_scan_rows(pg_engine, ad_account_id="123", rows=[row], scan_id=700)

    first = await claim_notification_delivery(pg_engine, worker_id="delivery-a")
    second = await claim_notification_delivery(pg_engine, worker_id="delivery-b")

    assert first is not None
    assert first.event.event_type == "incident_stop"
    assert first.chat_id == _RECIPIENT_CHAT_ID
    assert first.event.actions[0].kind == "pause_ad"
    assert second is None
    async with pg_engine.connect() as conn:
        state = await conn.scalar(text("SELECT state FROM notification_deliveries"))
    assert state == "leased"
