# -*- coding: utf-8 -*-
"""Transactional notification outbox and lease-fenced delivery operations."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.telegram.action_tokens import retire_replaced_action_tokens
from core.telegram.gateway import TelegramFailureKind, TelegramGatewayError
from core.telegram.navigation_tokens import retire_replaced_navigation_tokens
from core.telegram.outbound_authority import (
    credential_fingerprint_bytes,
    hold_telegram_outbound_authority,
    telegram_failure_authority_is_current,
)
from core.telegram.owner_roster import lock_owner_roster
from core.telegram.schemas import NotificationEventSpec
from core.worker_metrics import (
    NOTIFICATION_LATENCY_QUANTILE,
    NOTIFICATION_METRICS_LAST_REFRESH,
    NOTIFICATION_OLDEST_PENDING_AGE,
    NOTIFICATION_TERMINAL_RECENT,
    NOTIFICATION_TERMINAL_ROWS,
)


@dataclass(frozen=True)
class EnqueuedNotification:
    event_id: uuid.UUID
    delivery_count: int
    was_created: bool


@dataclass(frozen=True)
class ClaimedNotificationDelivery:
    delivery_id: int
    bot_generation: int
    lease_token: uuid.UUID
    attempt_count: int
    max_attempts: int
    recipient_id: uuid.UUID
    chat_id: int
    telegram_user_id: int
    recipient_role: str
    event_id: uuid.UUID
    incident_id: uuid.UUID | None
    incident_generation: int | None
    incident_status: str | None
    event: NotificationEventSpec
    slot_message_id: int | None
    event_created_at: datetime | None = None


@dataclass(frozen=True)
class DeliveryFailureDecision:
    state: Literal["retry", "dead", "unknown", "superseded"]
    scheduled_at: datetime | None
    error_code: str
    disable_recipient_delivery: bool = False
    finalized: bool = False


_SEVERITY_RANK = {"ok": 0, "warning": 1, "critical": 2, "unknown": 2}
_QUIET_HOURS_BYPASS_EVENTS = {
    "action_cancelled",
    "action_confirmed",
    "action_executing",
    "action_failed",
    "action_unknown",
    "incident_acknowledged",
    "incident_recovered",
    "incident_snapshot_reissued",
}
_INCIDENT_SLOT_ONLY_EVENTS = {
    "incident_acknowledged",
    "incident_recovered",
}
_TELEGRAM_AUTH_INCIDENT_KEY = "telegram:bot-auth"
_RECIPIENT_DELIVERY_LOCK_NAMESPACE = 0x54474E44  # "TGND"


def _recipient_delivery_lock_key(recipient_id: uuid.UUID) -> int:
    digest = hashlib.sha256(
        b"fb-agent:telegram-recipient-delivery:\x00" + recipient_id.bytes
    ).digest()
    return int.from_bytes(digest[:4], byteorder="big", signed=True)


async def serialize_recipient_delivery_state_in_transaction(
    conn: AsyncConnection,
    recipient_ids: Sequence[uuid.UUID],
) -> None:
    """Serialize enqueue/opt-out decisions for recipients in a stable order.

    The transaction lock is deliberately independent from delivery rows: an
    enqueue that observed enabled preferences must remain visible to a
    concurrent opt-out before either transaction can commit.  Callers that
    also touch incidents acquire the incident row first, then these locks,
    then delivery rows.
    """
    normalized = sorted(
        {uuid.UUID(str(recipient_id)) for recipient_id in recipient_ids},
        key=lambda recipient_id: recipient_id.bytes,
    )
    for recipient_id in normalized:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:namespace, :recipient_key)"),
            {
                "namespace": _RECIPIENT_DELIVERY_LOCK_NAMESPACE,
                "recipient_key": _recipient_delivery_lock_key(recipient_id),
            },
        )


async def _serialize_telegram_auth_incident_in_transaction(
    conn: AsyncConnection,
) -> None:
    """Take the global auth-gate lock before any recipient delivery locks."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _TELEGRAM_AUTH_INCIDENT_KEY},
    )


async def _serialize_incident_rows_in_transaction(
    conn: AsyncConnection,
    incident_ids: Sequence[uuid.UUID],
) -> None:
    """Lock incident rows in UUID order before recipient/delivery state."""
    normalized = sorted(
        {uuid.UUID(str(incident_id)) for incident_id in incident_ids},
        key=lambda incident_id: incident_id.bytes,
    )
    if not normalized:
        return
    await conn.execute(
        text(
            """
            SELECT id
            FROM incidents
            WHERE id = ANY(CAST(:incident_ids AS uuid[]))
            ORDER BY id
            FOR UPDATE
            """
        ),
        {"incident_ids": normalized},
    )


