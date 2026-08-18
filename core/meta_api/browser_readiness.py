"""Durable, short-lived browser readiness used only by queue scheduling."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.client import BROWSER_CONTRACT_VERSION
from core.observer.accounts import resolve_configured_ad_account_ids
from core.tasks.browser_fence import (
    BrowserFenceLeaseLost,
    BrowserOperationBlocked,
    BrowserOperationFence,
)

logger = logging.getLogger(__name__)

BROWSER_READINESS_CHANNEL = "meta_api"
BROWSER_READINESS_DEFAULT_TTL_SECONDS = 6
_BROWSER_MAINTENANCE_LOCK_SQL = text(
    """
    SELECT pg_advisory_xact_lock(
      hashtext('fb-agent'),
      hashtext('browser-maintenance')
    )
    """
)


class BrowserReadinessProbeClient(Protocol):
    async def check_health(
        self,
        *,
        full_probe: bool = False,
        expected_profile_id: str | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class VisionReadinessIdentity:
    config_id: uuid.UUID
    profile_id: str
    config_updated_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserReadinessObservation:
    state: str
    reason_code: str
    observed_contract_version: int | None
    observed_profile_id: str | None
    observed_session_id: str | None
    observed_at: datetime | None = None

    @property
    def ready(self) -> bool:
        return self.state == "ready"


def _safe_probe_reason(probe: dict[str, Any]) -> str:
    """Map arbitrary browser detail to a bounded non-secret diagnostic code."""
    detail = str(probe.get("detail") or "").casefold()
    for marker, code in (
        ("wrong_url", "wrong_url"),
        ("token_not_found", "token_not_found"),
        ("page_closed", "page_closed"),
        ("circuit_open", "circuit_open"),
        ("deadline", "probe_timeout"),
        ("timeout", "probe_timeout"),
    ):
        if marker in detail:
            return code
    return "browser_unhealthy"


def classify_browser_readiness(
    probe: dict[str, Any],
    *,
    expected_profile_id: str,
) -> BrowserReadinessObservation:
    """Classify exact local browser semantics without claiming Graph reachability."""
    raw_contract = probe.get("browser_contract_version")
    observed_contract = (
        raw_contract if isinstance(raw_contract, int) and not isinstance(raw_contract, bool) else 0
    )
    observed_profile = str(probe.get("vision_profile_id") or "").strip() or None
    observed_session = str(probe.get("session_id") or "").strip() or None
    expected_profile = expected_profile_id.strip()
    unavailable_reason = _safe_probe_reason(probe)

    if (
        observed_contract == 0
        and probe.get("healthy") is not True
        and unavailable_reason != "browser_unhealthy"
    ):
        return BrowserReadinessObservation(
            state="unavailable",
            reason_code=unavailable_reason,
            observed_contract_version=None,
            observed_profile_id=observed_profile,
            observed_session_id=observed_session,
        )
    if observed_contract != BROWSER_CONTRACT_VERSION:
        return BrowserReadinessObservation(
            state="incompatible",
            reason_code="browser_contract_incompatible",
            observed_contract_version=observed_contract or None,
            observed_profile_id=observed_profile,
            observed_session_id=observed_session,
        )
    if not expected_profile or observed_profile != expected_profile:
        return BrowserReadinessObservation(
            state="profile_mismatch",
            reason_code="vision_profile_mismatch",
            observed_contract_version=observed_contract,
            observed_profile_id=observed_profile,
            observed_session_id=observed_session,
        )
    if not observed_session:
        return BrowserReadinessObservation(
            state="unavailable",
            reason_code="browser_session_missing",
            observed_contract_version=observed_contract,
            observed_profile_id=observed_profile,
            observed_session_id=None,
        )
    if probe.get("healthy") is not True:
        return BrowserReadinessObservation(
            state="unavailable",
            reason_code=unavailable_reason,
            observed_contract_version=observed_contract,
            observed_profile_id=observed_profile,
            observed_session_id=observed_session,
        )
    return BrowserReadinessObservation(
        state="ready",
        reason_code="ready",
        observed_contract_version=observed_contract,
        observed_profile_id=observed_profile,
        observed_session_id=observed_session,
    )


async def load_vision_readiness_identity(
    engine: AsyncEngine,
) -> VisionReadinessIdentity | None:
    """Load only canonical identity/revision; encrypted credentials are not read."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT id, profile_id, updated_at
                    FROM vision_config
                    WHERE singleton_key = 'default'
                    """
                )
            )
        ).first()
    if row is None:
        return None
    profile_id = str(row.profile_id or "").strip()
    if not profile_id:
        return None
    return VisionReadinessIdentity(
        config_id=row.id,
        profile_id=profile_id,
        config_updated_at=row.updated_at,
    )


async def _database_clock(engine: AsyncEngine) -> datetime:
    async with engine.connect() as conn:
        value = await conn.scalar(text("SELECT clock_timestamp()"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RuntimeError("PostgreSQL returned an invalid readiness timestamp")
    return value


async def invalidate_browser_readiness(
    engine: AsyncEngine,
    *,
    writer_instance: uuid.UUID,
    state: str = "unavailable",
    reason_code: str = "readiness_probe_unavailable",
) -> None:
    """Expire existing scheduling evidence immediately using PostgreSQL time."""
    if state not in {"unavailable", "maintenance"}:
        raise ValueError("browser readiness invalidation state is invalid")
    async with engine.begin() as conn:
        await conn.execute(_BROWSER_MAINTENANCE_LOCK_SQL)
        await conn.execute(
            text(
                """
                UPDATE browser_channel_readiness
                SET state = :state,
                    reason_code = :reason_code,
                    observed_at = clock_timestamp(),
                    readiness_expires_at = NULL,
                    writer_instance = :writer_instance,
                    generation = generation + 1,
                    updated_at = clock_timestamp()
                WHERE channel = :channel
                """
            ),
            {
                "channel": BROWSER_READINESS_CHANNEL,
                "state": state,
                "reason_code": reason_code,
                "writer_instance": writer_instance,
            },
        )


async def persist_browser_readiness(
    engine: AsyncEngine,
    *,
    identity: VisionReadinessIdentity,
    observation: BrowserReadinessObservation,
    writer_instance: uuid.UUID,
    ttl_seconds: int = BROWSER_READINESS_DEFAULT_TTL_SECONDS,
) -> bool:
    """Publish one observation if its config revision is still canonical.

    The maintenance advisory lock serializes this positive write with every
    claim and maintenance acquisition.  A positive row is also conditioned on
    the absence of a live maintenance lease in the same transaction.
    """
    bounded_ttl = int(ttl_seconds)
    if not 2 <= bounded_ttl <= 30:
        raise ValueError("browser readiness TTL must be between 2 and 30 seconds")
    observed_at = observation.observed_at or await _database_clock(engine)
    if observed_at.tzinfo is None:
        raise ValueError("browser readiness observed_at must be timezone-aware")
    async with engine.begin() as conn:
        await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
        await conn.execute(_BROWSER_MAINTENANCE_LOCK_SQL)
        previous_ready = bool(
            await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1
                      FROM browser_channel_readiness AS readiness
                      JOIN vision_config AS config
                        ON config.id = readiness.vision_config_id
                       AND config.updated_at = readiness.vision_config_updated_at
                       AND config.profile_id = readiness.expected_profile_id
                      WHERE readiness.channel = :channel
                        AND readiness.state = 'ready'
                        AND readiness.observed_contract_version = :contract_version
                        AND readiness.observed_profile_id = config.profile_id
                        AND NULLIF(readiness.observed_session_id, '') IS NOT NULL
                        AND readiness.readiness_expires_at > clock_timestamp()
                    )
                    """
                ),
                {
                    "channel": BROWSER_READINESS_CHANNEL,
                    "contract_version": BROWSER_CONTRACT_VERSION,
                },
            )
        )
        maintenance_active = bool(
            await conn.scalar(
                text(
                    """
                    SELECT EXISTS (
                      SELECT 1
                      FROM system_config
                      WHERE key = 'browser_maintenance'
                        AND (value->>'expires_at')::timestamptz
                              > clock_timestamp()
                    )
                    """
                )
            )
        )
        effective = (
            BrowserReadinessObservation(
                state="maintenance",
                reason_code="browser_maintenance_active",
                observed_contract_version=observation.observed_contract_version,
                observed_profile_id=observation.observed_profile_id,
                observed_session_id=observation.observed_session_id,
                observed_at=observed_at,
            )
            if maintenance_active
            else replace(observation, observed_at=observed_at)
        )
        row = (
            await conn.execute(
                text(
                    """
                    WITH observed AS MATERIALIZED (
                      SELECT CAST(:observed_at AS timestamptz) AS observed_at
                    )
                    INSERT INTO browser_channel_readiness (
                      channel,
                      vision_config_id,
                      vision_config_updated_at,
                      expected_profile_id,
                      observed_profile_id,
                      observed_session_id,
                      observed_contract_version,
                      state,
                      reason_code,
                      observed_at,
                      readiness_expires_at,
                      writer_instance,
                      generation,
                      last_ready_at,
                      created_at,
                      updated_at
                    )
                    SELECT
                      :channel,
                      config.id,
                      config.updated_at,
                      config.profile_id,
                      :observed_profile_id,
                      :observed_session_id,
                      :observed_contract_version,
                      CAST(:state AS varchar(24)),
                      :reason_code,
                      observed.observed_at,
                      CASE WHEN CAST(:state AS varchar(24)) = 'ready'
                        THEN observed.observed_at
                          + make_interval(secs => :ttl_seconds)
                        ELSE NULL
                      END,
                      :writer_instance,
                      1,
                      CASE WHEN CAST(:state AS varchar(24)) = 'ready'
                        THEN observed.observed_at
                        ELSE NULL
                      END,
                      observed.observed_at,
                      observed.observed_at
                    FROM vision_config AS config
                    CROSS JOIN observed
                    WHERE config.singleton_key = 'default'
                      AND config.id = :vision_config_id
                      AND config.updated_at = :vision_config_updated_at
                      AND config.profile_id = :expected_profile_id
                      AND (
                        CAST(:state AS varchar(24)) <> 'ready'
                        OR NOT EXISTS (
                          SELECT 1
                          FROM system_config AS browser_gate
                          WHERE browser_gate.key = 'browser_maintenance'
                            AND (
                              browser_gate.value->>'expires_at'
                            )::timestamptz > clock_timestamp()
                        )
                      )
                      AND (
                        CAST(:state AS varchar(24)) <> 'ready'
                        OR observed.observed_at
                             + make_interval(secs => :ttl_seconds)
                           > clock_timestamp()
                      )
                    ON CONFLICT (channel) DO UPDATE
                    SET vision_config_id = EXCLUDED.vision_config_id,
                        vision_config_updated_at =
                          EXCLUDED.vision_config_updated_at,
                        expected_profile_id = EXCLUDED.expected_profile_id,
                        observed_profile_id = EXCLUDED.observed_profile_id,
                        observed_session_id = EXCLUDED.observed_session_id,
                        observed_contract_version =
                          EXCLUDED.observed_contract_version,
                        state = EXCLUDED.state,
                        reason_code = EXCLUDED.reason_code,
                        observed_at = EXCLUDED.observed_at,
                        readiness_expires_at =
                          EXCLUDED.readiness_expires_at,
                        writer_instance = EXCLUDED.writer_instance,
                        generation =
                          browser_channel_readiness.generation + 1,
                        last_ready_at = CASE
                          WHEN EXCLUDED.state = 'ready'
                            THEN EXCLUDED.observed_at
                          ELSE browser_channel_readiness.last_ready_at
                        END,
                        updated_at = EXCLUDED.updated_at
                    WHERE EXCLUDED.observed_at
                            > browser_channel_readiness.observed_at
                    RETURNING state
                    """
                ),
                {
                    "channel": BROWSER_READINESS_CHANNEL,
                    "vision_config_id": identity.config_id,
                    "vision_config_updated_at": identity.config_updated_at,
                    "expected_profile_id": identity.profile_id,
                    "observed_profile_id": effective.observed_profile_id,
                    "observed_session_id": effective.observed_session_id,
                    "observed_contract_version": (effective.observed_contract_version),
                    "state": effective.state,
                    "reason_code": effective.reason_code,
                    "ttl_seconds": bounded_ttl,
                    "writer_instance": writer_instance,
                    "observed_at": effective.observed_at,
                },
            )
        ).first()
        if row is None:
            return False
        published_ready = str(row.state) == "ready"
        if published_ready and not previous_ready:
            await conn.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {
                    "channel": "fb_task_queue",
                    "payload": ('{"task_type":"meta_api_mutation","reason":"browser_readiness"}'),
                },
            )
    return published_ready


