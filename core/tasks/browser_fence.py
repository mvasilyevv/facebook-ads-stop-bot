# -*- coding: utf-8 -*-
"""Durable shared/exclusive barrier for every browser-affecting operation.

Queue workers are fenced by ``task_queue`` and the maintenance row in
``system_config``.  A few synchronous API operations intentionally do not use
the task queue; they publish a renewable row in ``browser_operation_leases``.
The host-side maintenance owner inserts the exclusive gate under the same
PostgreSQL advisory transaction lock, then drains both sources of active work.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

BROWSER_FENCE_LEASE_SECONDS = 45
BROWSER_FENCE_RENEW_SECONDS = 10
BROWSER_EXCLUSIVE_DRAIN_SECONDS = 30
BROWSER_MAINTENANCE_CAPABILITY_MAX_TTL_SECONDS = 35
_MAINTENANCE_CONSUME_DB_TIMEOUT_SECONDS = 1.5
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OWNER_RE = re.compile(r"^[0-9a-f]{32}$")
_CAPABILITY_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")

_ADVISORY_LOCK_SQL = text(
    """
    SELECT pg_advisory_xact_lock(
      hashtext('fb-agent'),
      hashtext('browser-maintenance')
    )
    """
)
_SET_LOCAL_STATEMENT_TIMEOUT_SQL = text("SELECT set_config('statement_timeout', :timeout_ms, true)")
_CONSUME_MAINTENANCE_CAPABILITY_SQL = text(
    """
    UPDATE system_config
    SET value = value || jsonb_build_object(
          'consumed_capability_nonce', CAST(:nonce AS text),
          'consumed_capability_profile_id', CAST(:profile_id AS text),
          'consumed_capability_expires_at', to_timestamp(:expires_at_epoch),
          'consumed_capability_at', clock_timestamp()
        ),
        updated_at = clock_timestamp()
    WHERE key = 'browser_maintenance'
      AND value->>'owner' = :owner
      AND (value->>'expires_at')::timestamptz > clock_timestamp()
      AND NOT (value ? 'consumed_capability_nonce')
      AND to_timestamp(:expires_at_epoch) > clock_timestamp()
      AND to_timestamp(:expires_at_epoch)
            <= clock_timestamp()
              + make_interval(secs => :max_capability_ttl_seconds)
      AND to_timestamp(:expires_at_epoch)
            <= (value->>'expires_at')::timestamptz
    RETURNING value->>'consumed_capability_nonce'
    """
)


class BrowserOperationBlocked(RuntimeError):
    """An exclusive browser maintenance lease currently blocks new work."""


class BrowserFenceLeaseLost(RuntimeError):
    """A running operation can no longer prove ownership of its durable lease."""


class BrowserMaintenanceOwnerInvalid(RuntimeError):
    """The platform caller did not prove the current maintenance capability."""


class BrowserMaintenanceCapabilityConsumeDeniedError(RuntimeError):
    """The active maintenance lease cannot consume this recovery grant."""


class BrowserMaintenanceCapabilityAuthorityUnavailableError(RuntimeError):
    """PostgreSQL could not prove and commit maintenance grant consumption."""


class BrowserOperationDrainTimeout(RuntimeError):
    """Existing browser work did not quiesce before the bounded deadline."""


def _bounded_lease_seconds(value: int) -> int:
    return max(15, min(int(value), 300))


@dataclass(frozen=True, slots=True)
class BrowserMaintenanceCapabilityConsume:
    """Minimal durable binding for one signed Vision recovery request."""

    profile_id: str
    owner: str
    expires_at_epoch: int
    nonce: str

    def validate(self) -> None:
        if (
            not self.profile_id
            or len(self.profile_id) > 128
            or "\n" in self.profile_id
            or "\r" in self.profile_id
            or _OWNER_RE.fullmatch(self.owner) is None
            or _CAPABILITY_NONCE_RE.fullmatch(self.nonce) is None
            or not isinstance(self.expires_at_epoch, int)
            or isinstance(self.expires_at_epoch, bool)
            or self.expires_at_epoch <= 0
        ):
            raise BrowserMaintenanceCapabilityConsumeDeniedError(
                "browser maintenance capability binding is malformed"
            )


async def consume_browser_maintenance_capability(
    engine: AsyncEngine,
    capability: BrowserMaintenanceCapabilityConsume,
) -> None:
    """Commit exactly one recovery consume for the exact active lease owner."""
    capability.validate()
    params = {
        "profile_id": capability.profile_id,
        "owner": capability.owner,
        "expires_at_epoch": capability.expires_at_epoch,
        "nonce": capability.nonce,
        "max_capability_ttl_seconds": (BROWSER_MAINTENANCE_CAPABILITY_MAX_TTL_SECONDS),
    }
    try:
        async with asyncio.timeout(_MAINTENANCE_CONSUME_DB_TIMEOUT_SECONDS):
            async with engine.begin() as conn:
                await conn.execute(
                    _SET_LOCAL_STATEMENT_TIMEOUT_SQL,
                    {"timeout_ms": str(int(_MAINTENANCE_CONSUME_DB_TIMEOUT_SECONDS * 1000))},
                )
                result = await conn.execute(
                    _CONSUME_MAINTENANCE_CAPABILITY_SQL,
                    params,
                )
                consumed_nonce = result.scalar_one_or_none()
    except BrowserMaintenanceCapabilityConsumeDeniedError:
        raise
    except Exception as exc:  # noqa: BLE001 - fail closed before Vision mutation
        raise BrowserMaintenanceCapabilityAuthorityUnavailableError(
            "browser maintenance capability authority is unavailable"
        ) from exc
    if consumed_nonce != capability.nonce:
        raise BrowserMaintenanceCapabilityConsumeDeniedError(
            "browser maintenance capability is stale, mismatched, expired, or already consumed"
        )


@dataclass(slots=True)
class BrowserOperationFence:
    """Renewable shared lease for one synchronous browser-affecting operation."""

    engine: AsyncEngine
    operation_kind: str
    target: str | None = None
    lease_seconds: int = BROWSER_FENCE_LEASE_SECONDS
    renew_seconds: int = BROWSER_FENCE_RENEW_SECONDS
    operation_id: uuid.UUID = field(default_factory=uuid.uuid4, init=False)
    owner: uuid.UUID = field(default_factory=uuid.uuid4, init=False)
    _owner_task: asyncio.Task[object] | None = field(default=None, init=False)
    _renewal_task: asyncio.Task[None] | None = field(default=None, init=False)
    _lease_lost: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _acquired: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not _KIND_RE.fullmatch(self.operation_kind):
            raise ValueError("browser operation kind must be a lowercase identifier")
        if self.target is not None:
            self.target = self.target.strip() or None
        if self.target is not None and len(self.target) > 128:
            raise ValueError("browser operation target is too long")
        self.lease_seconds = _bounded_lease_seconds(self.lease_seconds)
        self.renew_seconds = max(
            1,
            min(int(self.renew_seconds), max(1, self.lease_seconds // 3)),
        )

    async def __aenter__(self) -> BrowserOperationFence:
        self._owner_task = asyncio.current_task()
        async with self.engine.begin() as conn:
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            await conn.execute(_ADVISORY_LOCK_SQL)
            await conn.execute(
                text(
                    """
                    DELETE FROM browser_operation_leases
                    WHERE lease_expires_at <= clock_timestamp()
                    """
                )
            )
            row = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO browser_operation_leases (
                          operation_id,
                          owner,
                          operation_kind,
                          target,
                          lease_expires_at
                        )
                        SELECT
                          :operation_id,
                          :owner,
                          :operation_kind,
                          :target,
                          clock_timestamp()
                            + make_interval(secs => :lease_seconds)
                        WHERE NOT EXISTS (
                          SELECT 1
                          FROM system_config
                          WHERE key = 'browser_maintenance'
                            AND (value->>'expires_at')::timestamptz
                              > clock_timestamp()
                        )
                        RETURNING operation_id
                        """
                    ),
                    {
                        "operation_id": self.operation_id,
                        "owner": self.owner,
                        "operation_kind": self.operation_kind,
                        "target": self.target,
                        "lease_seconds": self.lease_seconds,
                    },
                )
            ).first()
        if row is None:
            raise BrowserOperationBlocked("browser maintenance is active")
        self._acquired = True
        self._renewal_task = asyncio.create_task(
            self._renew_loop(),
            name=f"browser-fence-{self.operation_kind}-{self.operation_id}",
        )
        return self

    async def _renew(self) -> bool:
        async with self.engine.begin() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        UPDATE browser_operation_leases
                        SET lease_expires_at = clock_timestamp()
                              + make_interval(secs => :lease_seconds),
                            updated_at = clock_timestamp()
                        WHERE operation_id = :operation_id
                          AND owner = :owner
                          AND lease_expires_at > clock_timestamp()
                        RETURNING operation_id
                        """
                    ),
                    {
                        "operation_id": self.operation_id,
                        "owner": self.owner,
                        "lease_seconds": self.lease_seconds,
                    },
                )
            ).first()
        return row is not None

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.renew_seconds)
                if not await self._renew():
                    self._mark_lost()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._mark_lost()

    def _mark_lost(self, *, cancel_owner: bool = True) -> None:
        self._lease_lost.set()
        if cancel_owner and self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel()

    async def assert_held(self) -> None:
        if self._lease_lost.is_set() or not self._acquired:
            raise BrowserFenceLeaseLost("browser operation lease was lost")
        async with self.engine.connect() as conn:
            held = bool(
                (
                    await conn.execute(
                        text(
                            """
                            SELECT EXISTS (
                              SELECT 1
                              FROM browser_operation_leases
                              WHERE operation_id = :operation_id
                                AND owner = :owner
                                AND lease_expires_at > clock_timestamp()
                            )
                            """
                        ),
                        {
                            "operation_id": self.operation_id,
                            "owner": self.owner,
                        },
                    )
                ).scalar_one()
            )
        if not held:
            self._mark_lost(cancel_owner=False)
            raise BrowserFenceLeaseLost("browser operation lease was lost")

    async def _close(self) -> None:
        renewal = self._renewal_task
        self._renewal_task = None
        if renewal is not None:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal
        if self._acquired:
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(
                        text(
                            """
                            DELETE FROM browser_operation_leases
                            WHERE operation_id = :operation_id
                              AND owner = :owner
                            """
                        ),
                        {
                            "operation_id": self.operation_id,
                            "owner": self.owner,
                        },
                    )
            finally:
                self._acquired = False

    async def __aexit__(self, exc_type, exc, _tb) -> bool:
        lost = self._lease_lost.is_set()
        await self._close()
        if lost:
            raise BrowserFenceLeaseLost("browser operation lease was lost") from exc
        return False


@dataclass(slots=True)
class BrowserExclusiveMaintenance:
    """Exclusive API-owned maintenance fence with bounded active-work drain."""

    engine: AsyncEngine
    operation_kind: str
    lease_seconds: int = BROWSER_FENCE_LEASE_SECONDS
    renew_seconds: int = BROWSER_FENCE_RENEW_SECONDS
    drain_seconds: int = BROWSER_EXCLUSIVE_DRAIN_SECONDS
    owner: str = field(default_factory=lambda: uuid.uuid4().hex, init=False)
    _owner_task: asyncio.Task[object] | None = field(default=None, init=False)
    _renewal_task: asyncio.Task[None] | None = field(default=None, init=False)
    _lease_lost: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _acquired: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not _KIND_RE.fullmatch(self.operation_kind):
            raise ValueError("browser maintenance kind must be a lowercase identifier")
        self.lease_seconds = _bounded_lease_seconds(self.lease_seconds)
        self.renew_seconds = max(
            1,
            min(int(self.renew_seconds), max(1, self.lease_seconds // 3)),
        )
        self.drain_seconds = max(1, min(int(self.drain_seconds), 120))

    async def __aenter__(self) -> BrowserExclusiveMaintenance:
        self._owner_task = asyncio.current_task()
        async with self.engine.begin() as conn:
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            await conn.execute(_ADVISORY_LOCK_SQL)
            row = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO system_config (key, value, description)
                        VALUES (
                          'browser_maintenance',
                          jsonb_build_object(
                            'owner', CAST(:owner AS text),
                            'source', CAST(:operation_kind AS text),
                            'expires_at',
                            clock_timestamp()
                              + make_interval(secs => :lease_seconds)
                          ),
                          'Blocks every new browser-backed operation'
                        )
                        ON CONFLICT (key) DO UPDATE
                        SET value = EXCLUDED.value,
                            description = EXCLUDED.description,
                            updated_at = clock_timestamp()
                        WHERE COALESCE(
                          (system_config.value->>'expires_at')::timestamptz,
                          '-infinity'::timestamptz
                        ) <= clock_timestamp()
                        RETURNING value->>'owner'
                        """
                    ),
                    {
                        "owner": self.owner,
                        "operation_kind": self.operation_kind,
                        "lease_seconds": self.lease_seconds,
                    },
                )
            ).first()
        if row is None or str(row[0]) != self.owner:
            raise BrowserOperationBlocked("browser maintenance is already active")
        self._acquired = True
        self._renewal_task = asyncio.create_task(
            self._renew_loop(),
            name=f"browser-exclusive-{self.operation_kind}-{self.owner}",
        )
        try:
            await self._wait_quiescent()
        except BaseException:
            await self._close()
            raise
        return self

    async def _renew(self) -> bool:
        async with self.engine.begin() as conn:
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            await conn.execute(_ADVISORY_LOCK_SQL)
            row = (
                await conn.execute(
                    text(
                        """
                        UPDATE system_config
                        SET value = jsonb_set(
                              value,
                              '{expires_at}',
                              to_jsonb(
                                clock_timestamp()
                                  + make_interval(secs => :lease_seconds)
                              )
                            ),
                            updated_at = clock_timestamp()
                        WHERE key = 'browser_maintenance'
                          AND value->>'owner' = :owner
                          AND (value->>'expires_at')::timestamptz
                            > clock_timestamp()
                        RETURNING value->>'owner'
                        """
                    ),
                    {
                        "owner": self.owner,
                        "lease_seconds": self.lease_seconds,
                    },
                )
            ).first()
        return row is not None and str(row[0]) == self.owner

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.renew_seconds)
                if not await self._renew():
                    self._mark_lost()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._mark_lost()

    def _mark_lost(self, *, cancel_owner: bool = True) -> None:
        self._lease_lost.set()
        if cancel_owner and self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel()

    async def _active_work_count(self) -> int:
        async with self.engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        text(
                            """
                            SELECT
                              (
                                SELECT count(*)
                                FROM task_queue
                                WHERE lower(status) = 'running'
                                  AND task_type IN (
                                    'meta_api_mutation',
                                    'observer_scan',
                                    'campaign_create'
                                  )
                              )
                              +
                              (
                                SELECT count(*)
                                FROM browser_operation_leases
                                WHERE lease_expires_at > clock_timestamp()
                              )
                            """
                        )
                    )
                ).scalar_one()
            )

    async def _wait_quiescent(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.drain_seconds
        while True:
            if self._lease_lost.is_set():
                raise BrowserFenceLeaseLost("browser maintenance lease was lost")
            if await self._active_work_count() == 0:
                await self.assert_held()
                return
            if loop.time() >= deadline:
                raise BrowserOperationDrainTimeout(
                    "active browser work did not drain before the deadline"
                )
            await asyncio.sleep(0.2)

    async def assert_held(self) -> None:
        if self._lease_lost.is_set() or not await self._renew():
            self._mark_lost(cancel_owner=False)
            raise BrowserFenceLeaseLost("browser maintenance lease was lost")

    async def _close(self) -> None:
        renewal = self._renewal_task
        self._renewal_task = None
        if renewal is not None:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal
        if self._acquired:
            try:
                async with self.engine.begin() as conn:
                    await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                    await conn.execute(_ADVISORY_LOCK_SQL)
                    await conn.execute(
                        text(
                            """
                            DELETE FROM system_config
                            WHERE key = 'browser_maintenance'
                              AND value->>'owner' = :owner
                            """
                        ),
                        {"owner": self.owner},
                    )
            finally:
                self._acquired = False

    async def __aexit__(self, exc_type, exc, _tb) -> bool:
        lost = self._lease_lost.is_set()
        await self._close()
        if lost:
            raise BrowserFenceLeaseLost("browser maintenance lease was lost") from exc
        return False


@dataclass(slots=True)
class BrowserMaintenanceGuard:
    """Adopt and renew the platform's exclusive owner for one internal call."""

    engine: AsyncEngine
    owner: str
    lease_seconds: int = BROWSER_FENCE_LEASE_SECONDS
    renew_seconds: int = BROWSER_FENCE_RENEW_SECONDS
    _owner_task: asyncio.Task[object] | None = field(default=None, init=False)
    _renewal_task: asyncio.Task[None] | None = field(default=None, init=False)
    _lease_lost: asyncio.Event = field(default_factory=asyncio.Event, init=False)

    def __post_init__(self) -> None:
        self.owner = self.owner.strip().lower()
        if not _OWNER_RE.fullmatch(self.owner):
            raise BrowserMaintenanceOwnerInvalid("browser maintenance owner is missing or invalid")
        self.lease_seconds = _bounded_lease_seconds(self.lease_seconds)
        self.renew_seconds = max(
            1,
            min(int(self.renew_seconds), max(1, self.lease_seconds // 3)),
        )

    async def __aenter__(self) -> BrowserMaintenanceGuard:
        self._owner_task = asyncio.current_task()
        if not await self._renew():
            raise BrowserMaintenanceOwnerInvalid("browser maintenance owner is not active")
        if await self._active_work_count() != 0:
            raise BrowserOperationBlocked("browser maintenance has not drained active work")
        self._renewal_task = asyncio.create_task(
            self._renew_loop(),
            name=f"browser-maintenance-guard-{self.owner}",
        )
        return self

    async def _renew(self) -> bool:
        async with self.engine.begin() as conn:
            await conn.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            await conn.execute(_ADVISORY_LOCK_SQL)
            row = (
                await conn.execute(
                    text(
                        """
                        UPDATE system_config
                        SET value = jsonb_set(
                              value,
                              '{expires_at}',
                              to_jsonb(
                                clock_timestamp()
                                  + make_interval(secs => :lease_seconds)
                              )
                            ),
                            updated_at = clock_timestamp()
                        WHERE key = 'browser_maintenance'
                          AND value->>'owner' = :owner
                          AND (value->>'expires_at')::timestamptz
                            > clock_timestamp()
                        RETURNING value->>'owner'
                        """
                    ),
                    {
                        "owner": self.owner,
                        "lease_seconds": self.lease_seconds,
                    },
                )
            ).first()
        return row is not None

    async def _renew_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.renew_seconds)
                if not await self._renew():
                    self._mark_lost()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._mark_lost()

    def _mark_lost(self, *, cancel_owner: bool = True) -> None:
        self._lease_lost.set()
        if cancel_owner and self._owner_task is not None and not self._owner_task.done():
            self._owner_task.cancel()

    async def _active_work_count(self) -> int:
        async with self.engine.connect() as conn:
            return int(
                (
                    await conn.execute(
                        text(
                            """
                            SELECT
                              (
                                SELECT count(*)
                                FROM task_queue
                                WHERE lower(status) = 'running'
                                  AND task_type IN (
                                    'meta_api_mutation',
                                    'observer_scan',
                                    'campaign_create'
                                  )
                              )
                              +
                              (
                                SELECT count(*)
                                FROM browser_operation_leases
                                WHERE lease_expires_at > clock_timestamp()
                              )
                            """
                        )
                    )
                ).scalar_one()
            )

    async def assert_held(self) -> None:
        if self._lease_lost.is_set() or not await self._renew():
            self._mark_lost(cancel_owner=False)
            raise BrowserFenceLeaseLost("browser maintenance lease was lost")
        if await self._active_work_count() != 0:
            raise BrowserOperationBlocked("browser maintenance is no longer quiescent")

    async def __aexit__(self, exc_type, exc, _tb) -> bool:
        renewal = self._renewal_task
        self._renewal_task = None
        if renewal is not None:
            renewal.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await renewal
        if self._lease_lost.is_set():
            raise BrowserFenceLeaseLost("browser maintenance lease was lost") from exc
        return False
