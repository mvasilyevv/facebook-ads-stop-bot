# -*- coding: utf-8 -*-
"""Transactional incident lifecycle commands.

REST, Telegram, and future operator surfaces must use this module so the
incident transition and its notification event are committed atomically.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.telegram.notifications import enqueue_notification_in_transaction
from core.telegram.schemas import NotificationCardFacts, NotificationEventSpec


class IncidentNotFoundError(RuntimeError):
    """The requested incident does not exist."""


class IncidentGenerationMismatchError(RuntimeError):
    """The capability belongs to an older incident generation."""

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(f"incident generation changed from {expected} to {actual}")
        self.expected = expected
        self.actual = actual


class IncidentNotAcknowledgeableError(RuntimeError):
    """The incident is already in a terminal or executing state."""

    def __init__(self, status: str) -> None:
        super().__init__(f"incident is not acknowledgeable in state {status}")
        self.status = status


@asynccontextmanager
async def _transaction_scope(
    engine: AsyncEngine,
    connection: AsyncConnection | None,
) -> AsyncIterator[AsyncConnection]:
    if connection is not None:
        yield connection
        return
    async with engine.begin() as conn:
        yield conn


@dataclass(frozen=True)
class IncidentAcknowledgement:
    incident_id: uuid.UUID
    generation: int
    acknowledged_at: datetime
    acknowledged_by: str
    correlation_id: uuid.UUID
    was_changed: bool


async def acknowledge_incident(
    engine: AsyncEngine,
    *,
    incident_id: uuid.UUID,
    acknowledged_by: str,
    expected_generation: int | None = None,
    connection: AsyncConnection | None = None,
) -> IncidentAcknowledgement:
    """Acknowledge one incident and enqueue its card edit in one transaction.

    The command is idempotent for an already-acknowledged generation.  An
    optional expected generation lets recipient-bound Telegram capabilities
    fail closed if the incident changed after the button was minted.
    """
    principal = acknowledged_by.strip()
    if not principal:
        raise ValueError("acknowledged_by is required")
    if len(principal) > 128:
        raise ValueError("acknowledged_by exceeds 128 characters")
    if expected_generation is not None and expected_generation <= 0:
        raise ValueError("expected_generation must be positive")

    async with _transaction_scope(engine, connection) as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, generation, status, title, summary, correlation_id,
                           acknowledged_at, acknowledged_by
                    FROM incidents
                    WHERE id = :incident_id
                    FOR UPDATE
                    """
                ),
                {"incident_id": incident_id},
            )
        ).first()
        if row is None:
            raise IncidentNotFoundError(str(incident_id))

        generation = int(row.generation)
        if expected_generation is not None and generation != expected_generation:
            raise IncidentGenerationMismatchError(
                expected=expected_generation,
                actual=generation,
            )
        current_status = str(row.status)
        if current_status not in {"open", "acknowledged"}:
            raise IncidentNotAcknowledgeableError(current_status)

        was_changed = current_status == "open" or row.acknowledged_at is None
        if was_changed:
            updated = (
                await conn.execute(
                    text(
                        """
                        UPDATE incidents
                        SET status = 'acknowledged',
                            acknowledged_at = COALESCE(acknowledged_at, NOW()),
                            acknowledged_by = COALESCE(acknowledged_by, :acknowledged_by),
                            updated_at = NOW()
                        WHERE id = :incident_id
                        RETURNING acknowledged_at, acknowledged_by
                        """
                    ),
                    {
                        "incident_id": incident_id,
                        "acknowledged_by": principal,
                    },
                )
            ).one()
            acknowledged_at = updated.acknowledged_at
            effective_principal = str(updated.acknowledged_by)
        else:
            acknowledged_at = row.acknowledged_at
            effective_principal = str(row.acknowledged_by or principal)

        correlation_id = uuid.UUID(str(row.correlation_id))
        await enqueue_notification_in_transaction(
            conn,
            NotificationEventSpec(
                event_type="incident_acknowledged",
                severity="warning",
                # Every recipient owns an editable incident card.  The owner is
                # the only role allowed to acknowledge it, but read-only
                # recipients must see the same lifecycle transition.
                audience="all",
                facts=NotificationCardFacts(
                    title=str(row.title),
                    summary=str(row.summary) if row.summary else None,
                    status="Принято оператором",
                ),
                dedupe_key=(f"incident:{incident_id}:generation:{generation}:acknowledged"),
                incident_id=incident_id,
                correlation_id=correlation_id,
            ),
        )

    return IncidentAcknowledgement(
        incident_id=incident_id,
        generation=generation,
        acknowledged_at=acknowledged_at,
        acknowledged_by=effective_principal,
        correlation_id=correlation_id,
        was_changed=was_changed,
    )


__all__ = [
    "IncidentAcknowledgement",
    "IncidentGenerationMismatchError",
    "IncidentNotAcknowledgeableError",
    "IncidentNotFoundError",
    "acknowledge_incident",
]