async def resolve_readiness_ad_account_id(engine: AsyncEngine) -> str | None:
    """Кабинет пробы готовности — первый кабинет активных офферов.

    Детерминированный выбор из конфигурации, а не из состояния браузера.
    None означает, что настроенного кабинета нет: подтверждать готовность
    money-канала не на чем, и открывать наугад чужую вкладку нельзя.
    """
    accounts = await resolve_configured_ad_account_ids(engine)
    return accounts[0] if accounts else None


async def probe_and_publish_browser_readiness(
    engine: AsyncEngine,
    client: BrowserReadinessProbeClient,
    *,
    writer_instance: uuid.UUID,
    ttl_seconds: int = BROWSER_READINESS_DEFAULT_TTL_SECONDS,
) -> bool:
    """Probe once and publish bounded evidence without reading the Vision token."""
    identity: VisionReadinessIdentity | None = None
    try:
        async with BrowserOperationFence(
            engine,
            operation_kind="browser_readiness_probe",
            target=BROWSER_READINESS_CHANNEL,
        ) as fence:
            identity = await load_vision_readiness_identity(engine)
            if identity is None:
                await invalidate_browser_readiness(
                    engine,
                    writer_instance=writer_instance,
                    reason_code="vision_config_unavailable",
                )
                return False
            probe_account_id = await resolve_readiness_ad_account_id(engine)
            if probe_account_id is None:
                await invalidate_browser_readiness(
                    engine,
                    writer_instance=writer_instance,
                    reason_code="no_configured_cabinet",
                )
                return False
            try:
                probe = await client.check_health(
                    full_probe=False,
                    expected_profile_id=identity.profile_id,
                    ad_account_id=probe_account_id,
                )
            except Exception as exc:  # noqa: BLE001 - fail closed and age out
                observed_at = await _database_clock(engine)
                logger.warning(
                    "browser readiness probe unavailable error_type=%s",
                    type(exc).__name__,
                )
                observation = BrowserReadinessObservation(
                    state="unavailable",
                    reason_code="readiness_probe_unavailable",
                    observed_contract_version=None,
                    observed_profile_id=None,
                    observed_session_id=None,
                    observed_at=observed_at,
                )
            else:
                observed_at = await _database_clock(engine)
                observation = replace(
                    classify_browser_readiness(
                        probe,
                        expected_profile_id=identity.profile_id,
                    ),
                    observed_at=observed_at,
                )
            await fence.assert_held()
            return await persist_browser_readiness(
                engine,
                identity=identity,
                observation=observation,
                writer_instance=writer_instance,
                ttl_seconds=ttl_seconds,
            )
    except BrowserOperationBlocked:
        await invalidate_browser_readiness(
            engine,
            writer_instance=writer_instance,
            state="maintenance",
            reason_code="browser_maintenance_active",
        )
        return False
    except BrowserFenceLeaseLost:
        await invalidate_browser_readiness(
            engine,
            writer_instance=writer_instance,
            reason_code="readiness_probe_fence_lost",
        )
        return False


__all__ = [
    "BROWSER_READINESS_CHANNEL",
    "BROWSER_READINESS_DEFAULT_TTL_SECONDS",
    "BrowserReadinessObservation",
    "VisionReadinessIdentity",
    "classify_browser_readiness",
    "invalidate_browser_readiness",
    "load_vision_readiness_identity",
    "persist_browser_readiness",
    "probe_and_publish_browser_readiness",
    "resolve_readiness_ad_account_id",
]
