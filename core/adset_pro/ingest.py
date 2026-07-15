# -*- coding: utf-8 -*-
"""Atomic ingest of positive AdSet.pro postbacks into the durable inbox."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.adset_pro.schemas import PostbackEvent

logger = logging.getLogger(__name__)

SOURCE_ADSETPRO = "adsetpro"
SUPPORTED_EVENT_TYPES = frozenset({"registration", "ftd", "redeposit"})
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


def canonical_event_type_sql(column: str) -> str:
    """Return immutable SQL CASE mirroring :func:`canonical_event_type`.

    Revision 0035 deliberately keeps raw aliases compatible with an N-1 app
    rollback. Current readers must therefore canonicalize without mutating the
    inbox row, otherwise a second rollback would stop seeing its own `redep` /
    `hold` vocabulary.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", column):
        raise ValueError(f"unsafe SQL column reference: {column!r}")
    cases = " ".join(
        f"WHEN '{alias}' THEN '{canonical}'" for alias, canonical in _EVENT_ALIASES.items()
    )
    return f"CASE lower(replace(trim({column}), ' ', '_')) {cases} ELSE NULL END"


def provider_event_id_from_raw(raw: dict[str, Any] | None) -> str | None:
    """Extract a stable provider transaction/event identifier from raw payload."""
    if not raw:
        return None
    for key in _PROVIDER_ID_RAW_KEYS:
        value = raw.get(key)
        if value not in (None, ""):
            return str(value).strip() or None
    return None


# Backward-compatible private alias used by older callers/tests.
_TXN_ID_RAW_KEYS = _PROVIDER_ID_RAW_KEYS
_txn_id_from_raw = provider_event_id_from_raw


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
    """Resolve sub8 directly, otherwise exact legacy sub4/sub5/sub6/sub7.

    ``ext_sub6`` is the ad-set name/angle in the established URL contract and is
    intentionally never interpreted as a Meta ad id.
    """
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

    account = _first(raw, "sub4", "ext_sub4", "account", "account_id", "ad_account_id")
    campaign = _first(raw, "sub5", "ext_sub5", "campaign", "campaign_name")
    adset = _first(raw, "sub6", "ext_sub6", "adset", "adset_name")
    ad = _first(raw, "sub7", "ext_sub7", "ad", "ad_name")
    if not all((account, campaign, adset, ad)):
        return AttributionResult(ad_id=None, fb_ad_id=direct_id or None, status="unmatched")

    account = account.removeprefix("act_")
    rows = (
        await conn.execute(
            text(
                """
                SELECT a.id, a.fb_ad_id
                FROM fb_ads a
                JOIN fb_adsets s ON s.id = a.adset_id
                JOIN fb_campaigns c ON c.id = s.campaign_id
                WHERE regexp_replace(COALESCE(c.ad_account_id, ''), '^act_', '') = :account
                  AND c.campaign_name = :campaign
                  AND s.adset_name = :adset
                  AND a.ad_name = :ad
                LIMIT 2
                """
            ),
            {"account": account, "campaign": campaign, "adset": adset, "ad": ad},
        )
    ).all()
    if len(rows) == 1:
        return AttributionResult(ad_id=rows[0][0], fb_ad_id=rows[0][1], status="matched_legacy")
    if len(rows) > 1:
        return AttributionResult(ad_id=None, fb_ad_id=direct_id or None, status="ambiguous")
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
                          AND CASE
                              WHEN replace(lower(trim(event_type)), ' ', '_') IN
                                  ('registration', 'reg', 'signup', 'hold', 'cpa_hold')
                                  THEN 'registration'
                              WHEN replace(lower(trim(event_type)), ' ', '_') IN
                                  ('ftd', 'first_deposit', 'first-deposit',
                                   'accept', 'cpa_accept')
                                  THEN 'ftd'
                              WHEN replace(lower(trim(event_type)), ' ', '_') IN
                                  ('redeposit', 'redep', 'cpa_redep')
                                  THEN 'redeposit'
                              ELSE NULL
                          END = :event_type
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
                        "currency": (event.currency or "USD").upper()[:8],
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
                    "currency": (event.currency or "USD").upper()[:8],
                    "raw_json": _dumps_jsonable(raw_json),
                    "signature_valid": signature_valid,
                    "attribution_status": attribution.status,
                },
            )
        ).one()
        event_id = int(inserted[0])
        event_received_at = inserted[1]
        task_key = f"tracker:event:{source}:{event_id}:{event_received_at.isoformat()}"[:128]
        task = (
            await conn.execute(
                text(
                    """
                    INSERT INTO task_queue
                        (task_type, status, idempotency_key, payload, requested_by,
                         attempt_count, max_attempts, created_at, updated_at)
                    VALUES
                        ('tracker_event_process', 'pending', :key, CAST(:payload AS JSONB),
                         'adsetpro_postback', 0, 10080, now(), now())
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "key": task_key,
                    "payload": json.dumps(
                        {
                            "event_id": event_id,
                            "received_at": event_received_at.isoformat(),
                            "source": source,
                            "click_id": click_id,
                        }
                    ),
                },
            )
        ).first()
        if task is None:
            raise RuntimeError("failed to create durable tracker_event_process task")

    return IngestResult(
        inserted=True,
        is_duplicate=False,
        event_id=event_id,
        fb_ad_fk=attribution.ad_id,
        attribution_status=attribution.status,
        task_id=int(task[0]),
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
    "canonical_event_type_sql",
    "ingest_postback",
    "provider_event_id_from_raw",
    "resolve_attribution",
]
