# -*- coding: utf-8 -*-
"""Durable, lease-fenced Telegram webhook configuration lifecycle.

Bot-token rotation and ``setWebhook`` are one logical control-plane change but
cannot share one database transaction with Telegram.  PostgreSQL therefore
stores a monotonically increasing desired generation.  Workers may repeat the
idempotent Bot API operation after a lost response, while only the holder of
the current generation + lease token may publish the result.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.config import get_settings, reveal_secret
from core.crypto import decrypt
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
    TelegramHTMLGateway,
)
from core.telegram.notifications import open_telegram_auth_incident_in_transaction

WebhookOperation = Literal["configure", "delete"]


@dataclass(frozen=True)
class TelegramWebhookTarget:
    url: str
    secret_token: str
    secret_digest: bytes


@dataclass(frozen=True)
class ClaimedTelegramWebhookConfiguration:
    config_id: uuid.UUID
    generation: int
    operation: WebhookOperation
    desired_url: str | None
    secret_digest: bytes | None
    bot_token_encrypted: str
    attempt_count: int
    lease_token: uuid.UUID


@dataclass(frozen=True)
class TelegramWebhookRemoteSnapshot:
    url: str
    pending_update_count: int
    last_error_at: datetime | None
    last_error_message: str | None
    max_connections: int | None
    allowed_updates: tuple[str, ...]


def webhook_secret_digest(secret_token: str | SecretStr) -> bytes:
    raw = reveal_secret(secret_token).strip()
    if not raw:
        raise ValueError("TELEGRAM_WEBHOOK_SECRET is required")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def resolve_webhook_target(
    *,
    frontend_origin: str,
    secret_token: str | SecretStr,
) -> TelegramWebhookTarget:
    origin = (frontend_origin or "").strip().rstrip("/")
    if not origin.startswith("https://"):
        raise ValueError("FRONTEND_ORIGIN must use HTTPS before webhook configuration")
    secret = reveal_secret(secret_token).strip()
    # TelegramHTMLGateway applies the exact Bot API character/length contract.
    if not secret:
        raise ValueError("TELEGRAM_WEBHOOK_SECRET is required")
    return TelegramWebhookTarget(
        url=f"{origin}/api/v1/integrations/telegram/webhook",
        secret_token=secret,
        secret_digest=webhook_secret_digest(secret),
    )


def bind_webhook_generation(url: str, generation: int) -> str:
    """Bind Telegram's callback origin to one DB token generation."""
    if generation <= 0:
        raise ValueError("webhook generation must be positive")
    if "?" in url or "#" in url:
        raise ValueError("webhook base URL must not contain query or fragment")
    return f"{url}?bot_generation={generation}"


