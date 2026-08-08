# -*- coding: utf-8 -*-
"""Structured-concurrency supervisor for independent cabinet scan actors."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.deadlines import bind_absolute_deadline

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CabinetLease:
    ad_account_id: str
    owner_instance: uuid.UUID
    lease_token: int


async def acquire_cabinet_lease(
    engine: AsyncEngine,
    *,
    ad_account_id: str,
    owner_instance: uuid.UUID,
    ttl_seconds: int,
) -> CabinetLease | None:
    """Acquire or renew ownership and monotonically fence previous actors."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    INSERT INTO cabinet_runtime (
                        ad_account_id, owner_instance, lease_token,
                        lease_expires_at, last_progress_at, stage
                    )
                    VALUES (
                        :account, :owner, 1,
                        NOW() + make_interval(secs => :ttl), NOW(), 'claimed'
                    )
                    ON CONFLICT (ad_account_id) DO UPDATE
                    SET owner_instance = EXCLUDED.owner_instance,
                        lease_token = cabinet_runtime.lease_token + 1,
                        lease_expires_at = EXCLUDED.lease_expires_at,
                        last_progress_at = NOW(),
                        stage = 'claimed',
                        last_error_code = NULL
                    WHERE cabinet_runtime.owner_instance = EXCLUDED.owner_instance
                       OR cabinet_runtime.lease_expires_at IS NULL
                       OR cabinet_runtime.lease_expires_at <= NOW()
                    RETURNING lease_token
                    """
                ),
                {
                    "account": ad_account_id,
                    "owner": owner_instance,
                    "ttl": max(5, int(ttl_seconds)),
                },
            )
        ).first()
    if row is None:
        return None
    return CabinetLease(ad_account_id, owner_instance, int(row.lease_token))


async def update_cabinet_progress(
    engine: AsyncEngine,
    lease: CabinetLease,
    *,
    stage: str,
    ttl_seconds: int,
    has_snapshot: bool = False,
    error_code: str | None = None,
) -> bool:
    """Refresh a lease only when the caller still owns its fencing token."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE cabinet_runtime
                SET stage = :stage,
                    last_progress_at = NOW(),
                    last_snapshot_at = CASE WHEN :snapshot THEN NOW() ELSE last_snapshot_at END,
                    last_error_code = :error,
                    lease_expires_at = NOW() + make_interval(secs => :ttl)
                WHERE ad_account_id = :account
                  AND owner_instance = :owner
                  AND lease_token = :token
                """
            ),
            {
                "account": lease.ad_account_id,
                "owner": lease.owner_instance,
                "token": lease.lease_token,
                "stage": stage[:32],
                "snapshot": has_snapshot,
                "error": error_code[:64] if error_code else None,
                "ttl": max(5, int(ttl_seconds)),
            },
        )
    return bool(result.rowcount)


async def assert_cabinet_lease(
    engine: AsyncEngine,
    lease: CabinetLease,
) -> bool:
    """Check the persisted fencing token immediately before critical writes."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT 1
                    FROM cabinet_runtime
                    WHERE ad_account_id = :account
                      AND owner_instance = :owner
                      AND lease_token = :token
                      AND lease_expires_at > NOW()
                    """
                ),
                {
                    "account": lease.ad_account_id,
                    "owner": lease.owner_instance,
                    "token": lease.lease_token,
                },
            )
        ).first()
    return row is not None


async def release_cabinet_lease(engine: AsyncEngine, lease: CabinetLease) -> bool:
    """Release ownership without allowing a stale actor to clear a newer lease."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE cabinet_runtime
                SET owner_instance = NULL,
                    lease_expires_at = NULL,
                    last_progress_at = NOW()
                WHERE ad_account_id = :account
                  AND owner_instance = :owner
                  AND lease_token = :token
                """
            ),
            {
                "account": lease.ad_account_id,
                "owner": lease.owner_instance,
                "token": lease.lease_token,
            },
        )
    return bool(result.rowcount)


RunCabinet = Callable[[str, int, CabinetLease], Awaitable[dict[str, Any]]]


class CabinetSupervisor:
    """Run one isolated actor per cabinet with bounded structured concurrency."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        owner_instance: uuid.UUID,
        concurrency: int = 1,
        scan_deadline_seconds: int = 120,
        lease_ttl_seconds: int = 150,
    ) -> None:
        self._engine = engine
        self._owner_instance = owner_instance
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._scan_deadline_seconds = max(5, scan_deadline_seconds)
        self._lease_ttl_seconds = max(
            self._scan_deadline_seconds + 10,
            lease_ttl_seconds,
        )

    async def run_cycle(
        self,
        accounts: Sequence[str],
        run_cabinet: RunCabinet,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any] | None] = [None] * len(accounts)

        async def run_actor(index: int, account_id: str) -> None:
            async with self._semaphore:
                # A session-level advisory lock spans the complete actor lifetime.
                # Unlike the expiring row lease it cannot be overtaken while a
                # stale process still owns its database session. Connection loss
                # releases it automatically, after which the monotonically
                # increasing row token fences any late progress update.
                async with self._engine.connect() as lock_conn:
                    lock_key = f"fb_agent:cabinet_actor:{account_id}"
                    if not await self._try_actor_lock(lock_conn, lock_key):
                        results[index] = {
                            "ad_account_id": account_id,
                            "outcome": "skipped",
                            "error": "cabinet_actor_lock_held",
                        }
                        return
                    lease: CabinetLease | None = None
                    try:
                        lease = await acquire_cabinet_lease(
                            self._engine,
                            ad_account_id=account_id,
                            owner_instance=self._owner_instance,
                            ttl_seconds=self._lease_ttl_seconds,
                        )
                        if lease is None:
                            results[index] = {
                                "ad_account_id": account_id,
                                "outcome": "skipped",
                                "error": "cabinet_lease_held",
                            }
                            return
                        progress_started = await update_cabinet_progress(
                            self._engine,
                            lease,
                            stage="scanning",
                            ttl_seconds=self._lease_ttl_seconds,
                        )
                        if not progress_started:
                            results[index] = {
                                "ad_account_id": account_id,
                                "outcome": "error",
                                "error": "cabinet_lease_lost_before_scan",
                            }
                            return
                        deadline_at = datetime.now(UTC) + timedelta(
                            seconds=self._scan_deadline_seconds
                        )
                        with bind_absolute_deadline(deadline_at):
                            async with asyncio.timeout(self._scan_deadline_seconds):
                                results[index] = await run_cabinet(account_id, index, lease)
                        progress_applied = await update_cabinet_progress(
                            self._engine,
                            lease,
                            stage="idle",
                            ttl_seconds=self._lease_ttl_seconds,
                            has_snapshot=results[index].get("outcome") in {"success", "empty"},
                            error_code=results[index].get("error"),
                        )
                        if not progress_applied:
                            results[index] = {
                                **results[index],
                                "outcome": "error",
                                "error": "cabinet_lease_lost",
                            }
                    except TimeoutError:
                        results[index] = {
                            "ad_account_id": account_id,
                            "outcome": "timeout",
                            "error": "scan_deadline_exceeded",
                        }
                        await update_cabinet_progress(
                            self._engine,
                            lease,
                            stage="timeout",
                            ttl_seconds=self._lease_ttl_seconds,
                            error_code="scan_deadline_exceeded",
                        )
                    except Exception as exc:  # noqa: BLE001 - isolate cabinet actors
                        results[index] = {
                            "ad_account_id": account_id,
                            "outcome": "error",
                            "error": type(exc).__name__,
                        }
                        if lease is not None:
                            await update_cabinet_progress(
                                self._engine,
                                lease,
                                stage="error",
                                ttl_seconds=self._lease_ttl_seconds,
                                error_code=type(exc).__name__,
                            )
                    finally:
                        cleanup_errors: list[str] = []
                        if lease is not None:
                            try:
                                await release_cabinet_lease(self._engine, lease)
                            except Exception as exc:  # noqa: BLE001 - isolate actors
                                cleanup_errors.append(f"lease:{type(exc).__name__}")
                                logger.exception(
                                    "cabinet actor lease cleanup failed account=%s",
                                    account_id,
                                )
                        try:
                            await self._release_actor_lock(lock_conn, lock_key)
                        except Exception as exc:  # noqa: BLE001 - connection loss releases lock
                            cleanup_errors.append(f"lock:{type(exc).__name__}")
                            logger.exception(
                                "cabinet actor advisory-lock cleanup failed account=%s",
                                account_id,
                            )
                        if cleanup_errors:
                            previous = results[index] or {"ad_account_id": account_id}
                            results[index] = {
                                **previous,
                                "outcome": "error",
                                "error": "cabinet_cleanup_failed:" + ",".join(cleanup_errors),
                            }

        async with asyncio.TaskGroup() as group:
            for index, account_id in enumerate(accounts):
                group.create_task(run_actor(index, account_id), name=f"cabinet:{account_id}")

        return [
            result
            if result is not None
            else {"ad_account_id": account, "outcome": "error", "error": "actor_no_result"}
            for account, result in zip(accounts, results, strict=True)
        ]

    @staticmethod
    async def _try_actor_lock(conn: AsyncConnection, lock_key: str) -> bool:
        acquired = bool(
            (
                await conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": lock_key},
                )
            ).scalar_one()
        )
        # Session advisory locks survive transaction end; do not keep an idle
        # transaction open for the entire browser scan.
        await conn.commit()
        return acquired

    @staticmethod
    async def _release_actor_lock(conn: AsyncConnection, lock_key: str) -> None:
        await conn.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )
        await conn.commit()


__all__ = [
    "CabinetLease",
    "CabinetSupervisor",
    "acquire_cabinet_lease",
    "assert_cabinet_lease",
    "release_cabinet_lease",
    "update_cabinet_progress",
]
