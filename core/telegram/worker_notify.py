# -*- coding: utf-8 -*-
"""Typed business-worker entry point for the durable notification outbox.

Workers provide semantic facts, never Telegram markup or exception text.  The
delivery worker is the only component that renders HTML and talks to Bot API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.telegram.notifications import (
    enqueue_notification,
    enqueue_notification_in_rolling_window,
    enqueue_notification_in_transaction,
)
from core.telegram.schemas import (
    NotificationActionSpec,
    NotificationCardFacts,
    NotificationEventSpec,
    NotificationNavigationTarget,
    NotificationSeverity,
)

logger = logging.getLogger(__name__)

_EVENT_PART_RE = re.compile(r"[^a-z0-9_.-]+")


def _event_type(value: str) -> str:
    normalized = _EVENT_PART_RE.sub("_", value.strip().lower()).strip("_.-")
    return f"worker_{normalized or 'notification'}"[:64]


def _dedupe_key(
    *,
    event_type: str,
    facts: NotificationCardFacts,
    dedupe_key: str | None,
    dedupe_ttl_seconds: int | None,
) -> str:
    if dedupe_key is None:
        material = hashlib.sha256(
            json.dumps(
                facts.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:24]
    else:
        material = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:24]

    if dedupe_key is None and dedupe_ttl_seconds is None:
        return f"worker:{event_type}:{material}:{uuid.uuid4().hex}"[:200]
    if dedupe_ttl_seconds is None:
        return f"worker:{event_type}:{material}"[:200]

    # The rolling window is enforced transactionally in PostgreSQL.  Returning
    # a stable logical key here prevents epoch-boundary duplicates.
    return f"worker:{event_type}:{material}"[:200]


async def _notify(
    engine: AsyncEngine,
    *,
    audience: Literal["owners", "all"],
    event_type: str,
    severity: NotificationSeverity,
    title: str,
    summary: str | None = None,
    lines: Sequence[str] = (),
    risk: str | None = None,
    status: str | None = None,
    open_target: NotificationNavigationTarget | None = None,
    dedupe_key: str | None = None,
    dedupe_ttl_seconds: int | None = None,
    scheduled_at: datetime | None = None,
) -> bool:
    """Persist a deterministic card; warning events aggregate for five minutes."""
    facts = NotificationCardFacts(
        title=title,
        summary=summary,
        lines=list(lines),
        risk=risk,
        status=status,
        open_target=open_target,
    )
    normalized_event_type = _event_type(event_type)
    try:
        if scheduled_at is None and severity == "warning":
            async with engine.connect() as conn:
                scheduled_at = (
                    await conn.execute(text("SELECT NOW() + INTERVAL '5 minutes'"))
                ).scalar_one()
        spec = NotificationEventSpec(
            event_type=normalized_event_type,
            severity=severity,
            audience=audience,
            facts=facts,
            dedupe_key=_dedupe_key(
                event_type=normalized_event_type,
                facts=facts,
                dedupe_key=dedupe_key,
                dedupe_ttl_seconds=dedupe_ttl_seconds,
            ),
            scheduled_at=scheduled_at,
        )
        if dedupe_ttl_seconds is None:
            result = await enqueue_notification(engine, spec)
        else:
            result = await enqueue_notification_in_rolling_window(
                engine,
                spec,
                window_seconds=dedupe_ttl_seconds,
            )
    except Exception:
        logger.exception("worker notification enqueue failed: %s", normalized_event_type)
        return False
    return result.delivery_count > 0 or not result.was_created


async def notify_owners_in_transaction(
    conn: AsyncConnection,
    *,
    event_type: str,
    severity: NotificationSeverity,
    title: str,
    summary: str | None = None,
    lines: Sequence[str] = (),
    risk: str | None = None,
    status: str | None = None,
    open_target: NotificationNavigationTarget | None = None,
    dedupe_key: str | None = None,
    scheduled_at: datetime | None = None,
) -> bool:
    """Persist an owner card inside the caller's domain transaction.

    This hook deliberately has no best-effort exception handling and no rolling
    dedupe mode: a projection failure must abort the domain write. Callers that
    need a recurring operational signal should use
    :func:`notify_recurring_incident_in_transaction`.
    """
    facts = NotificationCardFacts(
        title=title,
        summary=summary,
        lines=list(lines),
        risk=risk,
        status=status,
        open_target=open_target,
    )
    normalized_event_type = _event_type(event_type)
    if scheduled_at is None and severity == "warning":
        scheduled_at = (
            await conn.execute(text("SELECT NOW() + INTERVAL '5 minutes'"))
        ).scalar_one()
    result = await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type=normalized_event_type,
            severity=severity,
            audience="owners",
            facts=facts,
            dedupe_key=_dedupe_key(
                event_type=normalized_event_type,
                facts=facts,
                dedupe_key=dedupe_key,
                dedupe_ttl_seconds=None,
            ),
            scheduled_at=scheduled_at,
        ),
    )
    return result.delivery_count > 0 or not result.was_created


async def notify_recurring_incident_in_transaction(
    conn: AsyncConnection,
    *,
    incident_key: str,
    audience: Literal["owners", "all"],
    event_type: str,
    severity: NotificationSeverity,
    title: str,
    summary: str | None = None,
    lines: Sequence[str] = (),
    risk: str | None = None,
    resource_type: str = "system",
    resource_id: str | None = None,
    correlation_id: uuid.UUID | None = None,
) -> bool:
    """Open or refresh one incident generation in the caller's transaction.

    Every material card revision gets a content-addressed snapshot event.  A
    repeated identical observation reuses that event, which also lets newly
    registered recipients catch up without creating duplicate messages for
    existing recipients.  Errors propagate to the owning transaction.
    """
    key = incident_key.strip()
    if not key or len(key) > 160:
        raise ValueError("incident_key must contain 1..160 characters")
    if severity not in ("warning", "critical"):
        raise ValueError("recurring worker incidents must be warning or critical")
    normalized_resource_type = resource_type.strip()
    normalized_resource_id = (resource_id or key).strip()
    if not normalized_resource_type or len(normalized_resource_type) > 32:
        raise ValueError("resource_type must contain 1..32 characters")
    if not normalized_resource_id or len(normalized_resource_id) > 160:
        raise ValueError("resource_id must contain 1..160 characters")
    normalized_event_type = _event_type(event_type)
    card = NotificationCardFacts(
        title=title,
        summary=summary,
        lines=list(lines),
        risk=risk,
        status="Активен",
    )
    incident_facts = {
        "source": "worker_notify",
        "event_type": normalized_event_type,
        "card": card.model_dump(mode="json", exclude_none=True),
    }

    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )
    active = (
        await conn.execute(
            text(
                """
                SELECT id, generation, correlation_id, status
                FROM incidents
                WHERE incident_key = :key
                  AND status IN ('open','acknowledged','executing')
                FOR UPDATE
                """
            ),
            {"key": key},
        )
    ).first()
    created = active is None
    if active is None:
        active = (
            await conn.execute(
                text(
                    """
                    INSERT INTO incidents
                        (incident_key, generation, resource_type, resource_id,
                         severity, status, title, summary, facts,
                         correlation_id, opened_at)
                    VALUES
                        (CAST(:key AS VARCHAR),
                         COALESCE((SELECT MAX(generation) + 1 FROM incidents
                                   WHERE incident_key = CAST(:key AS VARCHAR)), 1),
                         :resource_type, :resource_id, :severity, 'open',
                         :title, :summary,
                         CAST(:facts AS JSONB), :correlation_id, NOW())
                    RETURNING id, generation, correlation_id, status
                    """
                ),
                {
                    "key": key,
                    "resource_type": normalized_resource_type,
                    "resource_id": normalized_resource_id,
                    "severity": severity,
                    "title": title[:200],
                    "summary": summary[:700] if summary else None,
                    "facts": json.dumps(incident_facts, ensure_ascii=False),
                    "correlation_id": correlation_id or uuid.uuid4(),
                },
            )
        ).one()
    else:
        await conn.execute(
            text(
                """
                UPDATE incidents
                SET resource_type = :resource_type,
                    resource_id = :resource_id,
                    severity = :severity,
                    title = :title,
                    summary = :summary,
                    facts = CAST(:facts AS JSONB),
                    updated_at = NOW()
                WHERE id = :incident_id
                """
            ),
            {
                "incident_id": active.id,
                "resource_type": normalized_resource_type,
                "resource_id": normalized_resource_id,
                "severity": severity,
                "title": title[:200],
                "summary": summary[:700] if summary else None,
                "facts": json.dumps(incident_facts, ensure_ascii=False),
            },
        )

    incident_id = uuid.UUID(str(active.id))
    generation = int(active.generation)
    incident_status = str(active.status)
    snapshot_card = NotificationCardFacts.model_validate(
        {
            **card.model_dump(mode="json", exclude_none=True),
            "open_target": {"kind": "incident", "target_id": str(incident_id)},
            "incident_generation": generation,
            "incident_status": incident_status,
        }
    )
    actions = (
        [
            NotificationActionSpec(
                key="ack",
                label="Принять",
                kind="ack_incident",
                target_type="incident",
                target_id=str(incident_id),
                target_payload={"generation": generation},
                expires_in_seconds=7 * 24 * 3600,
            )
        ]
        if incident_status == "open"
        else []
    )
    snapshot_material = json.dumps(
        {
            "source_event_type": normalized_event_type,
            "facts": snapshot_card.model_dump(mode="json", exclude_none=True),
            "actions": [action.model_dump(mode="json") for action in actions],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    snapshot_hash = hashlib.sha256(snapshot_material.encode("utf-8")).hexdigest()[:24]
    await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type=(normalized_event_type if created else "incident_snapshot_updated"),
            severity=severity,
            audience=audience,
            facts=snapshot_card,
            actions=actions,
            dedupe_key=(f"worker-incident:{incident_id}:{generation}:snapshot:{snapshot_hash}"),
            incident_id=incident_id,
            correlation_id=uuid.UUID(str(active.correlation_id)),
        ),
    )
    return True


async def notify_recurring_incident(
    engine: AsyncEngine,
    *,
    incident_key: str,
    audience: Literal["owners", "all"],
    event_type: str,
    severity: NotificationSeverity,
    title: str,
    summary: str | None = None,
    lines: Sequence[str] = (),
    risk: str | None = None,
    resource_type: str = "system",
    resource_id: str | None = None,
) -> bool:
    """Commit one recurring-incident observation as a standalone transaction."""
    key = incident_key.strip()
    if not key or len(key) > 160:
        raise ValueError("incident_key must contain 1..160 characters")
    if severity not in ("warning", "critical"):
        raise ValueError("recurring worker incidents must be warning or critical")
    if not resource_type.strip() or len(resource_type.strip()) > 32:
        raise ValueError("resource_type must contain 1..32 characters")
    normalized_resource_id = (resource_id or key).strip()
    if not normalized_resource_id or len(normalized_resource_id) > 160:
        raise ValueError("resource_id must contain 1..160 characters")
    try:
        async with engine.begin() as conn:
            return await notify_recurring_incident_in_transaction(
                conn,
                incident_key=incident_key,
                audience=audience,
                event_type=event_type,
                severity=severity,
                title=title,
                summary=summary,
                lines=lines,
                risk=risk,
                resource_type=resource_type,
                resource_id=resource_id,
            )
    except Exception:
        logger.exception("recurring incident enqueue failed: %s", key)
        return False


async def resolve_recurring_incident_in_transaction(
    conn: AsyncConnection,
    *,
    incident_key: str,
    audience: Literal["owners", "all"],
    summary: str = "Проблема больше не наблюдается.",
) -> bool:
    """Resolve one active generation inside the caller's transaction.

    Errors deliberately propagate so a task finalizer cannot commit success
    without its required incident recovery projection.
    """
    key = incident_key.strip()
    if not key or len(key) > 160:
        raise ValueError("incident_key must contain 1..160 characters")
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )
    incident = (
        await conn.execute(
            text(
                """
                UPDATE incidents
                SET status = 'resolved', resolved_at = NOW(), updated_at = NOW()
                WHERE incident_key = :key
                  AND status IN ('open','acknowledged','executing')
                RETURNING id, generation, title, correlation_id
                """
            ),
            {"key": key},
        )
    ).first()
    if incident is None:
        return False

    incident_id = uuid.UUID(str(incident.id))
    generation = int(incident.generation)
    await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type="incident_recovered",
            severity="ok",
            audience=audience,
            facts=NotificationCardFacts(
                title=str(incident.title),
                summary=summary,
                status="Восстановлено",
                incident_generation=generation,
                incident_status="resolved",
            ),
            dedupe_key=f"worker-incident:{incident_id}:{generation}:resolved",
            incident_id=incident_id,
            correlation_id=uuid.UUID(str(incident.correlation_id)),
        ),
    )
    return True


async def resolve_recurring_incident(
    engine: AsyncEngine,
    *,
    incident_key: str,
    audience: Literal["owners", "all"],
    summary: str = "Проблема больше не наблюдается.",
) -> bool:
    """Resolve the active generation and enqueue one edit for its card."""
    try:
        async with engine.begin() as conn:
            return await resolve_recurring_incident_in_transaction(
                conn,
                incident_key=incident_key,
                audience=audience,
                summary=summary,
            )
    except Exception:
        logger.exception("recurring incident resolve failed: %s", incident_key)
        return False


async def notify_owners(
    engine: AsyncEngine,
    *,
    event_type: str,
    severity: NotificationSeverity,
    title: str,
    summary: str | None = None,
    lines: Sequence[str] = (),
    risk: str | None = None,
    status: str | None = None,
    open_target: NotificationNavigationTarget | None = None,
    dedupe_key: str | None = None,
    dedupe_ttl_seconds: int | None = None,
    scheduled_at: datetime | None = None,
) -> bool:
    """Durably enqueue a typed card for active owners."""
    return await _notify(
        engine,
        audience="owners",
        event_type=event_type,
        severity=severity,
        title=title,
        summary=summary,
        lines=lines,
        risk=risk,
        status=status,
        open_target=open_target,
        dedupe_key=dedupe_key,
        dedupe_ttl_seconds=dedupe_ttl_seconds,
        scheduled_at=scheduled_at,
    )


async def notify_recipients(
    engine: AsyncEngine,
    *,
    event_type: str,
    severity: NotificationSeverity,
    title: str,
    summary: str | None = None,
    lines: Sequence[str] = (),
    risk: str | None = None,
    status: str | None = None,
    open_target: NotificationNavigationTarget | None = None,
    dedupe_key: str | None = None,
    dedupe_ttl_seconds: int | None = None,
    scheduled_at: datetime | None = None,
) -> bool:
    """Durably enqueue a typed card for all active DM recipients."""
    return await _notify(
        engine,
        audience="all",
        event_type=event_type,
        severity=severity,
        title=title,
        summary=summary,
        lines=lines,
        risk=risk,
        status=status,
        open_target=open_target,
        dedupe_key=dedupe_key,
        dedupe_ttl_seconds=dedupe_ttl_seconds,
        scheduled_at=scheduled_at,
    )


__all__ = [
    "notify_owners_in_transaction",
    "notify_owners",
    "notify_recipients",
    "notify_recurring_incident",
    "notify_recurring_incident_in_transaction",
    "resolve_recurring_incident",
    "resolve_recurring_incident_in_transaction",
]