async def store_rotated_token_and_schedule_webhook(
    conn: AsyncConnection,
    *,
    bot_token_encrypted: str,
    bot_token_fingerprint: str,
    target: TelegramWebhookTarget,
) -> None:
    """Atomically make a token authoritative and schedule its webhook."""
    try:
        fingerprint = bytes.fromhex(bot_token_fingerprint)
    except ValueError as exc:
        raise ValueError("bot_token_fingerprint must be SHA-256 hex") from exc
    if len(fingerprint) != 32:
        raise ValueError("bot_token_fingerprint must be SHA-256 hex")
    current = (
        await conn.execute(
            text(
                """
                SELECT webhook_generation
                FROM telegram_config
                WHERE singleton_key = 'default'
                FOR UPDATE
                """
            )
        )
    ).scalar_one_or_none()
    generation = int(current or 0) + 1
    desired_url = bind_webhook_generation(target.url, generation)
    await conn.execute(
        text(
            """
            INSERT INTO telegram_config
                (singleton_key, bot_token_encrypted, bot_token_fingerprint, is_enabled,
                 webhook_generation, webhook_operation, webhook_desired_url,
                 webhook_secret_digest, webhook_state, webhook_scheduled_at,
                 webhook_attempt_count)
            VALUES
                ('default', :bot_token_encrypted, :bot_token_fingerprint, TRUE,
                 :generation, 'configure', :desired_url,
                 :secret_digest, 'pending', NOW(), 0)
            ON CONFLICT (singleton_key) DO UPDATE
            SET bot_token_encrypted = EXCLUDED.bot_token_encrypted,
                bot_token_fingerprint = EXCLUDED.bot_token_fingerprint,
                is_enabled = TRUE,
                webhook_generation = EXCLUDED.webhook_generation,
                webhook_operation = 'configure',
                webhook_desired_url = EXCLUDED.webhook_desired_url,
                webhook_secret_digest = EXCLUDED.webhook_secret_digest,
                webhook_state = 'pending',
                webhook_scheduled_at = NOW(),
                webhook_attempt_count = 0,
                webhook_lease_owner = NULL,
                webhook_lease_token = NULL,
                webhook_lease_expires_at = NULL,
                webhook_configured_at = NULL,
                webhook_last_error_code = NULL,
                webhook_last_error_detail = NULL,
                updated_at = NOW()
            """
        ),
        {
            "bot_token_encrypted": bot_token_encrypted,
            "bot_token_fingerprint": fingerprint,
            "generation": generation,
            "desired_url": desired_url,
            "secret_digest": target.secret_digest,
        },
    )
    # A committed event must not be stranded on the credential generation that
    # lost the config-row race.  Pre-boundary deliveries are safe to rebind;
    # leased rows are first returned to pending so an old worker claim cannot
    # supersede the newly authoritative row.  Rows that crossed the Telegram
    # boundary remain fenced for explicit reconciliation.
    await conn.execute(
        text(
            """
            UPDATE notification_deliveries
            SET bot_generation = :generation,
                state = CASE WHEN state = 'leased' THEN 'pending' ELSE state END,
                scheduled_at = CASE
                    WHEN state = 'leased' THEN LEAST(scheduled_at, NOW())
                    ELSE scheduled_at END,
                lease_owner = CASE WHEN state = 'leased' THEN NULL ELSE lease_owner END,
                lease_token = CASE WHEN state = 'leased' THEN NULL ELSE lease_token END,
                lease_expires_at = CASE
                    WHEN state = 'leased' THEN NULL ELSE lease_expires_at END,
                last_error_code = NULL,
                last_error_detail = NULL,
                updated_at = NOW()
            WHERE bot_generation <> :generation
              AND (
                  state IN ('pending', 'retry')
                  OR (state = 'leased' AND external_started_at IS NULL)
              )
            """
        ),
        {"generation": generation},
    )


