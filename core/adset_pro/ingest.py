# -*- coding: utf-8 -*-
"""Atomic ingest of positive AdSet.pro postbacks into the durable inbox."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.adset_pro.schemas import PostbackEvent
from core.money import validated_currency_code
from core.tasks.queue import create_task

logger = logging.getLogger(__name__)

SOURCE_ADSETPRO = "adsetpro"
SUPPORTED_EVENT_TYPES = frozenset({"registration", "ftd", "redeposit"})
# Срок жизни постбека, а не одного захода: гейт claim'а отбрасывает задачу с
# истёкшим deadline_at навсегда, и 120 секунд означали «переживи деплой или
# умри». 16.08 так умерли 7 конверсий одним пакетом, не дойдя до внешнего
# вызова. Длительность одного захода ограничивает лиз очереди (30 минут).
_TRACKER_DELIVERY_DEADLINE = timedelta(hours=24)
_EVENT_ALIASES = {
    "reg": "registration",
    "registration": "registration",
    "signup": "registration",
    "hold": "registration",
    "cpa_hold": "registration",
    "ftd": "ftd",
    "first_deposit": "ftd",
    "first-deposit": "ftd",
    "accept": "ftd",
    "cpa_accept": "ftd",
    "redeposit": "redeposit",
    "redep": "redeposit",
    "cpa_redep": "redeposit",
}
_PROVIDER_ID_RAW_KEYS = (
    "provider_event_id",
    "event_id",
    "transaction_id",
    "transactionId",
    "txn_id",
    "conversion_id",
    "postback_id",
)


def canonical_event_type(value: str | None) -> str | None:
    """Return a supported domain event type, never a negative status."""
    normalized = (value or "").strip().lower().replace(" ", "_")
    return _EVENT_ALIASES.get(normalized)


def provider_event_id_from_raw(raw: dict[str, Any] | None) -> str | None:
    """Extract a stable provider transaction/event identifier from raw payload."""
    if not raw:
        return None
    for key in _PROVIDER_ID_RAW_KEYS:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value).strip() or None
    return None


@dataclass(slots=True, frozen=True)
class AttributionResult:
    ad_id: uuid.UUID | None
    fb_ad_id: str | None
    status: str


@dataclass(slots=True, frozen=True)
class IngestResult:
    """Result of one atomic inbox + durable-task transaction."""

    inserted: bool
    is_duplicate: bool
    event_id: int | None
    fb_ad_fk: uuid.UUID | None
    attribution_status: str = "unmatched"
    task_id: int | None = None


def _first(raw: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value).strip() or None
    return None


async def resolve_attribution(
    conn: AsyncConnection,
    event: PostbackEvent,
) -> AttributionResult:
    """Resolve attribution only from the canonical Meta ad id/sub8 contract."""
    raw = event.raw or {}
    direct_id = (event.fb_ad_id or _first(raw, "sub8", "ext_sub8") or "").strip()
    if direct_id:
        rows = (
            await conn.execute(
                text("SELECT id, fb_ad_id FROM fb_ads WHERE fb_ad_id = :fid LIMIT 2"),
                {"fid": direct_id},
            )
        ).all()
        if len(rows) == 1:
            return AttributionResult(ad_id=rows[0][0], fb_ad_id=rows[0][1], status="matched_direct")
        if len(rows) > 1:
            return AttributionResult(ad_id=None, fb_ad_id=direct_id, status="ambiguous")
    return AttributionResult(ad_id=None, fb_ad_id=direct_id or None, status="unmatched")


async def ingest_postback(
    engine: AsyncEngine,
    event: PostbackEvent,
    *,
    signature_valid: bool = True,
    record_duplicate: bool = True,
) -> IngestResult:
    """Persist the event and ``tracker_event_process`` task in one transaction.

    Redeposit dedupe is exact by ``source + provider_event_id``. One-shot
    registration/FTD always use ``source + click_id + event_type`` even when a
    provider attaches a different delivery id to a retry.
    Redeposits without a stable provider id are rejected before any write.
    """
    event_type = canonical_event_type(event.event_type)
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ValueError(f"unsupported AdSet.pro event_type: {event.event_type!r}")
    click_id = (event.click_id or "").strip()
    if not click_id:
        raise ValueError("click_id is required for supported AdSet.pro events")

    source = (event.source or SOURCE_ADSETPRO).strip().lower() or SOURCE_ADSETPRO
    provider_event_id = (event.provider_event_id or "").strip() or provider_event_id_from_raw(
        event.raw
    )
    if event_type == "redeposit" and not provider_event_id:
        raise ValueError("redeposit requires provider_event_id")

    occurred_at = event.occurred_at or event.received_at
    currency = validated_currency_code(event.currency)
    if event_type == "redeposit":
        dedupe_key = f"{source}:provider:{provider_event_id}"
    else:
        dedupe_key = f"{source}:click:{click_id}:{event_type}"

    async with engine.begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": dedupe_key},
        )

        if event_type == "redeposit":
            existing = (
                await conn.execute(
                    text(
                        """
                        SELECT id, fb_ad_fk, attribution_status
                        FROM adsetpro_postback_events
                        WHERE source = :source
                          AND COALESCE(
                              provider_event_id,
                              NULLIF(raw_json->>'provider_event_id', ''),
                              NULLIF(raw_json->>'event_id', ''),
                              NULLIF(raw_json->>'transaction_id', ''),
                              NULLIF(raw_json->>'transactionId', ''),
                              NULLIF(raw_json->>'txn_id', ''),
                              NULLIF(raw_json->>'conversion_id', ''),
                              NULLIF(raw_json->>'postback_id', '')
                          ) = :provider_event_id
                          AND is_duplicate = FALSE
                        ORDER BY received_at DESC
                        LIMIT 1
                        """
                    ),
                    {"source": source, "provider_event_id": provider_event_id},
                )
            ).first()
        else:
            existing = (
                await conn.execute(
                    text(
                        """
                        SELECT id, fb_ad_fk, attribution_status
                        FROM adsetpro_postback_events
                        WHERE source = :source
                          AND click_id = :click_id
                          AND event_type = :event_type
                          AND is_duplicate = FALSE
                        ORDER BY received_at DESC
                        LIMIT 1
                        """
                    ),
                    {"source": source, "click_id": click_id, "event_type": event_type},
                )
            ).first()
        if existing is not None:
            if not record_duplicate:
                return IngestResult(
                    inserted=False,
                    is_duplicate=True,
                    event_id=int(existing[0]),
                    fb_ad_fk=existing[1],
                    attribution_status=str(existing[2] or "unmatched"),
                )
            duplicate_raw = _sanitized_raw(event.raw)
            duplicate_row = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO adsetpro_postback_events
                            (received_at, occurred_at, source, provider_event_id, click_id,
                             fb_ad_id, fb_ad_fk, event_type, revenue, currency, raw_json,
                             signature_valid, is_duplicate, attribution_status,
                             processed_at, last_error)
                        VALUES
                            (:received_at, :occurred_at, :source, :provider_event_id, :click_id,
                             :fb_ad_id, :fb_ad_fk, :event_type, :revenue, :currency,
                             CAST(:raw_json AS JSONB), :signature_valid, TRUE,
                             :attribution_status, now(), :last_error)
                        ON CONFLICT ON CONSTRAINT uq_adsetpro_postback_dedup DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "received_at": event.received_at,
                        "occurred_at": occurred_at,
                        "source": source,
                        "provider_event_id": provider_event_id,
                        "click_id": click_id,
                        "fb_ad_id": event.fb_ad_id,
                        "fb_ad_fk": existing[1],
                        "event_type": event_type,
                        "revenue": event.revenue,
                        "currency": currency,
                        "raw_json": _dumps_jsonable(duplicate_raw),
                        "signature_valid": signature_valid,
                        "attribution_status": str(existing[2] or "unmatched"),
                        "last_error": f"duplicate_of:{int(existing[0])}",
                    },
                )
            ).first()
            return IngestResult(
                inserted=False,
                is_duplicate=True,
                event_id=int(duplicate_row[0]) if duplicate_row is not None else int(existing[0]),
                fb_ad_fk=existing[1],
                attribution_status=str(existing[2] or "unmatched"),
            )

        attribution = await resolve_attribution(conn, event)
        raw_json = _sanitized_raw(event.raw)
        inserted = (
            await conn.execute(
                text(
                    """
                    INSERT INTO adsetpro_postback_events
                        (received_at, occurred_at, source, provider_event_id, click_id,
                         fb_ad_id, fb_ad_fk, event_type, revenue, currency, raw_json,
                         signature_valid, is_duplicate, attribution_status)
                    VALUES
                        (:received_at, :occurred_at, :source, :provider_event_id, :click_id,
                         :fb_ad_id, :fb_ad_fk, :event_type, :revenue, :currency,
                         CAST(:raw_json AS JSONB), :signature_valid, FALSE,
                         :attribution_status)
                    RETURNING id, received_at
                    """
                ),
                {
                    "received_at": event.received_at,
                    "occurred_at": occurred_at,
                    "source": source,
                    "provider_event_id": provider_event_id,
                    "click_id": click_id,
                    "fb_ad_id": attribution.fb_ad_id,
                    "fb_ad_fk": attribution.ad_id,
                    "event_type": event_type,
                    "revenue": event.revenue,
                    "currency": currency,
                    "raw_json": _dumps_jsonable(raw_json),
                    "signature_valid": signature_valid,
                    "attribution_status": attribution.status,
                },
            )
        ).one()
        event_id = int(inserted[0])
        event_received_at = inserted[1]
        task_key = f"tracker:event:{source}:{event_id}:{event_received_at.isoformat()}"[:128]
        queue_now_row = (await conn.execute(text("SELECT clock_timestamp()"))).first()
        if queue_now_row is None:
            raise RuntimeError("failed to read PostgreSQL clock for tracker task")
        queue_now = queue_now_row[0]
        task_id = await create_task(
            engine,
            task_type="tracker_event_process",
            idempotency_key=task_key,
            payload={
                "event_id": event_id,
                "received_at": event_received_at.isoformat(),
                "source": source,
                "click_id": click_id,
            },
            requested_by="adsetpro_postback",
            max_attempts=10080,
            lane="background",
            priority=0,
            available_at=queue_now,
            deadline_at=queue_now + _TRACKER_DELIVERY_DEADLINE,
            connection=conn,
        )
        if task_id is None:
            raise RuntimeError("failed to create durable tracker_event_process task")

    return IngestResult(
        inserted=True,
        is_duplicate=False,
        event_id=event_id,
        fb_ad_fk=attribution.ad_id,
        attribution_status=attribution.status,
        task_id=task_id,
    )


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def _dumps_jsonable(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=_json_default, ensure_ascii=False)


def _sanitized_raw(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    for secret_key in ("token", "secret", "postback_secret"):
        raw.pop(secret_key, None)
    return raw


__all__ = [
    "AttributionResult",
    "IngestResult",
    "SOURCE_ADSETPRO",
    "SUPPORTED_EVENT_TYPES",
    "canonical_event_type",
    "ingest_postback",
    "provider_event_id_from_raw",
    "resolve_attribution",
]
