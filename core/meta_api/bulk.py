# -*- coding: utf-8 -*-
"""Owner-scoped bulk command helpers without autonomous activation paths."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.observer.queries import campaign_matches_owner

logger = logging.getLogger(__name__)

MAX_BULK = 50

# Bound catalog reads before applying the stricter operator limit in Python.
# The larger SQL cap keeps the reported total useful without allowing an
# unbounded scan when an offer code matches an unexpectedly large catalogue.
_SQL_ROW_CAP = 20000


@dataclass(frozen=True, slots=True)
class StatusTargetLocks:
    """Deterministic session locks held through one read-only reconciliation."""

    requested_ad_ids: tuple[str, ...]
    busy_ad_id: str | None = None


@asynccontextmanager
async def locked_status_targets(
    engine: AsyncEngine,
    *,
    ad_ids: tuple[str, ...] | list[str],
) -> AsyncIterator[StatusTargetLocks]:
    """Serialize ambiguous bulk-status reconciliation per immutable ad id.

    A busy target is reported without waiting. Acquired locks remain held until
    the caller terminalizes the UNKNOWN command, preventing a concurrent money
    command from crossing the read/finalize boundary.
    """
    requested = tuple(sorted({str(ad_id).strip() for ad_id in ad_ids if str(ad_id).strip()}))
    if not requested:
        raise ValueError("status target locks require at least one ad id")

    acquired: list[str] = []
    async with engine.connect() as conn:
        try:
            busy_ad_id: str | None = None
            for ad_id in requested:
                locked = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(hashtext(:ad_id))"),
                    {"ad_id": ad_id},
                )
                if not bool(locked):
                    busy_ad_id = ad_id
                    break
                acquired.append(ad_id)
            await conn.commit()
            yield StatusTargetLocks(
                requested_ad_ids=requested,
                busy_ad_id=busy_ad_id,
            )
        finally:

            async def _unlock() -> None:
                if conn.in_transaction():
                    await conn.rollback()
                unlock_failed = False
                for ad_id in reversed(acquired):
                    unlocked = await conn.scalar(
                        text("SELECT pg_advisory_unlock(hashtext(:ad_id))"),
                        {"ad_id": ad_id},
                    )
                    unlock_failed = unlock_failed or not bool(unlocked)
                await conn.commit()
                if unlock_failed:
                    raise RuntimeError("one or more status reconciliation locks were not owned")

            release = asyncio.create_task(_unlock())
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                release_error: BaseException | None = None
                try:
                    await release
                except BaseException as exc:  # noqa: BLE001
                    release_error = exc
                if release_error is not None:
                    try:
                        await conn.invalidate()
                    except Exception:
                        logger.exception(
                            "failed to invalidate cancelled reconciliation lock connection"
                        )
                raise
            except Exception:
                logger.exception("failed to release status reconciliation locks")
                await conn.invalidate()


async def resolve_owner_ad_ids(
    engine: AsyncEngine,
    *,
    offer_code: str,
    owner_tag: str | None = None,
    limit: int = MAX_BULK,
) -> tuple[list[str], int]:
    """Resolve active owner-scoped ads for an explicit bulk command."""
    escaped = re.escape(offer_code.lower())
    pattern = rf"(^|[^a-z0-9]){escaped}([^a-z0-9]|$)"
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT DISTINCT a.fb_ad_id, c.campaign_name, a.ad_name
                    FROM fb_ads a
                    JOIN fb_adsets s ON s.id = a.adset_id
                    JOIN fb_campaigns c ON c.id = s.campaign_id
                    WHERE (c.campaign_name ~* :pattern OR a.ad_name ~* :pattern)
                      AND a.fb_ad_id IS NOT NULL
                      AND a.is_active = TRUE
                    LIMIT :sql_cap
                    """
                ),
                {"pattern": pattern, "sql_cap": _SQL_ROW_CAP},
            )
        ).all()

    owned: list[str] = []
    for fb_ad_id, campaign_name, ad_name in rows:
        if not fb_ad_id:
            continue
        if not campaign_matches_owner(
            campaign_name=campaign_name or "", ad_name=ad_name or "", owner_tag=owner_tag
        ):
            continue
        owned.append(str(fb_ad_id))

    return owned[:limit], len(owned)


__all__ = [
    "MAX_BULK",
    "StatusTargetLocks",
    "locked_status_targets",
    "resolve_owner_ad_ids",
]