async def disable_token_and_schedule_webhook_deletion(
    conn: AsyncConnection,
) -> None:
    """Disable runtime sends immediately and durably remove the remote webhook.

    The encrypted token is retained only while ``deleteWebhook`` is pending, so
    a crash cannot strand an active remote callback.  Successful reconciliation
    clears the ciphertext.  An empty/no-token tombstone is unconfigured
    immediately and still blocks environment bootstrap.
    """
    row = (
        await conn.execute(
            text(
                """
                SELECT id, bot_token_encrypted
                FROM telegram_config
                WHERE singleton_key = 'default'
                FOR UPDATE
                """
            )
        )
    ).first()
    if row is None:
        await conn.execute(
            text(
                """
                INSERT INTO telegram_config
                    (singleton_key, bot_token_encrypted, is_enabled,
                     webhook_state, webhook_generation)
                VALUES ('default', '', FALSE, 'unconfigured', 0)
                """
            )
        )
        return
    if not str(row.bot_token_encrypted or ""):
        await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET is_enabled = FALSE, bot_token_encrypted = '',
                    bot_token_fingerprint = NULL,
                    webhook_operation = NULL,
                    webhook_state = 'unconfigured',
                    webhook_scheduled_at = NULL,
                    webhook_lease_owner = NULL,
                    webhook_lease_token = NULL,
                    webhook_lease_expires_at = NULL,
                    webhook_last_error_code = NULL,
                    webhook_last_error_detail = NULL,
                    updated_at = NOW()
                WHERE id = :config_id
                """
            ),
            {"config_id": row.id},
        )
        return
    await conn.execute(
        text(
            """
            UPDATE telegram_config
            SET is_enabled = FALSE,
                webhook_generation = webhook_generation + 1,
                webhook_operation = 'delete',
                webhook_desired_url = NULL,
                webhook_secret_digest = NULL,
                webhook_state = 'pending',
                webhook_scheduled_at = NOW(),
                webhook_attempt_count = 0,
                webhook_lease_owner = NULL,
                webhook_lease_token = NULL,
                webhook_lease_expires_at = NULL,
                webhook_configured_at = NULL,
                webhook_last_error_code = NULL,
                webhook_last_error_detail = NULL,
                updated_at = NOW()
            WHERE id = :config_id
            """
        ),
        {"config_id": row.id},
    )


async def ensure_webhook_configuration_desired(
    engine: AsyncEngine,
    *,
    target: TelegramWebhookTarget,
    force: bool = False,
) -> bool:
    """Schedule the current DB token when release/env target drift is detected."""
    async with engine.begin() as conn:
        current = (
            await conn.execute(
                text(
                    """
                    SELECT id, webhook_generation, webhook_operation,
                           webhook_desired_url, webhook_secret_digest,
                           webhook_state, webhook_applied_generation
                    FROM telegram_config
                    WHERE singleton_key = 'default'
                      AND is_enabled
                      AND bot_token_encrypted <> ''
                    FOR UPDATE
                    """
                )
            )
        ).first()
        if current is None:
            return False
        current_expected_url = bind_webhook_generation(target.url, int(current.webhook_generation))
        needs_change = bool(
            force
            or current.webhook_operation != "configure"
            or current.webhook_desired_url != current_expected_url
            or current.webhook_secret_digest != target.secret_digest
            or current.webhook_state in {"unconfigured", "failed"}
            or (
                current.webhook_state == "configured"
                and current.webhook_applied_generation != current.webhook_generation
            )
        )
        if not needs_change:
            return False
        generation = int(current.webhook_generation) + 1
        result = await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET webhook_generation = :generation,
                    webhook_operation = 'configure',
                    webhook_desired_url = :desired_url,
                    webhook_secret_digest = :secret_digest,
                    webhook_state = 'pending',
                    webhook_scheduled_at = NOW(),
                    webhook_attempt_count = 0,
                    webhook_lease_owner = NULL,
                    webhook_lease_token = NULL,
                    webhook_lease_expires_at = NULL,
                    webhook_configured_at = NULL,
                    webhook_last_error_code = NULL,
                    webhook_last_error_detail = NULL,
                    updated_at = NOW()
                WHERE singleton_key = 'default'
                  AND is_enabled
                  AND bot_token_encrypted <> ''
                  AND id = :config_id
                """
            ),
            {
                "config_id": current.id,
                "generation": generation,
                "desired_url": bind_webhook_generation(target.url, generation),
                "secret_digest": target.secret_digest,
            },
        )
    return (result.rowcount or 0) > 0


