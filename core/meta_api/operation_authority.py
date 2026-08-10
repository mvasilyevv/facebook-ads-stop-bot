"""PostgreSQL-authoritative single-consume browser capability contract."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.client import BROWSER_CONTRACT_VERSION, browser_operation_payload

_CONSUME_DB_TIMEOUT_SECONDS = 2.0
_NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
_CALLERS = frozenset({"autopause", "meta_api", "campaign_creator"})
_RPCS = frozenset({"execute_graph_call", "upload_image", "upload_video"})

_SET_LOCAL_STATEMENT_TIMEOUT_SQL = text("SELECT set_config('statement_timeout', :timeout_ms, true)")
_CONSUME_PENDING_CAPABILITY_SQL = text(
    """
    WITH live_task AS MATERIALIZED (
        SELECT
            task.id,
            task.lease_owner,
            task.lease_token
        FROM task_queue AS task
        WHERE task.id = :task_id
          AND task.status = 'running'
          AND task.lease_owner = :lease_owner
          AND task.lease_token = :lease_token
          AND task.lease_expires_at > clock_timestamp()
          AND task.cancel_requested_at IS NULL
          AND task.deadline_at IS NOT NULL
          AND task.deadline_at > clock_timestamp()
        FOR SHARE
    )
    UPDATE browser_operation_capability_uses AS capability
    SET consumed_at = clock_timestamp()
    FROM live_task AS task
    WHERE capability.nonce_sha256 = :nonce_sha256
      AND capability.capability_digest = :capability_digest
      AND capability.operation_digest = :operation_digest
      AND capability.browser_contract_version = :browser_contract_version
      AND capability.caller = :caller
      AND capability.rpc = :rpc
      AND capability.task_id = :task_id
      AND capability.lease_owner = :lease_owner
      AND capability.lease_token = :lease_token
      AND capability.session_id = :session_id
      AND capability.vision_profile_id = :vision_profile_id
      AND capability.ad_account_id = :ad_account_id
      AND capability.expires_at = to_timestamp(:expires_at_epoch)
      AND capability.consumed_at IS NULL
      AND capability.expires_at > clock_timestamp()
      AND task.id = capability.task_id
      AND task.lease_owner = capability.lease_owner
      AND task.lease_token = capability.lease_token
    RETURNING capability.consumed_at
    """
)


class BrowserCapabilityConsumeDeniedError(RuntimeError):
    """The pending grant is absent, stale, mismatched, expired or already used."""


class BrowserCapabilityAuthorityUnavailableError(RuntimeError):
    """PostgreSQL could not prove and persist consume before the deadline."""


@dataclass(frozen=True, slots=True)
class BrowserCapabilityConsume:
    browser_contract_version: int
    rpc: str
    operation: str
    session_id: str
    vision_profile_id: str
    ad_account_id: str
    caller: str
    task_id: int
    lease_owner: uuid.UUID
    lease_token: int
    expires_at_epoch: int
    nonce: str

    def validate(self) -> None:
        if self.caller not in _CALLERS or self.rpc not in _RPCS:
            raise BrowserCapabilityConsumeDeniedError(
                "browser capability binding is not authorized"
            )
        if (
            self.browser_contract_version != BROWSER_CONTRACT_VERSION
            or self.task_id <= 0
            or self.lease_token <= 0
            or self.expires_at_epoch <= 0
            or not self.session_id
            or not self.vision_profile_id
            or not self.ad_account_id.isdigit()
            or not self.operation
            or _NONCE_RE.fullmatch(self.nonce) is None
        ):
            raise BrowserCapabilityConsumeDeniedError("browser capability binding is malformed")


async def consume_pending_browser_capability(
    engine: AsyncEngine,
    capability: BrowserCapabilityConsume,
) -> None:
    """Atomically cross the durable browser-send boundary exactly once."""
    capability.validate()
    payload = browser_operation_payload(
        browser_contract_version=capability.browser_contract_version,
        rpc=capability.rpc,
        operation=capability.operation,
        session_id=capability.session_id,
        vision_profile_id=capability.vision_profile_id,
        ad_account_id=capability.ad_account_id,
        caller=capability.caller,
        task_id=capability.task_id,
        lease_owner=capability.lease_owner,
        lease_token=capability.lease_token,
        expires_at_epoch=capability.expires_at_epoch,
        nonce=capability.nonce,
    )
    params = {
        "nonce_sha256": hashlib.sha256(capability.nonce.encode("ascii")).digest(),
        "capability_digest": hashlib.sha256(payload.encode("utf-8")).digest(),
        "operation_digest": hashlib.sha256(capability.operation.encode("utf-8")).digest(),
        "browser_contract_version": capability.browser_contract_version,
        "caller": capability.caller,
        "rpc": capability.rpc,
        "task_id": capability.task_id,
        "lease_owner": capability.lease_owner,
        "lease_token": capability.lease_token,
        "session_id": capability.session_id,
        "vision_profile_id": capability.vision_profile_id,
        "ad_account_id": capability.ad_account_id,
        "expires_at_epoch": capability.expires_at_epoch,
    }
    remaining_seconds = capability.expires_at_epoch - time.time()
    if remaining_seconds <= 0:
        raise BrowserCapabilityConsumeDeniedError(
            "browser capability expired before durable consume"
        )
    db_timeout = max(
        0.001,
        min(_CONSUME_DB_TIMEOUT_SECONDS, remaining_seconds),
    )
    try:
        async with asyncio.timeout(db_timeout):
            async with engine.begin() as conn:
                await conn.execute(
                    _SET_LOCAL_STATEMENT_TIMEOUT_SQL,
                    {"timeout_ms": str(max(1, int(db_timeout * 1000)))},
                )
                result = await conn.execute(
                    _CONSUME_PENDING_CAPABILITY_SQL,
                    params,
                )
                consumed_at = result.scalar_one_or_none()
    except BrowserCapabilityConsumeDeniedError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail closed before browser send
        raise BrowserCapabilityAuthorityUnavailableError(
            "browser capability authority is unavailable"
        ) from exc
    if consumed_at is None:
        raise BrowserCapabilityConsumeDeniedError(
            "browser capability is stale, mismatched, expired, or already consumed"
        )


__all__ = [
    "BrowserCapabilityAuthorityUnavailableError",
    "BrowserCapabilityConsume",
    "BrowserCapabilityConsumeDeniedError",
    "consume_pending_browser_capability",
]