async def open_telegram_auth_incident_in_transaction(
    conn: AsyncConnection,
    *,
    error_code: str,
    credential_fingerprint: str | None,
    source: str,
    extra_recipient_ids: Sequence[uuid.UUID] = (),
) -> bool:
    """Open or refresh the single credential incident with monotonic generations."""
    await _serialize_telegram_auth_incident_in_transaction(conn)
    active = (
        await conn.execute(
            text(
                """
                SELECT id, generation, correlation_id
                FROM incidents
                WHERE incident_key = :key
                  AND status IN ('open','acknowledged','executing')
                FOR UPDATE
                """
            ),
            {"key": _TELEGRAM_AUTH_INCIDENT_KEY},
        )
    ).first()
    facts = json.dumps(
        {
            "error_code": error_code[:64],
            "credential_fingerprint": credential_fingerprint or "unknown",
            "source": source[:64],
        },
        sort_keys=True,
    )
    created = active is None
    if active is not None:
        await conn.execute(
            text(
                """
                UPDATE incidents
                SET facts = CAST(:facts AS JSONB), updated_at = NOW()
                WHERE id = :incident_id
                """
            ),
            {"incident_id": active.id, "facts": facts},
        )
        incident_id = uuid.UUID(str(active.id))
        generation = int(active.generation)
        correlation_id = uuid.UUID(str(active.correlation_id))
    else:
        correlation_id = uuid.uuid4()
        incident = (
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
                     'integration', 'telegram', 'critical', 'open',
                     'Telegram bot authorization failed',
                     'Bot API returned 401; delivery is disabled until credentials rotate',
                     CAST(:facts AS JSONB), :correlation_id, NOW())
                RETURNING id, generation
                """
                ),
                {
                    "key": _TELEGRAM_AUTH_INCIDENT_KEY,
                    "facts": facts,
                    "correlation_id": correlation_id,
                },
            )
        ).one()
        incident_id = uuid.UUID(str(incident.id))
        generation = int(incident.generation)

    # Freeze the audience and take every advisory in one sorted set before any
    # config or recipient-row lock.  Callers may include a revoked/in-flight
    # recipient whose durable row still has to be terminalized.
    await lock_owner_roster(conn)
    recipient_rows = (
        await conn.execute(
            text(
                """
                SELECT id FROM telegram_recipients
                WHERE revoked_at IS NULL AND chat_id > 0
                ORDER BY id
                """
            )
        )
    ).all()
    await serialize_recipient_delivery_state_in_transaction(
        conn,
        [
            *(uuid.UUID(str(row.id)) for row in recipient_rows),
            *(uuid.UUID(str(recipient_id)) for recipient_id in extra_recipient_ids),
        ],
    )
    if not created:
        return False

    await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type="incident_telegram_auth_failed",
            severity="critical",
            audience="all",
            facts={
                "title": "Telegram bot authorization failed",
                "summary": "Bot API rejected the configured credentials",
                "risk": "Telegram delivery is paused until credentials rotate",
                "status": "Active",
                "incident_generation": generation,
                "incident_status": "open",
                "open_target": {"kind": "incident", "target_id": str(incident_id)},
            },
            dedupe_key=f"telegram-auth:{incident_id}:{generation}:open",
            incident_id=incident_id,
            correlation_id=correlation_id,
        ),
    )
    return True


async def resolve_telegram_auth_incident_in_transaction(
    conn: AsyncConnection,
    *,
    credential_fingerprint: str,
) -> bool:
    """Resolve auth failure after ``getMe`` confirmed the active credential."""
    await _serialize_telegram_auth_incident_in_transaction(conn)
    incident = (
        await conn.execute(
            text(
                """
                SELECT id, generation, title, correlation_id
                FROM incidents
                WHERE incident_key = :key
                  AND status IN ('open','acknowledged','executing')
                FOR UPDATE
                """
            ),
            {"key": _TELEGRAM_AUTH_INCIDENT_KEY},
        )
    ).first()
    if incident is None:
        return False
    await conn.execute(
        text(
            """
            UPDATE incidents
            SET status = 'resolved', resolved_at = NOW(), updated_at = NOW(),
                summary = 'Telegram Bot API authentication confirmed; delivery resumed',
                facts = jsonb_set(
                    facts,
                    '{recovered_by_fingerprint}',
                    to_jsonb(CAST(:credential_fingerprint AS text)),
                    TRUE
                )
            WHERE id = :incident_id
            """
        ),
        {
            "incident_id": incident.id,
            "credential_fingerprint": credential_fingerprint,
        },
    )
    incident_id = uuid.UUID(str(incident.id))
    generation = int(incident.generation)
    await enqueue_notification_in_transaction(
        conn,
        NotificationEventSpec(
            event_type="incident_recovered",
            severity="ok",
            audience="all",
            facts={
                "title": str(incident.title),
                "summary": "Telegram authentication confirmed; delivery resumed",
                "status": "Recovered",
                "incident_generation": generation,
                "incident_status": "resolved",
            },
            dedupe_key=f"telegram-auth:{incident_id}:{generation}:resolved",
            incident_id=incident_id,
            correlation_id=uuid.UUID(str(incident.correlation_id)),
        ),
    )
    return True


def notification_category(event_type: str, *, incident_id: uuid.UUID | None = None) -> str:
    if incident_id is not None:
        return "incidents"
    if event_type.startswith("incident_"):
        return "incidents"
    if event_type.startswith("action_"):
        return "actions"
    if "digest" in event_type:
        return "digests"
    if "recommend" in event_type or event_type.startswith("enable_"):
        return "recommendations"
    return "system"


def _preference_threshold(
    categories: object,
    *,
    event_type: str,
    category: str,
    default: str,
) -> str | None:
    values = categories if isinstance(categories, dict) else {}
    configured = values.get(event_type, values.get(category, values.get("*")))
    if configured is False or configured == "off":
        return None
    if configured is True or configured in {None, "inherit"}:
        return default
    if isinstance(configured, str) and configured in _SEVERITY_RANK:
        return configured
    return default


def recipient_delivery_schedule(
    spec: NotificationEventSpec,
    *,
    timezone_name: str,
    min_severity: str,
    categories: object,
    quiet_hours_start: Any,
    quiet_hours_end: Any,
    has_incident_slot: bool,
    has_incident_in_flight: bool = False,
    has_incident_delivery: bool = False,
    now: datetime | None = None,
) -> datetime | None:
    """Apply recipient category/severity/quiet-hours policy deterministically."""
    now = now or datetime.now(timezone.utc)
    base = spec.scheduled_at or now
    if base.tzinfo is None:
        raise ValueError("notification scheduled_at must be timezone-aware")

    category = notification_category(spec.event_type, incident_id=spec.incident_id)
    threshold = _preference_threshold(
        categories,
        event_type=spec.event_type,
        category=category,
        default=min_severity if min_severity in _SEVERITY_RANK else "warning",
    )
    if threshold is None:
        return None

    is_slot_only_lifecycle = spec.event_type in _INCIDENT_SLOT_ONLY_EVENTS
    is_explicit_reissue = spec.event_type == "incident_snapshot_reissued"
    is_action_lifecycle = spec.event_type.startswith("action_")
    if is_slot_only_lifecycle:
        # A non-terminal transition must replace an unseen queued snapshot as
        # well as edit an existing card.  Otherwise one owner's ACK can erase
        # another owner's still-pending CRITICAL without a current replacement.
        # A true recovery may silently cancel an incident that never crossed
        # Telegram's external boundary.
        if spec.incident_id is not None and not has_incident_slot and not has_incident_in_flight:
            if spec.event_type == "incident_recovered" or not has_incident_delivery:
                return None
    elif (
        not is_action_lifecycle
        and not is_explicit_reissue
        and _SEVERITY_RANK[spec.severity] < _SEVERITY_RANK[threshold]
    ):
        return None

    bypass_quiet = spec.severity == "critical" or spec.event_type in _QUIET_HOURS_BYPASS_EVENTS
    if bypass_quiet or quiet_hours_start is None or quiet_hours_end is None:
        return base
    if quiet_hours_start == quiet_hours_end:
        return base

    try:
        recipient_tz = ZoneInfo(timezone_name or "UTC")
    except ZoneInfoNotFoundError:
        recipient_tz = ZoneInfo("UTC")
    local = base.astimezone(recipient_tz)
    local_time = local.timetz().replace(tzinfo=None)
    wraps_midnight = quiet_hours_start > quiet_hours_end
    in_quiet_hours = (
        quiet_hours_start <= local_time < quiet_hours_end
        if not wraps_midnight
        else local_time >= quiet_hours_start or local_time < quiet_hours_end
    )
    if not in_quiet_hours:
        return base

    quiet_end_date = local.date()
    if wraps_midnight and local_time >= quiet_hours_start:
        quiet_end_date += timedelta(days=1)
    local_end = datetime.combine(quiet_end_date, quiet_hours_end, tzinfo=recipient_tz)
    return local_end.astimezone(timezone.utc)


def decide_delivery_failure(
    error: TelegramGatewayError,
    *,
    attempt_count: int,
    max_attempts: int,
    now: datetime | None = None,
) -> DeliveryFailureDecision:
    """Map Bot API failure to persisted policy; never sleeps in worker memory."""
    now = now or datetime.now(timezone.utc)
    code = f"telegram_{error.kind.value}"
    if error.kind is TelegramFailureKind.UNKNOWN:
        return DeliveryFailureDecision("unknown", None, code)
    if error.kind is TelegramFailureKind.FORBIDDEN:
        return DeliveryFailureDecision("dead", None, code, disable_recipient_delivery=True)
    if error.kind is TelegramFailureKind.UNAUTHORIZED:
        # A 401 is a global credential incident. Keep the delivery durable and
        # let the persisted auth gate halt all claims until token rotation.
        return DeliveryFailureDecision("retry", now + timedelta(minutes=5), code)
    if error.kind in {TelegramFailureKind.INVALID_REQUEST, TelegramFailureKind.NOT_FOUND}:
        return DeliveryFailureDecision("dead", None, code)
    if error.kind is TelegramFailureKind.RATE_LIMITED:
        # Telegram's retry_after is an external scheduling contract, not one
        # more generic delivery attempt.  It must win even when the claim that
        # received the 429 reached max_attempts; otherwise a long global flood
        # control window silently turns a healthy delivery into dead letter.
        delay = error.retry_after if error.retry_after is not None else 5.0
        return DeliveryFailureDecision("retry", now + timedelta(seconds=delay), code)
    if attempt_count >= max_attempts:
        return DeliveryFailureDecision("dead", None, "telegram_attempts_exhausted")
    delay = min(5.0 * (2 ** max(0, attempt_count - 1)), 900.0)
    return DeliveryFailureDecision("retry", now + timedelta(seconds=delay), code)


async def _retire_notification_delivery_backlog_in_transaction(
    conn: AsyncConnection,
    *,
    recipient_id: uuid.UUID,
    error_code: str,
    error_detail: str,
) -> None:
    """Retire only notification work that has not crossed Telegram I/O."""
    await conn.execute(
        text(
            """
            UPDATE notification_deliveries
            SET state = 'superseded', completed_at = NOW(),
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                last_error_code = :error_code,
                last_error_detail = :error_detail,
                updated_at = NOW()
            WHERE recipient_id = :recipient_id
              AND (
                    state IN ('pending','retry')
                    OR (state = 'leased' AND external_started_at IS NULL)
              )
            """
        ),
        {
            "recipient_id": recipient_id,
            "error_code": error_code,
            "error_detail": error_detail,
        },
    )


async def _retire_recipient_delivery_backlog_in_transaction(
    conn: AsyncConnection,
    *,
    recipient_id: uuid.UUID,
    chat_id: int,
    error_code: str,
    error_detail: str,
) -> None:
    """Retire undeliverable work without conflating delivery and access ACLs."""
    await _retire_notification_delivery_backlog_in_transaction(
        conn,
        recipient_id=recipient_id,
        error_code=error_code,
        error_detail=error_detail,
    )
    await conn.execute(
        text(
            """
            UPDATE telegram_command_replies
            SET state = 'dead', completed_at = NOW(),
                lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                last_error_code = :error_code,
                last_error_detail = :error_detail,
                updated_at = NOW()
            WHERE chat_id = :chat_id
              AND (
                    state IN ('pending','retry')
                    OR (state = 'leased' AND external_started_at IS NULL)
              )
            """
        ),
        {
            "chat_id": int(chat_id),
            "error_code": error_code,
            "error_detail": error_detail,
        },
    )
    await conn.execute(
        text(
            """
            UPDATE telegram_action_tokens
            SET revoked_at = COALESCE(revoked_at, NOW())
            WHERE recipient_id = :recipient_id
              AND claimed_at IS NULL
              AND consumed_at IS NULL
              AND revoked_at IS NULL
            """
        ),
        {"recipient_id": recipient_id},
    )
    await conn.execute(
        text(
            """
            UPDATE telegram_navigation_tokens
            SET revoked_at = COALESCE(revoked_at, NOW())
            WHERE recipient_id = :recipient_id
              AND consumed_at IS NULL
              AND revoked_at IS NULL
            """
        ),
        {"recipient_id": recipient_id},
    )


async def retire_revoked_recipient_backlog_in_transaction(
    conn: AsyncConnection,
    *,
    recipient_id: uuid.UUID,
    chat_id: int,
) -> None:
    """Terminalize delivery work after an explicit access revocation."""
    await serialize_recipient_delivery_state_in_transaction(conn, [recipient_id])
    await _retire_recipient_delivery_backlog_in_transaction(
        conn,
        recipient_id=recipient_id,
        chat_id=chat_id,
        error_code="recipient_revoked",
        error_detail="Telegram recipient access was revoked",
    )


async def disable_recipient_delivery_in_transaction(
    conn: AsyncConnection,
    *,
    recipient_id: uuid.UUID,
    chat_id: int,
) -> None:
    """Disable an undeliverable DM while preserving panel/TMA authorization."""
    await serialize_recipient_delivery_state_in_transaction(conn, [recipient_id])
    await conn.execute(
        text(
            """
            INSERT INTO telegram_recipient_preferences (recipient_id, is_enabled)
            VALUES (:recipient_id, FALSE)
            ON CONFLICT (recipient_id) DO UPDATE
            SET is_enabled = FALSE, updated_at = NOW()
            """
        ),
        {"recipient_id": recipient_id},
    )
    await _retire_recipient_delivery_backlog_in_transaction(
        conn,
        recipient_id=recipient_id,
        chat_id=chat_id,
        error_code="recipient_delivery_forbidden",
        error_detail="Telegram recipient blocked delivery; operator access is preserved",
    )


async def retire_disabled_recipient_notifications_in_transaction(
    conn: AsyncConnection,
    *,
    recipient_id: uuid.UUID,
) -> None:
    """Retire manual opt-out backlog while preserving post-boundary ambiguity."""
    await serialize_recipient_delivery_state_in_transaction(conn, [recipient_id])
    await _retire_notification_delivery_backlog_in_transaction(
        conn,
        recipient_id=recipient_id,
        error_code="recipient_notifications_disabled",
        error_detail="Telegram notifications were disabled by an operator",
    )


def build_incident_reissue_spec(
    *,
    source_event: NotificationEventSpec,
    source_event_id: uuid.UUID,
    recipient_id: uuid.UUID,
    incident_id: uuid.UUID,
    incident_generation: int | None,
    incident_status: str | None,
) -> NotificationEventSpec:
    """Create one explicit, auditable replacement for an unavailable card.

    The source event id and recipient form the idempotency key. A reissue event
    is never reissued again automatically, preventing an unbounded duplicate
    chain after repeated ambiguous ``sendMessage`` outcomes.
    """
    facts = source_event.facts.model_copy(
        update={
            "incident_generation": incident_generation,
            "incident_status": incident_status,
        }
    )
    return NotificationEventSpec(
        event_type="incident_snapshot_reissued",
        severity=source_event.severity,
        audience="explicit",
        template_version=source_event.template_version,
        facts=facts,
        actions=source_event.actions,
        dedupe_key=f"reissue:{source_event_id}:{recipient_id}",
        incident_id=incident_id,
        correlation_id=source_event.correlation_id,
        explicit_recipient_ids=[recipient_id],
    )


async def enqueue_notification_in_transaction(
    conn: AsyncConnection,
    spec: NotificationEventSpec,
) -> EnqueuedNotification:
    """Insert event and recipient deliveries using the caller's transaction."""
    if spec.audience == "explicit" and not spec.explicit_recipient_ids:
        raise ValueError("explicit audience requires explicit_recipient_ids")
    if spec.audience != "explicit" and spec.explicit_recipient_ids:
        raise ValueError("explicit_recipient_ids require explicit audience")

    correlation_id = spec.correlation_id or uuid.uuid4()
    event_row = (
        await conn.execute(
            text(
                """
                INSERT INTO notification_events
                    (incident_id, event_type, severity, audience, template_version,
                     facts, actions, dedupe_key, correlation_id)
                VALUES
                    (:incident_id, :event_type, :severity, :audience, :template_version,
                     CAST(:facts AS JSONB), CAST(:actions AS JSONB), :dedupe_key,
                     :correlation_id)
                ON CONFLICT (dedupe_key) DO NOTHING
                RETURNING id
                """
            ),
            {
                "incident_id": spec.incident_id,
                "event_type": spec.event_type,
                "severity": spec.severity,
                "audience": spec.audience,
                "template_version": spec.template_version,
                "facts": spec.facts.model_dump_json(exclude_none=True),
                "actions": json.dumps(
                    [action.model_dump(mode="json") for action in spec.actions],
                    ensure_ascii=False,
                ),
                "dedupe_key": spec.dedupe_key,
                "correlation_id": correlation_id,
            },
        )
    ).first()
    was_created = event_row is not None
    if event_row is None:
        event_row = (
            await conn.execute(
                text("SELECT id FROM notification_events WHERE dedupe_key = :dedupe_key"),
                {"dedupe_key": spec.dedupe_key},
            )
        ).first()
    if event_row is None:  # pragma: no cover - unique insert/select invariant
        raise RuntimeError("notification event disappeared after dedupe conflict")
    event_id = uuid.UUID(str(event_row[0]))

    if spec.audience == "explicit":
        audience_filter = "r.id = ANY(CAST(:recipient_ids AS uuid[]))"
        audience_params: dict[str, Any] = {"recipient_ids": list(spec.explicit_recipient_ids)}
    elif spec.audience == "owners":
        audience_filter = "r.role = 'owner'"
        audience_params = {}
    else:
        audience_filter = "TRUE"
        audience_params = {}

    candidate_rows = (
        await conn.execute(
            text(
                f"""
                SELECT r.id
                FROM telegram_recipients r
                WHERE r.revoked_at IS NULL
                  AND r.chat_id > 0
                  AND ({audience_filter})
                ORDER BY r.id
                """
            ),
            audience_params,
        )
    ).all()
    candidate_recipient_ids = [uuid.UUID(str(row.id)) for row in candidate_rows]
    await serialize_recipient_delivery_state_in_transaction(conn, candidate_recipient_ids)

    # Recipient delivery state is serialized before the config lock to preserve
    # the global incident -> recipient advisory -> recipient row -> config order
    # used by ACK/revoke paths.  Holding FOR SHARE until this transaction commits
    # makes generation selection linearizable with token rotation: a committed
    # event can never be left with only a stale-generation delivery.
    bot_generation = await conn.scalar(
        text(
            """
            SELECT webhook_generation
            FROM telegram_config
            WHERE singleton_key = 'default'
              AND is_enabled
              AND bot_token_encrypted <> ''
              AND bot_token_fingerprint IS NOT NULL
              AND webhook_operation = 'configure'
              AND webhook_generation > 0
            FOR SHARE
            """
        )
    )

    # Delivery claims compare against PostgreSQL NOW().  Using the database
    # clock here avoids a host/container clock skew that can briefly hide a
    # just-committed critical delivery from an immediate claim.
    policy_now = (await conn.execute(text("SELECT NOW()"))).scalar_one()
    recipients = []
    if bot_generation is not None:
        recipients = (
            await conn.execute(
                text(
                    f"""
                SELECT r.id, r.chat_id,
                       COALESCE(p.is_enabled, TRUE) AS is_enabled,
                       COALESCE(p.timezone, 'Europe/Kaliningrad') AS timezone,
                       COALESCE(p.min_severity, 'warning') AS min_severity,
                       p.quiet_hours_start, p.quiet_hours_end,
                       COALESCE(p.categories, '{{}}'::jsonb) AS categories,
                       EXISTS (
                           SELECT 1 FROM telegram_message_slots s
                           WHERE s.incident_id = :incident_id
                             AND s.recipient_id = r.id
                       ) AS has_incident_slot
                       , EXISTS (
                           SELECT 1
                           FROM notification_deliveries active_delivery
                           JOIN notification_events active_event
                             ON active_event.id = active_delivery.event_id
                           WHERE active_event.incident_id = :incident_id
                             AND active_delivery.recipient_id = r.id
                             AND active_delivery.state = 'leased'
                             AND active_delivery.external_started_at IS NOT NULL
                       ) AS has_incident_in_flight
                       , EXISTS (
                           SELECT 1
                           FROM notification_deliveries known_delivery
                           JOIN notification_events known_event
                             ON known_event.id = known_delivery.event_id
                           WHERE known_event.incident_id = :incident_id
                             AND known_delivery.recipient_id = r.id
                             AND known_delivery.state IN ('pending','retry','leased')
                       ) AS has_incident_delivery
                FROM telegram_recipients r
                LEFT JOIN telegram_recipient_preferences p ON p.recipient_id = r.id
                WHERE r.revoked_at IS NULL
                  AND r.chat_id > 0
                  AND r.id = ANY(CAST(:candidate_recipient_ids AS uuid[]))
                  AND ({audience_filter})
                """
                ),
                {
                    "incident_id": spec.incident_id,
                    "candidate_recipient_ids": candidate_recipient_ids,
                    **audience_params,
                },
            )
        ).all()
    supersede_recipient_ids = [recipient.id for recipient in recipients]
    delivery_params: list[dict[str, Any]] = []
    for recipient in recipients:
        if not bool(recipient.is_enabled):
            continue
        categories = recipient.categories
        if isinstance(categories, str):
            categories = json.loads(categories)
        scheduled_at = recipient_delivery_schedule(
            spec,
            timezone_name=str(recipient.timezone),
            min_severity=str(recipient.min_severity),
            categories=categories,
            quiet_hours_start=recipient.quiet_hours_start,
            quiet_hours_end=recipient.quiet_hours_end,
            has_incident_slot=bool(recipient.has_incident_slot),
            has_incident_in_flight=bool(recipient.has_incident_in_flight),
            has_incident_delivery=bool(recipient.has_incident_delivery),
            now=policy_now,
        )
        if scheduled_at is None:
            continue
        delivery_params.append(
            {
                "event_id": event_id,
                "recipient_id": recipient.id,
                "bot_generation": int(bot_generation),
                "scheduled_at": scheduled_at,
                "chat_id": int(recipient.chat_id),
            }
        )

    delivery_count = 0
    if delivery_params:
        delivery_result = await conn.execute(
            text(
                """
                INSERT INTO notification_deliveries
                    (event_id, recipient_id, bot_generation, channel, state, scheduled_at,
                     telegram_chat_id)
                SELECT :event_id, item.recipient_id, :bot_generation,
                       'telegram', 'pending',
                       item.scheduled_at, item.chat_id
                FROM UNNEST(
                    CAST(:recipient_ids AS uuid[]),
                    CAST(:scheduled_ats AS timestamptz[]),
                    CAST(:chat_ids AS bigint[])
                ) AS item(recipient_id, scheduled_at, chat_id)
                ON CONFLICT (event_id, recipient_id, channel) DO NOTHING
                RETURNING id
                """
            ),
            {
                "event_id": event_id,
                "bot_generation": int(bot_generation),
                "recipient_ids": [item["recipient_id"] for item in delivery_params],
                "scheduled_ats": [item["scheduled_at"] for item in delivery_params],
                "chat_ids": [item["chat_id"] for item in delivery_params],
            },
        )
        delivery_count = len(delivery_result.all())

    # A newly committed incident snapshot replaces any older delivery that has
    # not crossed the external Telegram boundary yet. This must also run when
    # the new snapshot itself is intentionally suppressed (for example a
    # recovery before the delayed warning created a message slot).
    if was_created and spec.incident_id is not None and supersede_recipient_ids:
        await conn.execute(
            text(
                """
                UPDATE notification_deliveries d
                SET state = 'superseded', completed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error_code = 'newer_incident_snapshot',
                    updated_at = NOW()
                FROM notification_events older
                WHERE older.id = d.event_id
                  AND older.incident_id = :incident_id
                  AND older.id <> :event_id
                  AND d.recipient_id = ANY(CAST(:recipient_ids AS uuid[]))
                  AND (
                      d.state IN ('pending','retry')
                      OR (d.state = 'leased' AND d.external_started_at IS NULL)
                  )
                  AND older.created_at <= (
                      SELECT created_at FROM notification_events WHERE id = :event_id
                  )
                """
            ),
            {
                "incident_id": spec.incident_id,
                "event_id": event_id,
                "recipient_ids": supersede_recipient_ids,
            },
        )
    return EnqueuedNotification(
        event_id=event_id,
        delivery_count=delivery_count,
        was_created=was_created,
    )