async def claim_webhook_configuration(
    engine: AsyncEngine,
    *,
    worker_id: str,
    lease_seconds: int = 60,
) -> ClaimedTelegramWebhookConfiguration | None:
    if not worker_id or lease_seconds < 5:
        raise ValueError("worker_id and lease_seconds>=5 are required")
    lease_token = uuid.uuid4()
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    WITH candidate AS (
                        SELECT id
                        FROM telegram_config
                        WHERE bot_token_encrypted <> ''
                          AND (
                                (
                                    webhook_state IN ('pending','retry')
                                    AND webhook_scheduled_at <= NOW()
                                )
                                OR (
                                    webhook_state = 'applying'
                                    AND webhook_lease_expires_at <= NOW()
                                )
                          )
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE telegram_config c
                    SET webhook_state = 'applying',
                        webhook_lease_owner = :worker_id,
                        webhook_lease_token = :lease_token,
                        webhook_lease_expires_at =
                            NOW() + make_interval(secs => :lease_seconds),
                        webhook_attempt_count = c.webhook_attempt_count + 1,
                        updated_at = NOW()
                    FROM candidate
                    WHERE c.id = candidate.id
                    RETURNING c.id, c.webhook_generation, c.webhook_operation,
                              c.webhook_desired_url, c.webhook_secret_digest,
                              c.bot_token_encrypted, c.webhook_attempt_count,
                              c.webhook_lease_token
                    """
                ),
                {
                    "worker_id": worker_id[:96],
                    "lease_token": lease_token,
                    "lease_seconds": int(lease_seconds),
                },
            )
        ).first()
    if row is None:
        return None
    operation = str(row.webhook_operation or "")
    if operation not in {"configure", "delete"}:
        # The row is now fenced; the processor will persist a deterministic
        # failure rather than letting malformed state spin.
        operation = "configure"
    return ClaimedTelegramWebhookConfiguration(
        config_id=uuid.UUID(str(row.id)),
        generation=int(row.webhook_generation),
        operation=operation,  # type: ignore[arg-type]
        desired_url=str(row.webhook_desired_url) if row.webhook_desired_url else None,
        secret_digest=(
            bytes(row.webhook_secret_digest) if row.webhook_secret_digest is not None else None
        ),
        bot_token_encrypted=str(row.bot_token_encrypted),
        attempt_count=int(row.webhook_attempt_count),
        lease_token=uuid.UUID(str(row.webhook_lease_token)),
    )


@asynccontextmanager
async def hold_webhook_configuration_authority(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramWebhookConfiguration,
) -> AsyncIterator[bool]:
    """Hold the exact config-operation lease through its Bot API calls.

    A token rotation or DELETE takes ``FOR UPDATE`` on the singleton row.  It
    therefore either wins before this lock (the stale operation makes zero
    calls) or waits until the current set/delete/getWebhookInfo sequence ends.
    Result publication remains a separate exact-generation CAS.
    """
    async with engine.begin() as conn:
        authorized = await conn.scalar(
            text(
                """
                SELECT 1
                FROM telegram_config
                WHERE id = :config_id
                  AND webhook_generation = :generation
                  AND webhook_operation = :operation
                  AND webhook_state = 'applying'
                  AND webhook_lease_token = :lease_token
                  AND webhook_lease_expires_at > clock_timestamp()
                  AND bot_token_encrypted = :bot_token_encrypted
                FOR SHARE
                """
            ),
            {
                "config_id": claim.config_id,
                "generation": claim.generation,
                "operation": claim.operation,
                "lease_token": claim.lease_token,
                "bot_token_encrypted": claim.bot_token_encrypted,
            },
        )
        yield authorized is not None


def _nonnegative_int(value: Any, *, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _optional_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def parse_remote_webhook_snapshot(info: dict[str, Any]) -> TelegramWebhookRemoteSnapshot:
    raw_error_date = info.get("last_error_date")
    last_error_at: datetime | None = None
    if raw_error_date is not None:
        try:
            last_error_at = datetime.fromtimestamp(int(raw_error_date), tz=UTC)
        except (OSError, OverflowError, TypeError, ValueError):
            last_error_at = None
    raw_allowed = info.get("allowed_updates")
    allowed_updates = (
        tuple(str(item)[:64] for item in raw_allowed if isinstance(item, str))
        if isinstance(raw_allowed, list)
        else ()
    )
    raw_message = info.get("last_error_message")
    return TelegramWebhookRemoteSnapshot(
        url=str(info.get("url") or "")[:2048],
        pending_update_count=_nonnegative_int(info.get("pending_update_count")),
        last_error_at=last_error_at,
        last_error_message=(str(raw_message)[:500] if raw_message else None),
        max_connections=_optional_positive_int(info.get("max_connections")),
        allowed_updates=allowed_updates,
    )


def _remote_params(snapshot: TelegramWebhookRemoteSnapshot | None) -> dict[str, Any]:
    return {
        "remote_url": snapshot.url if snapshot is not None else None,
        "remote_pending": (snapshot.pending_update_count if snapshot is not None else None),
        "remote_last_error_at": (snapshot.last_error_at if snapshot is not None else None),
        "remote_last_error_message": (
            snapshot.last_error_message if snapshot is not None else None
        ),
        "remote_max_connections": (snapshot.max_connections if snapshot is not None else None),
        "remote_allowed_updates": (
            json.dumps(snapshot.allowed_updates, ensure_ascii=True)
            if snapshot is not None
            else None
        ),
    }


async def mark_webhook_configuration_success(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramWebhookConfiguration,
    remote: TelegramWebhookRemoteSnapshot,
) -> bool:
    expected_url = claim.desired_url or ""
    if remote.url != expected_url:
        raise ValueError("remote webhook URL does not match the claimed generation")
    params = _remote_params(remote)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET webhook_state = CASE
                        WHEN :operation = 'configure'
                        THEN 'configured' ELSE 'unconfigured' END,
                    webhook_applied_generation = :generation,
                    webhook_scheduled_at = NULL,
                    webhook_lease_owner = NULL,
                    webhook_lease_token = NULL,
                    webhook_lease_expires_at = NULL,
                    webhook_remote_url = :remote_url,
                    webhook_remote_pending_update_count = :remote_pending,
                    webhook_remote_last_error_at = :remote_last_error_at,
                    webhook_remote_last_error_message =
                        :remote_last_error_message,
                    webhook_remote_max_connections =
                        :remote_max_connections,
                    webhook_remote_allowed_updates =
                        CAST(:remote_allowed_updates AS JSONB),
                    webhook_checked_at = NOW(),
                    webhook_configured_at = CASE
                        WHEN :operation = 'configure' THEN NOW() ELSE NULL END,
                    webhook_last_error_code = NULL,
                    webhook_last_error_detail = NULL,
                    bot_token_encrypted = CASE
                        WHEN :operation = 'delete' THEN ''
                        ELSE bot_token_encrypted END,
                    bot_token_fingerprint = CASE
                        WHEN :operation = 'delete' THEN NULL
                        ELSE bot_token_fingerprint END,
                    updated_at = NOW()
                WHERE id = :config_id
                  AND webhook_generation = :generation
                  AND webhook_state = 'applying'
                  AND webhook_lease_token = :lease_token
                """
            ),
            {
                **params,
                "operation": claim.operation,
                "generation": claim.generation,
                "config_id": claim.config_id,
                "lease_token": claim.lease_token,
            },
        )
    return (result.rowcount or 0) > 0


