# -*- coding: utf-8 -*-
"""Best-effort reconciliation of AdSet.pro facts into the local event inbox.

The live postback path remains authoritative for low latency.  This module is a
bounded repair loop for a five-minute caller: it reads provider conversions for
a safe overlap window, feeds missing positive facts through the same atomic
ingest transaction and records a compact, secret-free audit in ``system_config``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_pro.credentials import create_adsetpro_client, resolve_adsetpro_api_key
from core.adset_pro.ingest import (
    canonical_event_type,
    ingest_postback,
    provider_event_id_from_raw,
)
from core.adset_pro.schemas import ConversionRow, PostbackEvent
from core.config import get_settings
from core.metrics import (
    TRACKER_PROVIDER_RECONCILIATION_DRIFT,
    TRACKER_RECONCILIATION_RUNS,
)

logger = logging.getLogger(__name__)
_AUDIT_KEY = "tracker_provider_reconciliation"
DEFAULT_PROVIDER_LOOKBACK = timedelta(days=2)


@dataclass(slots=True, frozen=True)
class ProviderReconciliationResult:
    """Observable result of one provider-to-local repair pass.

    ``missing`` is the number of unique provider facts absent before repair;
    ``accepted`` is how many of those facts were inserted.  ``duplicates`` also
    includes repeated rows in the provider response.  ``drift_*`` is the
    symmetric difference, so it exposes both provider-only and local-only facts.
    """

    status: str
    checked_at: datetime
    window_start: datetime
    window_end: datetime
    provider_rows: int = 0
    provider_facts: int = 0
    accepted: int = 0
    missing: int = 0
    duplicates: int = 0
    skipped: int = 0
    local_facts: int = 0
    drift_before: int = 0
    drift_after: int = 0
    error: str | None = None

    @property
    def ignored(self) -> int:
        """Compatibility alias for the old audit/result vocabulary."""
        return self.skipped


def _fact_key(row: ConversionRow) -> tuple[str, str, str] | None:
    event_type = canonical_event_type(row.event_type)
    click_id = row.click_id.strip()
    if event_type is None or not click_id:
        return None
    if event_type == "redeposit":
        provider_id = provider_event_id_from_raw(row.raw)
        if not provider_id:
            return None
        return (event_type, click_id, provider_id)
    return (event_type, click_id, "")


async def _local_fact_keys(
    engine: AsyncEngine,
    *,
    window_start: datetime,
    window_end: datetime,
) -> set[tuple[str, str, str]]:
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT event_type, click_id, provider_event_id
                    FROM adsetpro_postback_events
                    WHERE source = 'adsetpro'
                      AND is_duplicate = FALSE
                      AND occurred_at >= :window_start
                      AND occurred_at <= :window_end
                    """
                ),
                {"window_start": window_start, "window_end": window_end},
            )
        ).all()
    facts: set[tuple[str, str, str]] = set()
    for event_type, click_id, provider_id in rows:
        canonical = canonical_event_type(str(event_type))
        if canonical == "redeposit":
            if provider_id:
                facts.add((canonical, str(click_id), str(provider_id)))
        elif canonical:
            facts.add((canonical, str(click_id), ""))
    return facts


