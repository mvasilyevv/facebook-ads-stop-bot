from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.telegram.alertmanager_ingress import (
    AlertmanagerWebhookPayload,
    persist_alertmanager_payload,
)

pytestmark = pytest.mark.asyncio


def _payload(*, fingerprint: str, status: str, starts_at: datetime) -> AlertmanagerWebhookPayload:
    return AlertmanagerWebhookPayload.model_validate(
        {
            "version": "4",
            "status": status,
            "receiver": "durable-notification-plane",
            "alerts": [
                {
                    "status": status,
                    "labels": {
                        "alertname": "PlatformQaHeartbeatStale",
                        "severity": "critical",
                        "service": "platform-qa",
                    },
                    "annotations": {"summary": "Disposable integration alert"},
                    "startsAt": starts_at.isoformat(),
                    "fingerprint": fingerprint,
                }
            ],
        }
    )


async def test_alertmanager_firing_dedup_and_resolution_are_one_durable_lifecycle(
    pg_engine: AsyncEngine,
) -> None:
    fingerprint = uuid.uuid4().hex
    incident_key = f"alertmanager:{fingerprint}"
    starts_at = datetime.now(timezone.utc).replace(microsecond=0)

    try:
        async with pg_engine.begin() as conn:
            first = await persist_alertmanager_payload(
                conn,
                _payload(fingerprint=fingerprint, status="firing", starts_at=starts_at),
                operator_public_url="https://app.adpulse.su",
            )
        async with pg_engine.begin() as conn:
            duplicate = await persist_alertmanager_payload(
                conn,
                _payload(fingerprint=fingerprint, status="firing", starts_at=starts_at),
                operator_public_url="https://app.adpulse.su",
            )
        async with pg_engine.begin() as conn:
            resolved = await persist_alertmanager_payload(
                conn,
                _payload(fingerprint=fingerprint, status="resolved", starts_at=starts_at),
                operator_public_url="https://app.adpulse.su",
            )

        assert (first.received, first.changed) == (1, 1)
        assert (duplicate.received, duplicate.changed) == (1, 0)
        assert (resolved.received, resolved.changed) == (1, 1)

        async with pg_engine.connect() as conn:
            incident = (
                await conn.execute(
                    text(
                        "SELECT id, status, resolved_at FROM incidents "
                        "WHERE incident_key = :incident_key"
                    ),
                    {"incident_key": incident_key},
                )
            ).one()
            event_types = (
                (
                    await conn.execute(
                        text(
                            "SELECT event_type FROM notification_events "
                            "WHERE incident_id = :incident_id ORDER BY event_type"
                        ),
                        {"incident_id": incident.id},
                    )
                )
                .scalars()
                .all()
            )

        assert incident.status == "resolved"
        assert incident.resolved_at is not None
        assert event_types == ["incident_monitoring", "incident_recovered"]
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM incidents WHERE incident_key = :incident_key"),
                {"incident_key": incident_key},
            )