async def mark_webhook_configuration_failure(
    engine: AsyncEngine,
    *,
    claim: ClaimedTelegramWebhookConfiguration,
    error_code: str,
    error_detail: str,
    retry_after: float | None,
    retryable: bool,
    remote: TelegramWebhookRemoteSnapshot | None = None,
    authentication_failure: bool = False,
    credential_fingerprint: str | None = None,
) -> bool:
    delay = (
        max(0.0, float(retry_after))
        if retry_after is not None
        else min(5.0 * (2 ** max(0, claim.attempt_count - 1)), 900.0)
    )
    params = _remote_params(remote)
    async with engine.begin() as conn:
        if authentication_failure:
            # Auth incident enqueue serializes recipients before taking the
            # config FOR SHARE lock.  Preserve that global order, then use the
            # exact claim CAS below to decide whether the provisional incident
            # belongs to this credential generation.
            await open_telegram_auth_incident_in_transaction(
                conn,
                error_code=error_code[:64],
                credential_fingerprint=credential_fingerprint,
                source="webhook_configuration",
            )
        result = await conn.execute(
            text(
                """
                UPDATE telegram_config
                SET webhook_state = CASE WHEN :retryable THEN 'retry' ELSE 'failed' END,
                    webhook_scheduled_at = CASE
                        WHEN :retryable
                        THEN NOW() + make_interval(secs => :delay)
                        ELSE NULL END,
                    webhook_lease_owner = NULL,
                    webhook_lease_token = NULL,
                    webhook_lease_expires_at = NULL,
                    webhook_remote_url =
                        COALESCE(:remote_url, webhook_remote_url),
                    webhook_remote_pending_update_count =
                        COALESCE(:remote_pending,
                                 webhook_remote_pending_update_count),
                    webhook_remote_last_error_at =
                        COALESCE(:remote_last_error_at,
                                 webhook_remote_last_error_at),
                    webhook_remote_last_error_message =
                        COALESCE(:remote_last_error_message,
                                 webhook_remote_last_error_message),
                    webhook_remote_max_connections =
                        COALESCE(:remote_max_connections,
                                 webhook_remote_max_connections),
                    webhook_remote_allowed_updates = CASE
                        WHEN CAST(:remote_allowed_updates AS JSONB) IS NULL
                        THEN webhook_remote_allowed_updates
                        ELSE CAST(:remote_allowed_updates AS JSONB) END,
                    webhook_checked_at = CASE
                        WHEN :remote_url IS NULL THEN webhook_checked_at
                        ELSE NOW() END,
                    webhook_last_error_code = :error_code,
                    webhook_last_error_detail = :error_detail,
                    updated_at = NOW()
                WHERE id = :config_id
                  AND webhook_generation = :generation
                  AND webhook_operation = :operation
                  AND webhook_state = 'applying'
                  AND webhook_lease_token = :lease_token
                  AND bot_token_encrypted = :bot_token_encrypted
                """
            ),
            {
                **params,
                "retryable": retryable,
                "delay": delay,
                "error_code": error_code[:64],
                "error_detail": error_detail[:500],
                "config_id": claim.config_id,
                "generation": claim.generation,
                "operation": claim.operation,
                "lease_token": claim.lease_token,
                "bot_token_encrypted": claim.bot_token_encrypted,
            },
        )
        if (result.rowcount or 0) <= 0:
            if authentication_failure:
                await conn.rollback()
            return False
    return True