async def enqueue_notification(
    engine: AsyncEngine,
    spec: NotificationEventSpec,
) -> EnqueuedNotification:
    async with engine.begin() as conn:
        return await enqueue_notification_in_transaction(conn, spec)


async def enqueue_notification_in_rolling_window(
    engine: AsyncEngine,
    spec: NotificationEventSpec,
    *,
    window_seconds: int,
) -> EnqueuedNotification:
    """Persist at most one event in a rolling PostgreSQL time window.

    A transaction-scoped advisory lock serializes contenders for the logical
    key.  Unlike epoch buckets, a window boundary can never create two events
    one second apart.  The concrete event key gets a nonce only when the
    previous committed event is genuinely older than the rolling window.
    """
    window = int(window_seconds)
    if window < 60:
        raise ValueError("notification dedupe window must be at least 60 seconds")

    # Leave enough room for ':' plus a 32-char UUID nonce.  worker_notify keys
    # are already shorter, but this keeps the public helper correct for every
    # valid NotificationEventSpec.
    raw_key = spec.dedupe_key
    if len(raw_key) <= 167:
        logical_key = raw_key
    else:
        # Preserve identity even when two valid 200-char keys share the same
        # long prefix.  A plain truncation would suppress unrelated events.
        logical_key = f"{raw_key[:126]}:{hashlib.sha256(raw_key.encode()).hexdigest()[:40]}"
    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": logical_key},
        )
        existing = (
            await conn.execute(
                text(
                    """
                    SELECT e.id,
                           (SELECT COUNT(*) FROM notification_deliveries d
                            WHERE d.event_id = e.id) AS delivery_count
                    FROM notification_events e
                    WHERE LEFT(e.dedupe_key, LENGTH(:key)) = :key
                      AND (
                          e.dedupe_key = :key
                          OR SUBSTRING(e.dedupe_key FROM LENGTH(:key) + 1 FOR 1) = ':'
                      )
                      AND e.created_at >= NOW() - make_interval(secs => :window)
                    ORDER BY e.created_at DESC, e.id DESC
                    LIMIT 1
                    """
                ),
                {"key": logical_key, "window": window},
            )
        ).first()
        if existing is not None:
            return EnqueuedNotification(
                event_id=uuid.UUID(str(existing.id)),
                delivery_count=int(existing.delivery_count or 0),
                was_created=False,
            )

        concrete = f"{logical_key}:{uuid.uuid4().hex}"
        return await enqueue_notification_in_transaction(
            conn,
            spec.model_copy(update={"dedupe_key": concrete}),
        )


