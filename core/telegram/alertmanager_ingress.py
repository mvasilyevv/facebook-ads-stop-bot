# -*- coding: utf-8 -*-
"""Authenticated Alertmanager ingress into the durable incident/outbox plane.

Alertmanager is an at-least-once sender.  PostgreSQL therefore owns both
correlation and deduplication: the webhook transaction updates an incident and
creates its notification event/deliveries before the API acknowledges it.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from core.telegram.notifications import enqueue_notification_in_transaction
from core.telegram.schemas import (
    NotificationActionSpec,
    NotificationCardFacts,
    NotificationEventSpec,
    NotificationSeverity,
)

_ACTIVE_INCIDENT_STATES = "'open','acknowledged','executing'"
_SPACE_RE = re.compile(r"\s+")


class AlertmanagerAlert(BaseModel):
    """The stable subset of Alertmanager webhook v4 used by the control plane."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    status: Literal["firing", "resolved"]
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(alias="startsAt")
    ends_at: datetime | None = Field(default=None, alias="endsAt")
    generator_url: str = Field(default="", alias="generatorURL", max_length=2000)
    fingerprint: str | None = Field(default=None, max_length=128, pattern=r"^[0-9a-fA-F]+$")

    @field_validator("labels", "annotations")
    @classmethod
    def validate_string_map(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("too many Alertmanager labels or annotations")
        for key, item in value.items():
            if not key or len(key) > 128 or len(item) > 2000:
                raise ValueError("Alertmanager label or annotation exceeds limits")
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Alertmanager timestamps must include a timezone")
        return value


class AlertmanagerWebhookPayload(BaseModel):
    """Bounded Alertmanager webhook envelope."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    version: str = Field(max_length=16)
    status: Literal["firing", "resolved"]
    receiver: str = Field(default="", max_length=128)
    group_key: str = Field(default="", alias="groupKey", max_length=1000)
    alerts: list[AlertmanagerAlert] = Field(min_length=1, max_length=100)


@dataclass(frozen=True)
class NormalizedAlert:
    fingerprint: str
    incident_key: str
    severity: NotificationSeverity
    title: str
    summary: str
    lines: list[str]
    starts_at: datetime
    status: Literal["firing", "resolved"]


@dataclass(frozen=True)
class AlertmanagerPersistResult:
    received: int
    changed: int


def _short_text(value: str, limit: int) -> str:
    collapsed = _SPACE_RE.sub(" ", value).strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _stable_fingerprint(alert: AlertmanagerAlert) -> str:
    if alert.fingerprint:
        return alert.fingerprint.lower()
    canonical = json.dumps(alert.labels, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:32]


def normalize_alert(alert: AlertmanagerAlert) -> NormalizedAlert:
    """Produce short, deterministic and renderer-safe operator facts."""
    fingerprint = _stable_fingerprint(alert)
    alert_name = _short_text(alert.labels.get("alertname", "Monitoring alert"), 120)
    service = _short_text(alert.labels.get("service", "platform"), 60)
    raw_severity = alert.labels.get("severity", "unknown").lower()
    severity: NotificationSeverity = (
        raw_severity if raw_severity in {"ok", "warning", "critical", "unknown"} else "unknown"
    )
    # Своего текста у алерта может не быть: тогда карточка честно говорит, что
    # сработал мониторинг, и называет само правило вместо английской заглушки.
    summary = _short_text(
        alert.annotations.get("summary") or f"Мониторинг сообщил о срабатывании «{alert_name}».",
        280,
    )
    lines: list[str] = [f"Сервис: {service}"]
    for label, caption in (("instance", "Узел"), ("worker", "Воркер"), ("source", "Источник")):
        value = alert.labels.get(label)
        if value:
            lines.append(f"{caption}: {_short_text(value, 120)}")
    return NormalizedAlert(
        fingerprint=fingerprint,
        incident_key=f"alertmanager:{fingerprint}",
        severity=severity,
        title=_short_text(f"Мониторинг: {alert_name} · {service}", 200),
        summary=summary,
        lines=lines[:5],
        starts_at=alert.starts_at.astimezone(timezone.utc),
        status=alert.status,
    )


def _firing_dedupe_key(alert: NormalizedAlert) -> str:
    material = (
        f"{alert.fingerprint}|{alert.starts_at.isoformat()}|{alert.severity}|{alert.status}"
    ).encode()
    return f"alertmanager:firing:{hashlib.sha256(material).hexdigest()}"


async def _persist_firing(
    conn: AsyncConnection,
    alert: NormalizedAlert,
    *,
    operator_public_url: str,
    now: datetime,
) -> bool:
    dedupe_key = _firing_dedupe_key(alert)
    already_seen = (
        await conn.execute(
            text("SELECT 1 FROM notification_events WHERE dedupe_key = :dedupe_key"),
            {"dedupe_key": dedupe_key},
        )
    ).first()
    if already_seen is not None:
        return False

    incident_facts = {
        "source": "alertmanager",
        "fingerprint": alert.fingerprint,
        "starts_at": alert.starts_at.isoformat(),
    }
    correlation_id = uuid.uuid4()
    incident = (
        await conn.execute(
            text(
                f"""
                INSERT INTO incidents
                    (incident_key, generation, resource_type, resource_id,
                     severity, status, title, summary, facts, correlation_id, opened_at)
                VALUES
                    (CAST(:incident_key AS VARCHAR),
                     COALESCE((
                         SELECT MAX(generation) + 1 FROM incidents
                         WHERE incident_key = CAST(:incident_key AS VARCHAR)
                     ), 1),
                     'monitoring_alert', :resource_id, :severity, 'open',
                     :title, :summary, CAST(:facts AS JSONB), :correlation_id, NOW())
                ON CONFLICT (incident_key)
                  WHERE status IN ({_ACTIVE_INCIDENT_STATES})
                DO UPDATE SET severity = EXCLUDED.severity,
                              title = EXCLUDED.title,
                              summary = EXCLUDED.summary,
                              facts = EXCLUDED.facts,
                              updated_at = NOW()
                RETURNING id, generation, correlation_id
                """
            ),
            {
                "incident_key": alert.incident_key,
                "resource_id": alert.fingerprint,
                "severity": alert.severity,
                "title": alert.title,
                "summary": alert.summary,
                "facts": json.dumps(incident_facts, ensure_ascii=False),
                "correlation_id": correlation_id,
            },
        )
    ).one()
    incident_id = uuid.UUID(str(incident.id))
    await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type="incident_monitoring",
            severity=alert.severity,
            audience="owners",
            facts=NotificationCardFacts(
                title=alert.title,
                summary=alert.summary,
                lines=alert.lines,
                risk=(
                    "Платформа работает нештатно: скан и авто-стоп могут не сработать"
                    if alert.severity == "critical"
                    else None
                ),
                status="Активен",
                open_target={"kind": "incident", "target_id": str(incident_id)},
            ),
            actions=[
                NotificationActionSpec(
                    key="ack",
                    label="Принять",
                    kind="ack_incident",
                    target_type="incident",
                    target_id=str(incident_id),
                    target_payload={"generation": int(incident.generation)},
                )
            ],
            dedupe_key=dedupe_key,
            incident_id=incident_id,
            correlation_id=uuid.UUID(str(incident.correlation_id)),
            scheduled_at=(now + timedelta(minutes=5) if alert.severity != "critical" else None),
        ),
    )
    return True


async def _persist_resolved(
    conn: AsyncConnection,
    alert: NormalizedAlert,
) -> bool:
    incident = (
        await conn.execute(
            text(
                f"""
                UPDATE incidents
                SET status = 'resolved', resolved_at = NOW(), updated_at = NOW()
                WHERE incident_key = :incident_key
                  AND status IN ({_ACTIVE_INCIDENT_STATES})
                  AND facts->>'starts_at' = :starts_at
                RETURNING id, generation, title, correlation_id
                """
            ),
            {
                "incident_key": alert.incident_key,
                "starts_at": alert.starts_at.isoformat(),
            },
        )
    ).first()
    if incident is None:
        return False

    incident_id = uuid.UUID(str(incident.id))
    await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type="incident_recovered",
            severity="ok",
            audience="owners",
            facts=NotificationCardFacts(
                title=str(incident.title),
                summary="Мониторинг больше не видит проблему.",
                status="Восстановлено",
            ),
            dedupe_key=(f"alertmanager:resolved:{incident_id}:{int(incident.generation)}"),
            incident_id=incident_id,
            correlation_id=uuid.UUID(str(incident.correlation_id)),
        ),
    )
    return True


async def persist_alertmanager_payload(
    conn: AsyncConnection,
    payload: AlertmanagerWebhookPayload,
    *,
    operator_public_url: str = "",
) -> AlertmanagerPersistResult:
    """Persist every alert under per-fingerprint transaction advisory fencing."""
    changed = 0
    now = datetime.now(timezone.utc)
    for incoming in payload.alerts:
        alert = normalize_alert(incoming)
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:incident_key, 0))"),
            {"incident_key": alert.incident_key},
        )
        if alert.status == "resolved":
            changed += await _persist_resolved(conn, alert)
        else:
            changed += await _persist_firing(
                conn,
                alert,
                operator_public_url=operator_public_url,
                now=now,
            )
    return AlertmanagerPersistResult(received=len(payload.alerts), changed=changed)


__all__ = [
    "AlertmanagerAlert",
    "AlertmanagerPersistResult",
    "AlertmanagerWebhookPayload",
    "NormalizedAlert",
    "normalize_alert",
    "persist_alertmanager_payload",
]