async def _write_audit(engine: AsyncEngine, result: ProviderReconciliationResult) -> None:
    payload = asdict(result)
    for field in ("checked_at", "window_start", "window_end"):
        payload[field] = payload[field].isoformat()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO system_config (key, value, description)
                VALUES (:key, CAST(:value AS JSONB), 'AdSet.pro provider reconciliation audit')
                ON CONFLICT (key) DO UPDATE
                SET value = EXCLUDED.value, updated_at = now()
                """
            ),
            {"key": _AUDIT_KEY, "value": json.dumps(payload)},
        )


def _safe_error(exc: BaseException) -> str:
    """Return diagnostic information that cannot contain a token or raw URL."""
    return type(exc).__name__


async def _write_audit_best_effort(
    engine: AsyncEngine,
    result: ProviderReconciliationResult,
) -> None:
    try:
        await _write_audit(engine, result)
    except Exception as exc:  # noqa: BLE001 - observability must not stop repair caller
        logger.warning("provider reconciliation audit write failed: %s", _safe_error(exc))


async def reconcile_provider_events(
    engine: AsyncEngine,
    *,
    now: datetime | None = None,
    lookback: timedelta = DEFAULT_PROVIDER_LOOKBACK,
    client: Any | None = None,
) -> ProviderReconciliationResult:
    """Fetch provider conversions, ingest missing facts and persist drift audit.

    The MCP credential is resolved DB-first. Provider/MCP failure is converted to
    an audit result so the existing tracker worker remains healthy.
    """
    checked_at = now or datetime.now(UTC)
    checked_at = (
        checked_at.replace(tzinfo=UTC) if checked_at.tzinfo is None else checked_at.astimezone(UTC)
    )
    if lookback <= timedelta(0):
        result = ProviderReconciliationResult(
            status="error",
            checked_at=checked_at,
            window_start=checked_at,
            window_end=checked_at,
            error="InvalidLookback",
        )
        await _write_audit_best_effort(engine, result)
        TRACKER_RECONCILIATION_RUNS.labels(outcome="error").inc()
        return result

    requested_start = checked_at - lookback
    window_start = requested_start.replace(hour=0, minute=0, second=0, microsecond=0)
    owns_client = client is None
    try:
        if owns_client:
            settings = get_settings()
            api_key = await resolve_adsetpro_api_key(
                engine,
                fallback=settings.adsetpro_mcp_key.get_secret_value(),
            )
            if not api_key:
                result = ProviderReconciliationResult(
                    status="unconfigured",
                    checked_at=checked_at,
                    window_start=window_start,
                    window_end=checked_at,
                )
                await _write_audit_best_effort(engine, result)
                TRACKER_RECONCILIATION_RUNS.labels(outcome="unconfigured").inc()
                return result
            client = await create_adsetpro_client(engine, api_key=api_key)
            await client.start()

        rows = await client.list_conversions(
            since=window_start.date(),
            until=checked_at.date(),
        )
        provider_facts = {key for row in rows if (key := _fact_key(row)) is not None}
        local_before = await _local_fact_keys(
            engine,
            window_start=window_start,
            window_end=checked_at,
        )
        missing = len(provider_facts - local_before)
        accepted = duplicates = skipped = row_errors = 0
        first_row_error: str | None = None
        for row in rows:
            key = _fact_key(row)
            if key is None:
                skipped += 1
                continue
            event_type = key[0]
            provider_id = key[2] or provider_event_id_from_raw(row.raw)
            try:
                ingest_result = await ingest_postback(
                    engine,
                    PostbackEvent(
                        click_id=row.click_id.strip(),
                        fb_ad_id=row.fb_ad_id,
                        event_type=event_type,
                        revenue=Decimal(row.revenue),
                        currency=row.currency,
                        received_at=checked_at,
                        occurred_at=row.occurred_at or checked_at,
                        provider_event_id=provider_id,
                        raw=row.raw,
                    ),
                    record_duplicate=False,
                )
            except Exception as exc:  # noqa: BLE001 - one malformed row must not stop repair
                row_errors += 1
                first_row_error = first_row_error or _safe_error(exc)
                continue
            if ingest_result.inserted:
                accepted += 1
            else:
                duplicates += 1

        local_after = await _local_fact_keys(
            engine,
            window_start=window_start,
            window_end=checked_at,
        )
        result = ProviderReconciliationResult(
            status="partial" if row_errors else "ok",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            provider_rows=len(rows),
            provider_facts=len(provider_facts),
            accepted=accepted,
            missing=missing,
            duplicates=duplicates,
            skipped=skipped,
            local_facts=len(local_after),
            drift_before=len(provider_facts.symmetric_difference(local_before)),
            drift_after=len(provider_facts.symmetric_difference(local_after)),
            error=(f"{row_errors} row(s): {first_row_error}" if row_errors else None),
        )
        await _write_audit_best_effort(engine, result)
        TRACKER_PROVIDER_RECONCILIATION_DRIFT.set(result.drift_after)
        TRACKER_RECONCILIATION_RUNS.labels(outcome=result.status).inc()
        return result
    except Exception as exc:  # noqa: BLE001 - reconciliation must not kill worker
        safe_error = _safe_error(exc)
        logger.warning("AdSet.pro provider reconciliation failed: %s", safe_error)
        result = ProviderReconciliationResult(
            status="error",
            checked_at=checked_at,
            window_start=window_start,
            window_end=checked_at,
            error=safe_error,
        )
        await _write_audit_best_effort(engine, result)
        TRACKER_RECONCILIATION_RUNS.labels(outcome="error").inc()
        return result
    finally:
        if owns_client and client is not None:
            try:
                await client.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "AdSet.pro reconciliation client close failed: %s",
                    _safe_error(exc),
                )


__all__ = [
    "DEFAULT_PROVIDER_LOOKBACK",
    "ProviderReconciliationResult",
    "reconcile_provider_events",
]