async def claim_notification_delivery(
    engine: AsyncEngine,
    *,
    worker_id: str,
    gateway_generation: int,
    credential_fingerprint: str,
    lease_seconds: int = 60,
) -> ClaimedNotificationDelivery | None:
    if not worker_id or lease_seconds < 5 or gateway_generation <= 0:
        raise ValueError("worker_id, gateway_generation and lease_seconds>=5 are required")
    credential_digest = credential_fingerprint_bytes(credential_fingerprint)
    lease_token = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                UPDATE notification_deliveries d
                SET state = 'superseded', completed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error_code = 'stale_bot_generation',
                    last_error_detail =
                        'Telegram bot generation is no longer authoritative',
                    updated_at = NOW()
                WHERE d.state IN ('pending','retry')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM telegram_config c
                      WHERE c.singleton_key = 'default'
                        AND c.is_enabled
                        AND c.bot_token_encrypted <> ''
                        AND c.bot_token_fingerprint IS NOT NULL
                        AND c.webhook_operation = 'configure'
                        AND c.webhook_generation = d.bot_generation
                  )
                """
            )
        )
        authority = await conn.scalar(
            text(
                """
                SELECT webhook_generation
                FROM telegram_config
                WHERE singleton_key = 'default'
                  AND is_enabled
                  AND bot_token_encrypted <> ''
                  AND bot_token_fingerprint = :credential_digest
                  AND webhook_operation = 'configure'
                  AND webhook_state = 'configured'
                  AND webhook_applied_generation = webhook_generation
                  AND webhook_generation = :gateway_generation
                FOR SHARE
                """
            ),
            {
                "credential_digest": credential_digest,
                "gateway_generation": int(gateway_generation),
            },
        )
        if authority is None:
            return None
        row = (
            await conn.execute(
                text(
                    """
                    WITH candidate AS (
                        SELECT d.id
                        FROM notification_deliveries d
                        JOIN telegram_recipients r ON r.id = d.recipient_id
                        JOIN notification_events e ON e.id = d.event_id
                        LEFT JOIN incidents current_incident
                          ON current_incident.id = e.incident_id
                        LEFT JOIN telegram_recipient_preferences p
                          ON p.recipient_id = r.id
                        WHERE d.state IN ('pending','retry')
                          AND d.scheduled_at <= NOW()
                          AND d.bot_generation = :gateway_generation
                          AND r.revoked_at IS NULL
                          AND r.chat_id > 0
                          AND COALESCE(p.is_enabled, TRUE)
                          AND (e.audience <> 'owners' OR r.role = 'owner')
                          AND (
                              e.incident_id IS NULL
                              OR (
                                  current_incident.id IS NOT NULL
                                  AND CASE
                                      WHEN e.event_type IN
                                          ('incident_recovered', 'action_confirmed')
                                          THEN current_incident.status = 'resolved'
                                      WHEN e.event_type IN
                                          ('action_failed', 'action_unknown',
                                           'action_cancelled')
                                          THEN current_incident.status = 'failed'
                                      WHEN e.event_type = 'action_executing'
                                          THEN current_incident.status = 'executing'
                                      WHEN e.event_type = 'incident_acknowledged'
                                          THEN current_incident.status = 'acknowledged'
                                      WHEN e.event_type = 'incident_warning_growth'
                                          THEN current_incident.status IN
                                              ('open', 'acknowledged')
                                      WHEN e.event_type IN
                                          ('incident_snapshot_reissued',
                                           'incident_snapshot_updated')
                                          THEN
                                              e.facts->>'incident_generation'
                                                  = current_incident.generation::text
                                              AND e.facts->>'incident_status'
                                                  = current_incident.status
                                      ELSE current_incident.status = 'open'
                                  END
                                  AND NOT EXISTS (
                                      SELECT 1
                                      FROM jsonb_array_elements(e.actions) action
                                      WHERE action->>'kind' = 'ack_incident'
                                        AND (
                                            action->>'target_type' <> 'incident'
                                            OR action->>'target_id'
                                                <> current_incident.id::text
                                            OR CASE
                                                WHEN COALESCE(
                                                    action->'target_payload'
                                                        ->>'generation',
                                                    ''
                                                ) ~ '^[1-9][0-9]*$'
                                                THEN (
                                                    action->'target_payload'
                                                        ->>'generation'
                                                )::bigint
                                                    <> current_incident.generation
                                                ELSE TRUE
                                            END
                                        )
                                  )
                              )
                          )
                          AND NOT EXISTS (
                              SELECT 1
                              FROM incidents auth_incident
                              WHERE auth_incident.incident_key = 'telegram:bot-auth'
                                AND auth_incident.status IN
                                    ('open','acknowledged','executing')
                          )
                          AND (
                              e.incident_id IS NULL
                              OR NOT EXISTS (
                                  SELECT 1
                                  FROM notification_deliveries in_flight
                                  JOIN notification_events in_flight_event
                                    ON in_flight_event.id = in_flight.event_id
                                  WHERE in_flight.recipient_id = d.recipient_id
                                    AND in_flight_event.incident_id = e.incident_id
                                    AND in_flight.state = 'leased'
                              )
                          )
                          AND (
                              e.incident_id IS NULL
                              OR NOT EXISTS (
                                  SELECT 1
                                  FROM notification_deliveries older_delivery
                                  JOIN notification_events older_event
                                    ON older_event.id = older_delivery.event_id
                                  WHERE older_delivery.recipient_id = d.recipient_id
                                    AND older_event.incident_id = e.incident_id
                                    AND older_delivery.state IN ('pending','retry')
                                    AND (
                                        older_event.created_at < e.created_at
                                        OR (
                                            older_event.created_at = e.created_at
                                            AND older_delivery.id < d.id
                                        )
                                    )
                              )
                          )
                        ORDER BY d.scheduled_at, d.id
                        FOR UPDATE OF d, r SKIP LOCKED
                        LIMIT 1
                    ), claimed AS (
                        UPDATE notification_deliveries d
                        SET state = 'leased',
                            lease_owner = :worker_id,
                            lease_token = :lease_token,
                            lease_expires_at = NOW() + make_interval(secs => :lease_seconds),
                            attempt_count = d.attempt_count + 1,
                            external_started_at = NULL,
                            external_operation_kind = NULL,
                            updated_at = NOW()
                        FROM candidate c
                        WHERE d.id = c.id
                        RETURNING d.*
                    )
                    SELECT
                        d.id, d.bot_generation, d.lease_token,
                        d.attempt_count, d.max_attempts,
                        r.id, r.chat_id, r.telegram_user_id, r.role,
                        e.id, e.incident_id, i.generation, i.status,
                        e.event_type, e.severity, e.audience, e.template_version,
                        e.facts, e.actions, e.dedupe_key, e.correlation_id,
                        s.message_id, e.created_at
                    FROM claimed d
                    JOIN notification_events e ON e.id = d.event_id
                    JOIN telegram_recipients r ON r.id = d.recipient_id
                    LEFT JOIN incidents i ON i.id = e.incident_id
                    LEFT JOIN telegram_message_slots s
                      ON s.incident_id = e.incident_id AND s.recipient_id = r.id
                    """
                ),
                {
                    "worker_id": worker_id[:96],
                    "gateway_generation": int(gateway_generation),
                    "lease_token": lease_token,
                    "lease_seconds": int(lease_seconds),
                },
            )
        ).first()
    if row is None:
        return None
    facts = row[17]
    actions = row[18]
    if isinstance(facts, str):
        facts = json.loads(facts)
    if isinstance(actions, str):
        actions = json.loads(actions)
    event = NotificationEventSpec.model_validate(
        {
            "event_type": row[13],
            "severity": row[14],
            "audience": row[15],
            "template_version": row[16],
            "facts": facts or {},
            "actions": actions or [],
            "dedupe_key": row[19],
            "incident_id": row[10],
            "correlation_id": row[20],
        }
    )
    return ClaimedNotificationDelivery(
        delivery_id=int(row[0]),
        bot_generation=int(row[1]),
        lease_token=uuid.UUID(str(row[2])),
        attempt_count=int(row[3]),
        max_attempts=int(row[4]),
        recipient_id=uuid.UUID(str(row[5])),
        chat_id=int(row[6]),
        telegram_user_id=int(row[7]),
        recipient_role=str(row[8]),
        event_id=uuid.UUID(str(row[9])),
        incident_id=uuid.UUID(str(row[10])) if row[10] is not None else None,
        incident_generation=int(row[11]) if row[11] is not None else None,
        incident_status=str(row[12]) if row[12] is not None else None,
        event=event,
        slot_message_id=int(row[21]) if row[21] is not None else None,
        event_created_at=row[22],
    )


def _event_matches_incident_state(
    claim: ClaimedNotificationDelivery,
    *,
    generation: int,
    status: str,
) -> bool:
    """Apply the same generation/state CAS immediately before Telegram I/O."""
    event_type = claim.event.event_type
    if event_type in {"incident_recovered", "action_confirmed"}:
        state_matches = status == "resolved"
    elif event_type in {"action_failed", "action_unknown", "action_cancelled"}:
        state_matches = status == "failed"
    elif event_type == "action_executing":
        state_matches = status == "executing"
    elif event_type == "incident_acknowledged":
        state_matches = status == "acknowledged"
    elif event_type == "incident_warning_growth":
        state_matches = status in {"open", "acknowledged"}
    elif event_type in {"incident_snapshot_reissued", "incident_snapshot_updated"}:
        state_matches = (
            claim.event.facts.incident_generation == generation
            and claim.event.facts.incident_status == status
        )
    else:
        state_matches = status == "open"
    if not state_matches:
        return False

    for action in claim.event.actions:
        if action.kind != "ack_incident":
            continue
        try:
            action_generation = int(action.target_payload.get("generation"))
        except (TypeError, ValueError):
            return False
        if (
            action.target_type != "incident"
            or action.target_id != str(claim.incident_id)
            or action_generation != generation
        ):
            return False
    return True


async def mark_delivery_external_started(
    engine: AsyncEngine,
    *,
    claim: ClaimedNotificationDelivery,
    operation_kind: Literal["send", "edit"],
    gateway_generation: int,
    credential_fingerprint: str,
) -> Literal["ready", "superseded", "lost"]:
    if operation_kind not in {"send", "edit"}:
        raise ValueError("operation_kind must be send or edit")
    credential_digest = credential_fingerprint_bytes(credential_fingerprint)
    async with engine.begin() as conn:
        if claim.incident_id is not None:
            # Incident transitions UPDATE this row before enqueuing their
            # lifecycle event. Holding the same row lock makes exactly one of
            # two outcomes possible: either this external boundary wins and
            # the transition observes an in-flight snapshot, or the transition
            # wins and this stale snapshot is superseded before Telegram I/O.
            incident = (
                await conn.execute(
                    text(
                        """
                        SELECT generation, status
                        FROM incidents
                        WHERE id = :incident_id
                        FOR UPDATE
                        """
                    ),
                    {"incident_id": claim.incident_id},
                )
            ).first()
            is_current = incident is not None and _event_matches_incident_state(
                claim,
                generation=int(incident.generation),
                status=str(incident.status),
            )
            await serialize_recipient_delivery_state_in_transaction(
                conn,
                [claim.recipient_id],
            )
            if not is_current:
                superseded = await conn.execute(
                    text(
                        """
                        UPDATE notification_deliveries
                        SET state = 'superseded', completed_at = NOW(),
                            lease_owner = NULL, lease_token = NULL,
                            lease_expires_at = NULL,
                            last_error_code = 'stale_incident_snapshot',
                            last_error_detail =
                                'Incident changed before Telegram boundary',
                            updated_at = NOW()
                        WHERE id = :delivery_id
                          AND state = 'leased'
                          AND lease_token = :lease_token
                        """
                    ),
                    {
                        "delivery_id": claim.delivery_id,
                        "lease_token": claim.lease_token,
                    },
                )
                if (superseded.rowcount or 0) > 0:
                    return "superseded"
                current_state = await conn.scalar(
                    text("SELECT state FROM notification_deliveries WHERE id = :delivery_id"),
                    {"delivery_id": claim.delivery_id},
                )
                return "superseded" if current_state == "superseded" else "lost"
        else:
            await serialize_recipient_delivery_state_in_transaction(
                conn,
                [claim.recipient_id],
            )

        # Config is deliberately last in the shared incident -> recipient ->
        # config order.  Enqueue holds the same FOR SHARE lock after recipient
        # serialization, so a concurrent rotation cannot form a lock cycle.
        config_authorized = (
            await conn.scalar(
                text(
                    """
                    SELECT 1
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                      AND is_enabled
                      AND bot_token_encrypted <> ''
                      AND bot_token_fingerprint = :credential_digest
                      AND webhook_operation = 'configure'
                      AND webhook_state = 'configured'
                      AND webhook_applied_generation = webhook_generation
                      AND webhook_generation = :gateway_generation
                      AND webhook_generation = :delivery_generation
                    FOR SHARE
                    """
                ),
                {
                    "credential_digest": credential_digest,
                    "gateway_generation": int(gateway_generation),
                    "delivery_generation": claim.bot_generation,
                },
            )
            is not None
        )

        if not config_authorized:
            superseded = await conn.execute(
                text(
                    """
                    UPDATE notification_deliveries
                    SET state = 'superseded', completed_at = NOW(),
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error_code = 'stale_bot_generation',
                        last_error_detail =
                            'Telegram credential changed before external boundary',
                        updated_at = NOW()
                    WHERE id = :delivery_id
                      AND state = 'leased'
                      AND lease_token = :lease_token
                      AND external_started_at IS NULL
                    """
                ),
                {
                    "delivery_id": claim.delivery_id,
                    "lease_token": claim.lease_token,
                },
            )
            if (superseded.rowcount or 0) > 0:
                await conn.execute(
                    text(
                        """
                        UPDATE telegram_action_tokens
                        SET revoked_at = COALESCE(revoked_at, NOW())
                        WHERE delivery_id = :delivery_id
                          AND claimed_at IS NULL
                          AND consumed_at IS NULL
                          AND revoked_at IS NULL
                        """
                    ),
                    {"delivery_id": claim.delivery_id},
                )
                await conn.execute(
                    text(
                        """
                        UPDATE telegram_navigation_tokens
                        SET revoked_at = COALESCE(revoked_at, NOW())
                        WHERE delivery_id = :delivery_id
                          AND consumed_at IS NULL
                          AND revoked_at IS NULL
                        """
                    ),
                    {"delivery_id": claim.delivery_id},
                )
            return "superseded" if (superseded.rowcount or 0) > 0 else "lost"

        result = await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET external_started_at = COALESCE(external_started_at, NOW()),
                    external_operation_kind = :operation_kind,
                    updated_at = NOW()
                WHERE id = :delivery_id
                  AND state = 'leased'
                  AND lease_token = :lease_token
                  AND lease_expires_at > NOW()
                """
            ),
            {
                "delivery_id": claim.delivery_id,
                "lease_token": claim.lease_token,
                "operation_kind": operation_kind,
            },
        )
    return "ready" if (result.rowcount or 0) > 0 else "lost"