async def process_one_webhook_configuration(
    engine: AsyncEngine,
    *,
    worker_id: str,
) -> bool:
    """Apply at most one desired generation and persist every outcome."""
    claim = await claim_webhook_configuration(engine, worker_id=worker_id)
    if claim is None:
        return False
    try:
        token = decrypt(claim.bot_token_encrypted).strip()
    except Exception:
        await mark_webhook_configuration_failure(
            engine,
            claim=claim,
            error_code="credential_decrypt_failed",
            error_detail="Stored Telegram bot token could not be decrypted",
            retry_after=None,
            retryable=False,
        )
        return True
    if not token:
        await mark_webhook_configuration_failure(
            engine,
            claim=claim,
            error_code="credential_missing",
            error_detail="Stored Telegram bot token is empty",
            retry_after=None,
            retryable=False,
        )
        return True

    settings = get_settings()
    if claim.operation == "configure":
        try:
            target = resolve_webhook_target(
                frontend_origin=settings.frontend_origin,
                secret_token=settings.telegram_webhook_secret,
            )
        except ValueError:
            await mark_webhook_configuration_failure(
                engine,
                claim=claim,
                error_code="webhook_target_unavailable",
                error_detail="Webhook URL or secret is not configured",
                retry_after=None,
                retryable=False,
            )
            return True
        desired_target_url = bind_webhook_generation(target.url, claim.generation)
        if desired_target_url != claim.desired_url or target.secret_digest != claim.secret_digest:
            await mark_webhook_configuration_failure(
                engine,
                claim=claim,
                error_code="webhook_target_revision_mismatch",
                error_detail="Runtime webhook target differs from the claimed generation",
                retry_after=None,
                retryable=False,
            )
            return True
    else:
        target = None

    gateway = TelegramHTMLGateway(token)
    remote: TelegramWebhookRemoteSnapshot | None = None
    try:
        async with hold_webhook_configuration_authority(
            engine,
            claim=claim,
        ) as authorized:
            if not authorized:
                return True
            if claim.operation == "configure":
                assert target is not None
                await gateway.set_webhook(
                    url=desired_target_url,
                    secret_token=target.secret_token,
                    drop_pending_updates=False,
                )
            else:
                await gateway.delete_webhook(drop_pending_updates=False)
            remote = parse_remote_webhook_snapshot(await gateway.get_webhook_info())
        expected_url = claim.desired_url or ""
        if remote.url != expected_url:
            await mark_webhook_configuration_failure(
                engine,
                claim=claim,
                error_code="remote_webhook_url_mismatch",
                error_detail="Telegram reported a different webhook URL",
                retry_after=None,
                retryable=True,
                remote=remote,
            )
            return True
        await mark_webhook_configuration_success(
            engine,
            claim=claim,
            remote=remote,
        )
    except TelegramGatewayError as exc:
        retryable = exc.kind in {
            TelegramFailureKind.RATE_LIMITED,
            TelegramFailureKind.TRANSIENT,
            TelegramFailureKind.UNKNOWN,
        }
        await mark_webhook_configuration_failure(
            engine,
            claim=claim,
            error_code=f"telegram_{exc.kind.value}",
            error_detail=exc.description or exc.kind.value,
            retry_after=exc.retry_after,
            retryable=retryable,
            remote=remote,
            authentication_failure=exc.kind is TelegramFailureKind.UNAUTHORIZED,
            credential_fingerprint=gateway.credential_fingerprint,
        )
    finally:
        await gateway.close()
    return True


__all__ = [
    "ClaimedTelegramWebhookConfiguration",
    "TelegramWebhookRemoteSnapshot",
    "TelegramWebhookTarget",
    "bind_webhook_generation",
    "claim_webhook_configuration",
    "disable_token_and_schedule_webhook_deletion",
    "ensure_webhook_configuration_desired",
    "hold_webhook_configuration_authority",
    "mark_webhook_configuration_failure",
    "mark_webhook_configuration_success",
    "parse_remote_webhook_snapshot",
    "process_one_webhook_configuration",
    "resolve_webhook_target",
    "store_rotated_token_and_schedule_webhook",
    "webhook_secret_digest",
]
