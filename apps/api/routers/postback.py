# -*- coding: utf-8 -*-
"""GET/POST AdSet.pro postback endpoint with strict positive-event semantics."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.deps import get_engine
from core.adset_pro.credentials import resolve_adsetpro_postback_secret
from core.adset_pro.ingest import (
    canonical_event_type,
    ingest_postback,
    provider_event_id_from_raw,
)
from core.adset_pro.schemas import PostbackEvent
from core.metrics import ADSETPRO_POSTBACK_EVENTS
from core.money import validated_currency_code
from core.pubsub import CHANNEL_TRACKER_WAKEUP
from core.safe_diagnostics import safe_exception_diagnostic

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/postback", tags=["postback"])

_UNSUPPORTED_STATUSES = frozenset({"decline", "declined", "rejected", "trash", "baddep"})


async def _authorize(
    *,
    provided: str | None,
    engine: AsyncEngine,
) -> None:
    expected = await resolve_adsetpro_postback_secret(engine)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="adsetpro postback endpoint is not configured",
        )
    if not secrets.compare_digest(provided or "", expected):
        raise HTTPException(status_code=401, detail="invalid postback secret")


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _parse_occurred_at(value: Any, *, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(float(value), tz=UTC)
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.fromtimestamp(float(raw), tz=UTC)
            except (ValueError, OverflowError) as exc:
                raise HTTPException(status_code=422, detail="invalid occurred_at") from exc
    else:
        return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_optional_revenue(value: Any) -> Decimal | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        revenue = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid revenue") from exc
    if not revenue.is_finite():
        raise HTTPException(status_code=422, detail="invalid revenue")
    return revenue


def _normalize(
    payload: dict[str, Any], *, received_at: datetime
) -> tuple[PostbackEvent | None, str]:
    raw_type = str(_first(payload, "event_type", "event", "status", "goal", "type") or "")
    event_type = canonical_event_type(raw_type)
    if event_type is None:
        normalized = raw_type.strip().lower()
        reason = "negative_status" if normalized in _UNSUPPORTED_STATUSES else "unsupported_event"
        return None, reason

    click_id = str(_first(payload, "click_id", "clickid", "event_click_id", "subid") or "").strip()
    if not click_id:
        raise HTTPException(status_code=422, detail="click_id is required")
    provider_event_id = _first(payload, "provider_event_id", *_provider_aliases())
    provider_event_id = (
        str(provider_event_id).strip() if provider_event_id not in (None, "") else None
    )
    if event_type == "redeposit" and not provider_event_id:
        return None, "redeposit_without_provider_event_id"

    revenue = _parse_optional_revenue(_first(payload, "revenue", "payout", "amount"))

    sanitized = dict(payload)
    for key in ("token", "secret", "postback_secret"):
        sanitized.pop(key, None)
    direct_ad_id = _first(payload, "fb_ad_id", "sub8", "ext_sub8")
    event = PostbackEvent(
        click_id=click_id,
        fb_ad_id=str(direct_ad_id).strip() if direct_ad_id not in (None, "") else None,
        event_type=event_type,
        revenue=revenue,
        currency=validated_currency_code(_first(payload, "currency")),
        received_at=received_at,
        occurred_at=_parse_occurred_at(
            _first(payload, "occurred_at", "event_time", "created_at", "timestamp"),
            fallback=received_at,
        ),
        provider_event_id=provider_event_id or provider_event_id_from_raw(payload),
        raw=sanitized,
    )
    return event, "accepted"


def _provider_aliases() -> tuple[str, ...]:
    return (
        "event_id",
        "transaction_id",
        "transactionId",
        "txn_id",
        "conversion_id",
        "postback_id",
    )


async def _record(redis: Redis | None, outcome: str) -> None:
    ADSETPRO_POSTBACK_EVENTS.labels(outcome=outcome).inc()
    if redis is None:
        return
    try:
        await redis.incr(f"fb_agent:tracker:{outcome}_events")
    except Exception as exc:
        logger.debug(
            "tracker technical counter unavailable (%s)",
            safe_exception_diagnostic(exc),
        )


async def _handle(
    *,
    payload: dict[str, Any],
    engine: AsyncEngine,
    redis: Redis | None,
    accepted_status: int,
) -> JSONResponse:
    event, reason = _normalize(payload, received_at=datetime.now(UTC))
    if event is None:
        await _record(redis, "unsupported")
        return JSONResponse(
            status_code=200,
            content={"received": True, "status": "ignored", "reason": reason},
        )

    result = await ingest_postback(engine, event, signature_valid=True)
    outcome = "duplicate" if result.is_duplicate else "accepted"
    await _record(redis, outcome)
    if result.inserted and redis is not None:
        try:
            await redis.publish(
                CHANNEL_TRACKER_WAKEUP,
                str(result.task_id or result.event_id or "event"),
            )
        except Exception as exc:
            # Durable DB task remains the source of truth; the one-second DB poll
            # handles Redis outages without losing the event.
            logger.debug(
                "tracker wakeup publish unavailable (%s)",
                safe_exception_diagnostic(exc),
            )
    return JSONResponse(
        status_code=accepted_status,
        content={
            "received": True,
            "status": "duplicate" if result.is_duplicate else "accepted",
            "inserted": result.inserted,
            "is_duplicate": result.is_duplicate,
        },
    )


@router.get("/adsetpro", summary="Receive AdSet.pro GET postback")
async def receive_adsetpro_get(
    request: Request,
    token: str | None = Query(default=None),
    engine: AsyncEngine = Depends(get_engine),
) -> JSONResponse:
    """AdSet.pro-compatible GET endpoint. The raw URL is never logged here."""
    await _authorize(provided=token, engine=engine)
    return await _handle(
        payload=dict(request.query_params),
        engine=engine,
        redis=getattr(request.app.state, "redis", None),
        accepted_status=200,
    )


__all__ = ["receive_adsetpro_get", "router"]