async def mark_delivery_sent(
    engine: AsyncEngine,
    *,
    claim: ClaimedNotificationDelivery,
    message_id: int,
    render_hash: bytes,
    active_action_token_ids: Sequence[uuid.UUID] = (),
    active_navigation_token_ids: Sequence[uuid.UUID] = (),
) -> bool:
    if message_id <= 0:
        raise ValueError("message_id must be positive")
    if len(render_hash) != 32:
        raise ValueError("render_hash must be SHA-256")
    async with engine.begin() as conn:
        if claim.incident_id is not None:
            await _serialize_incident_rows_in_transaction(conn, [claim.incident_id])
        await serialize_recipient_delivery_state_in_transaction(
            conn,
            [claim.recipient_id],
        )
        result = await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = 'sent', telegram_chat_id = :chat_id,
                    telegram_message_id = :message_id, sent_at = NOW(),
                    completed_at = NOW(), lease_owner = NULL,
                    lease_token = NULL, lease_expires_at = NULL,
                    last_error_code = NULL, last_error_detail = NULL,
                    updated_at = NOW()
                WHERE id = :delivery_id
                  AND state = 'leased'
                  AND lease_token = :lease_token
                  AND external_started_at IS NOT NULL
                """
            ),
            {
                "delivery_id": claim.delivery_id,
                "lease_token": claim.lease_token,
                "chat_id": claim.chat_id,
                "message_id": int(message_id),
            },
        )
        if (result.rowcount or 0) <= 0:
            return False
        if claim.incident_id is not None:
            await conn.execute(
                text(
                    """
                    INSERT INTO telegram_message_slots
                        (incident_id, recipient_id, last_event_id, chat_id,
                         message_id, incident_generation, state, render_hash)
                    VALUES
                        (:incident_id, :recipient_id, :event_id, :chat_id,
                         :message_id, :generation, :state, :render_hash)
                    ON CONFLICT (incident_id, recipient_id) DO UPDATE
                    SET last_event_id = EXCLUDED.last_event_id,
                        chat_id = EXCLUDED.chat_id,
                        message_id = EXCLUDED.message_id,
                        incident_generation = EXCLUDED.incident_generation,
                        state = EXCLUDED.state,
                        render_hash = EXCLUDED.render_hash,
                        updated_at = NOW()
                    """
                ),
                {
                    "incident_id": claim.incident_id,
                    "recipient_id": claim.recipient_id,
                    "event_id": claim.event_id,
                    "chat_id": claim.chat_id,
                    "message_id": int(message_id),
                    "generation": claim.incident_generation or 1,
                    "state": claim.incident_status or claim.event.event_type,
                    "render_hash": render_hash,
                },
            )
        await retire_replaced_action_tokens(
            conn,
            delivery_id=claim.delivery_id,
            recipient_id=claim.recipient_id,
            active_token_ids=active_action_token_ids,
        )
        await retire_replaced_navigation_tokens(
            conn,
            delivery_id=claim.delivery_id,
            recipient_id=claim.recipient_id,
            active_token_ids=active_navigation_token_ids,
        )
    return True


async def mark_delivery_failure(
    engine: AsyncEngine,
    *,
    claim: ClaimedNotificationDelivery,
    error: TelegramGatewayError,
    credential_fingerprint: str | None = None,
) -> DeliveryFailureDecision:
    detail = (error.description or error.kind.value)[:500]
    async with engine.begin() as conn:
        policy_now = (await conn.execute(text("SELECT NOW()"))).scalar_one()
        decision = decide_delivery_failure(
            error,
            attempt_count=claim.attempt_count,
            max_attempts=claim.max_attempts,
            now=policy_now,
        )
        incident = None
        if claim.incident_id is not None:
            incident = (
                await conn.execute(
                    text(
                        """
                        SELECT generation, status
                        FROM incidents
                        WHERE id = :incident_id
                        FOR UPDATE
                        """
                    ),
                    {"incident_id": claim.incident_id},
                )
            ).first()

        auth_incident_prepared = False
        if error.kind is TelegramFailureKind.UNAUTHORIZED:
            auth_savepoint = await conn.begin_nested()
            await open_telegram_auth_incident_in_transaction(
                conn,
                error_code=decision.error_code,
                credential_fingerprint=credential_fingerprint,
                source="notification_delivery",
                extra_recipient_ids=[claim.recipient_id],
            )
            auth_incident_prepared = await telegram_failure_authority_is_current(
                conn,
                bot_generation=claim.bot_generation,
                credential_fingerprint=credential_fingerprint,
            )
            if auth_incident_prepared:
                await auth_savepoint.commit()
            else:
                await auth_savepoint.rollback()
                decision = DeliveryFailureDecision(
                    state="dead",
                    scheduled_at=None,
                    error_code="stale_bot_generation",
                )
                detail = "Telegram credential changed before 401 persistence"

        # A retryable edit/send failure is known not to have completed.  If the
        # incident advanced while Telegram was answering, retrying this older
        # card would both block and potentially overwrite the newer lifecycle
        # delivery.  Serialize with the incident transition and retire the
        # stale attempt instead.  A 401 remains global credential evidence and
        # must still open the bot-auth gate below.
        if (
            claim.incident_id is not None
            and decision.state == "retry"
            and error.kind is not TelegramFailureKind.UNAUTHORIZED
        ):
            is_current = incident is not None and _event_matches_incident_state(
                claim,
                generation=int(incident.generation),
                status=str(incident.status),
            )
        else:
            is_current = True

        await serialize_recipient_delivery_state_in_transaction(
            conn,
            [claim.recipient_id],
        )
        if not is_current:
            stale_result = await conn.execute(
                text(
                    """
                    UPDATE notification_deliveries
                    SET state = 'superseded', completed_at = NOW(),
                        lease_owner = NULL, lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error_code = 'stale_incident_snapshot_after_failure',
                        last_error_detail = :error_detail,
                        updated_at = NOW()
                    WHERE id = :delivery_id
                      AND state = 'leased'
                      AND lease_token = :lease_token
                    """
                ),
                {
                    "delivery_id": claim.delivery_id,
                    "lease_token": claim.lease_token,
                    "error_detail": detail,
                },
            )
            if (stale_result.rowcount or 0) <= 0:
                return decision
            return DeliveryFailureDecision(
                state="superseded",
                scheduled_at=None,
                error_code="stale_incident_snapshot_after_failure",
                finalized=True,
            )

        delivery_result = await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = CAST(:state AS VARCHAR),
                    scheduled_at = COALESCE(:scheduled_at, scheduled_at),
                    completed_at = CASE
                                        WHEN CAST(:state AS VARCHAR)
                                             IN ('dead','unknown')
                                        THEN NOW() ELSE NULL END,
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error_code = :error_code,
                    last_error_detail = :error_detail,
                    updated_at = NOW()
                WHERE id = :delivery_id
                  AND state = 'leased'
                  AND lease_token = :lease_token
                """
            ),
            {
                "delivery_id": claim.delivery_id,
                "lease_token": claim.lease_token,
                "state": decision.state,
                "scheduled_at": decision.scheduled_at,
                "error_code": decision.error_code,
                "error_detail": detail,
            },
        )
        # A stale worker must not revoke recipients or open auth incidents after
        # its lease was reconciled or handed to another process.
        if (delivery_result.rowcount or 0) <= 0:
            if auth_incident_prepared:
                await conn.rollback()
            return decision
        if (
            decision.state == "unknown"
            and claim.incident_id is not None
            and claim.event.event_type != "incident_snapshot_reissued"
            and error.method in {"sendMessage", "finalizeDelivery"}
        ):
            # The first send may be visible even though its acknowledgement was
            # lost. Do not hide that ambiguity and do not silently retry the same
            # event. Commit one explicitly named replacement lifecycle event in
            # the same transaction as the UNKNOWN terminal state.
            await enqueue_notification_in_transaction(
                conn,
                build_incident_reissue_spec(
                    source_event=claim.event,
                    source_event_id=claim.event_id,
                    recipient_id=claim.recipient_id,
                    incident_id=claim.incident_id,
                    incident_generation=claim.incident_generation,
                    incident_status=claim.incident_status,
                ),
            )
        if decision.disable_recipient_delivery:
            # A Telegram 403 proves only that DM delivery is unavailable.  It
            # must not revoke the independent panel/TMA owner identity.
            await disable_recipient_delivery_in_transaction(
                conn,
                recipient_id=claim.recipient_id,
                chat_id=claim.chat_id,
            )
    return replace(decision, finalized=True)


