"""PostgreSQL lifecycle tests for the Meta reporting-shadow watchdog."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from apps.health_watchdog.main import (
    SHADOW_INCIDENT_KEY_PREFIX,
    _record_shadow_observation,
)
from core.meta_api.shadow_spend import ShadowSample


async def _cleanup(engine, *, account_id: str) -> None:
    incident_key = f"{SHADOW_INCIDENT_KEY_PREFIX}{account_id}"
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM notification_deliveries
                WHERE event_id IN (
                    SELECT event.id
                    FROM notification_events AS event
                    JOIN incidents AS incident ON incident.id = event.incident_id
                    WHERE incident.incident_key = :incident_key
                )
                """
            ),
            {"incident_key": incident_key},
        )
        await conn.execute(
            text(
                """
                DELETE FROM notification_events
                WHERE incident_id IN (
                    SELECT id FROM incidents WHERE incident_key = :incident_key
                )
                """
            ),
            {"incident_key": incident_key},
        )
        await conn.execute(
            text("DELETE FROM incidents WHERE incident_key = :incident_key"),
            {"incident_key": incident_key},
        )
        await conn.execute(
            text("DELETE FROM meta_shadow_spend_state WHERE account_id = :account_id"),
            {"account_id": account_id},
        )


@pytest.mark.asyncio
async def test_shadow_incident_open_and_exact_recovery_are_one_locked_lifecycle(pg_engine) -> None:
    account_id = f"9{uuid.uuid4().int % 10**14:014d}"
    incident_key = f"{SHADOW_INCIDENT_KEY_PREFIX}{account_id}"
    day_start = datetime(2026, 7, 22, 0, 0, tzinfo=UTC)
    t0 = day_start + timedelta(hours=8)
    await _cleanup(pg_engine, account_id=account_id)
    try:
        first = await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=t0,
                currency="USD",
                billing_minor=1000,
                reported_minor=500,
            ),
            cabinet_day_start=day_start,
        )
        opened = await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=t0 + timedelta(minutes=5),
                currency="USD",
                billing_minor=1030,
                reported_minor=500,
            ),
            cabinet_day_start=day_start,
        )
        assert first.verdict is None
        assert opened.verdict is not None
        assert opened.incident_event_committed is True

        # A process restart loses no evidence.  One catch-up observation arms
        # recovery but cannot resolve the incident by itself.
        candidate = await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=t0 + timedelta(minutes=6),
                currency="USD",
                billing_minor=1030,
                reported_minor=530,
            ),
            cabinet_day_start=day_start,
        )
        assert candidate.verdict is None
        assert candidate.recovery_confirmed is False

        recovered = await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=t0 + timedelta(minutes=7),
                currency="USD",
                billing_minor=1031,
                reported_minor=531,
            ),
            cabinet_day_start=day_start,
        )
        assert recovered.recovery_confirmed is True
        assert recovered.incident_event_committed is True

        async with pg_engine.connect() as conn:
            incident = (
                await conn.execute(
                    text(
                        "SELECT status FROM incidents "
                        "WHERE incident_key = :incident_key ORDER BY generation DESC LIMIT 1"
                    ),
                    {"incident_key": incident_key},
                )
            ).scalar_one()
            state = (
                await conn.execute(
                    text(
                        """
                        SELECT incident_baseline_at, recovery_candidate_at
                        FROM meta_shadow_spend_state
                        WHERE account_id = :account_id
                        """
                    ),
                    {"account_id": account_id},
                )
            ).one()
        assert incident == "resolved"
        assert state.incident_baseline_at is None
        assert state.recovery_candidate_at is None
    finally:
        await _cleanup(pg_engine, account_id=account_id)


@pytest.mark.asyncio
async def test_cabinet_midnight_rebases_active_episode_without_cross_day_verdict(pg_engine) -> None:
    account_id = f"8{uuid.uuid4().int % 10**14:014d}"
    old_day = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
    new_day = old_day + timedelta(days=1)
    await _cleanup(pg_engine, account_id=account_id)
    try:
        await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=old_day + timedelta(hours=23, minutes=50),
                currency="USD",
                billing_minor=1000,
                reported_minor=500,
            ),
            cabinet_day_start=old_day,
        )
        await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=old_day + timedelta(hours=23, minutes=55),
                currency="USD",
                billing_minor=1030,
                reported_minor=500,
            ),
            cabinet_day_start=old_day,
        )

        after_midnight = await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=new_day + timedelta(minutes=1),
                currency="USD",
                billing_minor=1031,
                reported_minor=0,
            ),
            cabinet_day_start=new_day,
        )
        assert after_midnight.verdict is None
        assert after_midnight.recovery_confirmed is False

        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT jsonb_array_length(samples) AS sample_count,
                               cabinet_day_start,
                               incident_baseline_at,
                               incident_baseline_reported_minor
                        FROM meta_shadow_spend_state
                        WHERE account_id = :account_id
                        """
                    ),
                    {"account_id": account_id},
                )
            ).one()
        assert row.sample_count == 1
        assert row.cabinet_day_start == new_day
        assert row.incident_baseline_at == new_day + timedelta(minutes=1)
        assert row.incident_baseline_reported_minor == 0
    finally:
        await _cleanup(pg_engine, account_id=account_id)


@pytest.mark.asyncio
async def test_currency_change_resets_samples_and_closes_old_episode(pg_engine) -> None:
    account_id = f"7{uuid.uuid4().int % 10**14:014d}"
    incident_key = f"{SHADOW_INCIDENT_KEY_PREFIX}{account_id}"
    day_start = datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
    t0 = day_start + timedelta(hours=8)
    await _cleanup(pg_engine, account_id=account_id)
    try:
        await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=t0,
                currency="USD",
                billing_minor=1000,
                reported_minor=500,
            ),
            cabinet_day_start=day_start,
        )
        await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=t0 + timedelta(minutes=5),
                currency="USD",
                billing_minor=1030,
                reported_minor=500,
            ),
            cabinet_day_start=day_start,
        )

        changed = await _record_shadow_observation(
            pg_engine,
            account_id=account_id,
            sample=ShadowSample(
                ts=t0 + timedelta(minutes=6),
                currency="KES",
                billing_minor=2000,
                reported_minor=700,
            ),
            cabinet_day_start=day_start,
        )

        assert changed.currency_reset is True
        assert changed.verdict is None
        async with pg_engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT currency,
                               jsonb_array_length(samples) AS sample_count,
                               samples->0->>'currency' AS sample_currency,
                               incident_baseline_at,
                               recovery_candidate_at
                        FROM meta_shadow_spend_state
                        WHERE account_id = :account_id
                        """
                    ),
                    {"account_id": account_id},
                )
            ).one()
            incident_status = await conn.scalar(
                text(
                    """
                    SELECT status
                    FROM incidents
                    WHERE incident_key = :incident_key
                    ORDER BY generation DESC
                    LIMIT 1
                    """
                ),
                {"incident_key": incident_key},
            )
        assert row.currency == "KES"
        assert row.sample_count == 1
        assert row.sample_currency == "KES"
        assert row.incident_baseline_at is None
        assert row.recovery_candidate_at is None
        assert incident_status == "resolved"
    finally:
        await _cleanup(pg_engine, account_id=account_id)