async def refresh_notification_metrics(engine: AsyncEngine) -> None:
    """Refresh queue age and terminal SLOs over a bounded rolling window."""

    terminal_states = ("sent", "dead", "unknown", "superseded")
    severities = ("ok", "warning", "critical", "unknown")
    async with engine.connect() as conn:
        pending_rows = (
            await conn.execute(
                text(
                    """
                    SELECT e.severity,
                           GREATEST(
                               0,
                               EXTRACT(EPOCH FROM (NOW() - MIN(e.created_at)))
                           ) AS age_seconds
                    FROM notification_deliveries d
                    JOIN notification_events e ON e.id = d.event_id
                    WHERE d.state IN ('pending','retry','leased')
                      AND d.scheduled_at <= NOW()
                    GROUP BY e.severity
                    """
                )
            )
        ).all()
        terminal_rows = (
            await conn.execute(
                text(
                    """
                    SELECT d.state,
                           COUNT(*)::bigint AS total,
                           COUNT(*) FILTER (
                               WHERE d.completed_at >= NOW() - INTERVAL '5 minutes'
                           )::bigint AS recent,
                           percentile_cont(0.50) WITHIN GROUP (
                               ORDER BY EXTRACT(EPOCH FROM (d.completed_at - e.created_at))
                           ) AS p50,
                           percentile_cont(0.95) WITHIN GROUP (
                               ORDER BY EXTRACT(EPOCH FROM (d.completed_at - e.created_at))
                           ) AS p95,
                           percentile_cont(0.99) WITHIN GROUP (
                               ORDER BY EXTRACT(EPOCH FROM (d.completed_at - e.created_at))
                           ) AS p99
                    FROM notification_deliveries d
                    JOIN notification_events e ON e.id = d.event_id
                    WHERE d.state IN ('sent','dead','unknown','superseded')
                      AND d.completed_at IS NOT NULL
                      AND d.completed_at >= NOW() - INTERVAL '7 days'
                    GROUP BY d.state
                    """
                )
            )
        ).all()

    pending = {str(row.severity): max(0.0, float(row.age_seconds)) for row in pending_rows}
    for severity in severities:
        NOTIFICATION_OLDEST_PENDING_AGE.labels(severity=severity).set(pending.get(severity, 0.0))

    terminal = {str(row.state): row for row in terminal_rows}
    for state in terminal_states:
        row = terminal.get(state)
        NOTIFICATION_TERMINAL_ROWS.labels(state=state).set(0 if row is None else int(row.total))
        NOTIFICATION_TERMINAL_RECENT.labels(state=state).set(0 if row is None else int(row.recent))
        for attribute, quantile in (("p50", "0.50"), ("p95", "0.95"), ("p99", "0.99")):
            value = None if row is None else getattr(row, attribute)
            NOTIFICATION_LATENCY_QUANTILE.labels(state=state, quantile=quantile).set(
                float("nan") if value is None else max(0.0, float(value))
            )
    NOTIFICATION_METRICS_LAST_REFRESH.set(datetime.now(timezone.utc).timestamp())


async def refresh_telegram_auth_gate(
    engine: AsyncEngine,
    *,
    credential_fingerprint: str,
    authentication_confirmed: bool = False,
) -> bool:
    """Release the auth gate only after an authenticated probe was confirmed."""
    if not credential_fingerprint or not authentication_confirmed:
        return False
    async with engine.begin() as conn:
        await resolve_telegram_auth_incident_in_transaction(
            conn,
            credential_fingerprint=credential_fingerprint,
        )
        await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = 'retry', scheduled_at = NOW(), completed_at = NULL,
                    last_error_detail = 'Authentication confirmed; retry enabled',
                    updated_at = NOW()
                WHERE state IN ('retry','dead')
                  AND last_error_code = 'telegram_unauthorized'
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE telegram_updates_inbox
                SET state = 'retry', scheduled_at = NOW(), processed_at = NULL,
                    last_error_detail =
                        'Authentication confirmed; retry enabled'
                WHERE state IN ('retry','dead')
                  AND last_error_code = 'telegram_unauthorized'
                """
            )
        )
        await conn.execute(
            text(
                """
                UPDATE telegram_command_replies
                SET state = 'retry', scheduled_at = NOW(), completed_at = NULL,
                    last_error_detail =
                        'Authentication confirmed; retry enabled',
                    updated_at = NOW()
                WHERE state IN ('retry','dead')
                  AND last_error_code = 'telegram_unauthorized'
                """
            )
        )
    return True


async def verify_telegram_authentication(
    engine: AsyncEngine,
    *,
    gateway: Any,
    gateway_generation: int,
) -> bool:
    """Prove only the still-authoritative credential with ``getMe``.

    The shared config lock spans the external call and gate transition.  Token
    deletion/rotation therefore either commits before this probe (zero Bot API
    calls) or waits until the complete probe result is durable.
    """
    async with hold_telegram_outbound_authority(
        engine,
        bot_generation=gateway_generation,
        credential_fingerprint=gateway.credential_fingerprint,
    ) as authorized:
        if not authorized:
            return False
        try:
            await gateway.get_me()
        except TelegramGatewayError as error:
            if error.kind is TelegramFailureKind.UNAUTHORIZED:
                async with engine.begin() as conn:
                    await open_telegram_auth_incident_in_transaction(
                        conn,
                        error_code="telegram_unauthorized",
                        credential_fingerprint=gateway.credential_fingerprint,
                        source="auth_probe",
                    )
            return False
        except Exception:  # a transport failure is not recovery evidence
            return False
        return await refresh_telegram_auth_gate(
            engine,
            credential_fingerprint=gateway.credential_fingerprint,
            authentication_confirmed=True,
        )


async def mark_delivery_superseded(
    engine: AsyncEngine,
    *,
    claim: ClaimedNotificationDelivery,
    reason: str,
    reissue: NotificationEventSpec | None = None,
) -> bool:
    """Terminally replace a failed edit and atomically enqueue its reissue.

    ``reissue`` is committed only when this exact lease wins the supersede CAS.
    This prevents a lost worker from leaving both the original delivery retrying
    and a replacement card pending.
    """
    async with engine.begin() as conn:
        if claim.incident_id is not None:
            await _serialize_incident_rows_in_transaction(conn, [claim.incident_id])
        await serialize_recipient_delivery_state_in_transaction(
            conn,
            [claim.recipient_id],
        )
        result = await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = 'superseded', completed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error_code = 'incident_snapshot_reissued',
                    last_error_detail = :reason,
                    updated_at = NOW()
                WHERE id = :delivery_id
                  AND state = 'leased'
                  AND lease_token = :lease_token
                """
            ),
            {
                "delivery_id": claim.delivery_id,
                "lease_token": claim.lease_token,
                "reason": reason[:500],
            },
        )
        if (result.rowcount or 0) <= 0:
            return False
        if reissue is not None:
            await enqueue_notification_in_transaction(conn, reissue)
    return True


async def reconcile_expired_delivery_leases(engine: AsyncEngine) -> tuple[int, int]:
    """Retry only pre-call crashes; ambiguous post-call crashes become UNKNOWN."""
    async with engine.begin() as conn:
        candidates = (
            await conn.execute(
                text(
                    """
                    SELECT d.id, d.recipient_id, e.incident_id
                    FROM notification_deliveries d
                    JOIN notification_events e ON e.id = d.event_id
                    WHERE d.state = 'leased'
                      AND d.lease_expires_at <= NOW()
                    ORDER BY d.recipient_id, d.id
                    """
                )
            )
        ).all()
        if not candidates:
            return 0, 0
        candidate_delivery_ids = [int(row.id) for row in candidates]
        await _serialize_incident_rows_in_transaction(
            conn,
            [uuid.UUID(str(row.incident_id)) for row in candidates if row.incident_id is not None],
        )
        await serialize_recipient_delivery_state_in_transaction(
            conn,
            [uuid.UUID(str(row.recipient_id)) for row in candidates],
        )
        retry_result = await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = 'retry', scheduled_at = NOW(),
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error_code = 'lease_expired_before_external_call',
                    updated_at = NOW()
                WHERE state = 'leased'
                  AND id = ANY(CAST(:delivery_ids AS bigint[]))
                  AND lease_expires_at <= NOW()
                  AND (
                      external_started_at IS NULL
                      OR external_operation_kind = 'edit'
                  )
                """
            ),
            {"delivery_ids": candidate_delivery_ids},
        )
        unknown_result = await conn.execute(
            text(
                """
                UPDATE notification_deliveries
                SET state = 'unknown', completed_at = NOW(),
                    lease_owner = NULL, lease_token = NULL,
                    lease_expires_at = NULL,
                    last_error_code = 'lease_expired_after_external_call',
                    last_error_detail = 'Delivery result is ambiguous; automatic resend disabled',
                    updated_at = NOW()
                WHERE state = 'leased'
                  AND id = ANY(CAST(:delivery_ids AS bigint[]))
                  AND lease_expires_at <= NOW()
                  AND external_started_at IS NOT NULL
                  AND external_operation_kind = 'send'
                RETURNING id
                """
            ),
            {"delivery_ids": candidate_delivery_ids},
        )
        unknown_delivery_ids = [int(row._mapping["id"]) for row in unknown_result.all()]
        unknown_count = len(unknown_delivery_ids)

        if unknown_delivery_ids:
            sources = (
                await conn.execute(
                    text(
                        """
                        SELECT d.recipient_id, e.id AS event_id, e.incident_id,
                               i.generation, i.status AS incident_status,
                               e.event_type, e.severity, e.audience,
                               e.template_version, e.facts, e.actions,
                               e.dedupe_key, e.correlation_id
                        FROM notification_deliveries d
                        JOIN notification_events e ON e.id = d.event_id
                        LEFT JOIN incidents i ON i.id = e.incident_id
                        WHERE d.id = ANY(CAST(:delivery_ids AS bigint[]))
                        ORDER BY d.id
                        """
                    ),
                    {"delivery_ids": unknown_delivery_ids},
                )
            ).all()
            for source in sources:
                if source.incident_id is None or source.event_type == "incident_snapshot_reissued":
                    continue
                facts = source.facts
                actions = source.actions
                if isinstance(facts, str):
                    facts = json.loads(facts)
                if isinstance(actions, str):
                    actions = json.loads(actions)
                source_event = NotificationEventSpec.model_validate(
                    {
                        "event_type": source.event_type,
                        "severity": source.severity,
                        "audience": source.audience,
                        "template_version": source.template_version,
                        "facts": facts or {},
                        "actions": actions or [],
                        "dedupe_key": source.dedupe_key,
                        "incident_id": source.incident_id,
                        "correlation_id": source.correlation_id,
                    }
                )
                await enqueue_notification_in_transaction(
                    conn,
                    build_incident_reissue_spec(
                        source_event=source_event,
                        source_event_id=uuid.UUID(str(source.event_id)),
                        recipient_id=uuid.UUID(str(source.recipient_id)),
                        incident_id=uuid.UUID(str(source.incident_id)),
                        incident_generation=(
                            int(source.generation) if source.generation is not None else None
                        ),
                        incident_status=(
                            str(source.incident_status)
                            if source.incident_status is not None
                            else None
                        ),
                    ),
                )
    return int(retry_result.rowcount or 0), unknown_count


__all__ = [
    "ClaimedNotificationDelivery",
    "DeliveryFailureDecision",
    "EnqueuedNotification",
    "build_incident_reissue_spec",
    "claim_notification_delivery",
    "decide_delivery_failure",
    "disable_recipient_delivery_in_transaction",
    "enqueue_notification",
    "enqueue_notification_in_rolling_window",
    "enqueue_notification_in_transaction",
    "mark_delivery_external_started",
    "mark_delivery_failure",
    "mark_delivery_sent",
    "mark_delivery_superseded",
    "notification_category",
    "open_telegram_auth_incident_in_transaction",
    "recipient_delivery_schedule",
    "retire_disabled_recipient_notifications_in_transaction",
    "retire_revoked_recipient_backlog_in_transaction",
    "refresh_notification_metrics",
    "reconcile_expired_delivery_leases",
    "refresh_telegram_auth_gate",
    "resolve_telegram_auth_incident_in_transaction",
    "serialize_recipient_delivery_state_in_transaction",
    "verify_telegram_authentication",
]
