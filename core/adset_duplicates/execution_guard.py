"""DB-backed execution guard for irreversible ad-set duplication tasks."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.adset_duplicates.plan_integrity import (
    DUPLICATE_ADSET_STRUCTURE_KIND,
    duplicate_execution_plan_digest,
    duplicate_execution_plan_digest_matches,
)
from core.meta_api.identity import require_ad_account_id

_TASK_PAYLOAD_KEYS = {
    "mutation_kind",
    "target_id",
    "params",
    "ad_account_id",
}


class DuplicateExecutionReceiptError(ValueError):
    """The queued create plan is not backed by one coherent durable receipt."""


def _canonical_payload_digest(payload: object) -> bytes:
    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DuplicateExecutionReceiptError("task payload is not canonical JSON") from exc
    return hashlib.sha256(canonical).digest()


def _validated_anchor(
    *,
    task_payload: object,
    stored_plan_digest: object,
) -> tuple[bytes, bytes]:
    if not isinstance(task_payload, dict) or set(task_payload) != _TASK_PAYLOAD_KEYS:
        raise DuplicateExecutionReceiptError("receipt task payload shape is invalid")
    params = task_payload.get("params")
    if not isinstance(params, dict):
        raise DuplicateExecutionReceiptError("receipt params are invalid")
    if (
        task_payload.get("mutation_kind") != DUPLICATE_ADSET_STRUCTURE_KIND
        or not isinstance(task_payload.get("target_id"), str)
        or params.get("source_adset_id") != task_payload.get("target_id")
    ):
        raise DuplicateExecutionReceiptError("receipt execution identity is invalid")
    try:
        canonical_account_id = require_ad_account_id(task_payload.get("ad_account_id"))
        recomputed = duplicate_execution_plan_digest(
            mutation_kind=task_payload["mutation_kind"],
            target_id=task_payload["target_id"],
            params=params,
            ad_account_id=task_payload["ad_account_id"],
        )
        embedded_matches = duplicate_execution_plan_digest_matches(
            mutation_kind=task_payload["mutation_kind"],
            target_id=task_payload["target_id"],
            params=params,
            ad_account_id=task_payload["ad_account_id"],
            plan_digest=params.get("plan_digest"),
        )
        anchored_digest = bytes(stored_plan_digest)
    except (TypeError, ValueError) as exc:
        raise DuplicateExecutionReceiptError("receipt digest is malformed") from exc
    if canonical_account_id != task_payload["ad_account_id"]:
        raise DuplicateExecutionReceiptError("receipt account id is not canonical")
    if (
        not embedded_matches
        or len(anchored_digest) != hashlib.sha256().digest_size
        or not secrets.compare_digest(recomputed, anchored_digest)
    ):
        raise DuplicateExecutionReceiptError("receipt digest does not match its plan")
    return _canonical_payload_digest(task_payload), anchored_digest


def _json_object(value: object, *, label: str) -> dict[str, Any]:
    try:
        decoded = value if isinstance(value, dict) else json.loads(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DuplicateExecutionReceiptError(f"{label} is not a JSON object") from exc
    if not isinstance(decoded, dict):
        raise DuplicateExecutionReceiptError(f"{label} is not a JSON object")
    return decoded


def _verify_receipt_rows(
    rows: list[Any],
    *,
    task_payload: dict[str, Any],
    requested_by: str,
    require_consumed: bool,
) -> None:
    """Validate every receipt and bind them to one exact task authority."""
    if not rows:
        raise DuplicateExecutionReceiptError("duplicate task has no durable receipt")
    if not isinstance(requested_by, str) or not requested_by:
        raise DuplicateExecutionReceiptError("queued task principal is invalid")

    live_payload_digest = _canonical_payload_digest(task_payload)
    expected_payload_digest: bytes | None = None
    expected_plan_digest: bytes | None = None
    expected_principal: bytes | None = None

    for row in rows:
        if require_consumed and not isinstance(row.get("consumed_at"), datetime):
            raise DuplicateExecutionReceiptError("duplicate receipt is not consumed")
        receipt_payload = _json_object(
            row.get("task_payload"),
            label="receipt task payload",
        )
        receipt_payload_digest, receipt_plan_digest = _validated_anchor(
            task_payload=receipt_payload,
            stored_plan_digest=row.get("plan_digest"),
        )
        principal = row.get("principal")
        if not isinstance(principal, str) or not principal:
            raise DuplicateExecutionReceiptError("receipt principal is invalid")
        principal_bytes = principal.encode("utf-8")
        if expected_payload_digest is None:
            expected_payload_digest = receipt_payload_digest
            expected_plan_digest = receipt_plan_digest
            expected_principal = principal_bytes
            continue
        if (
            not secrets.compare_digest(expected_payload_digest, receipt_payload_digest)
            or expected_plan_digest is None
            or not secrets.compare_digest(expected_plan_digest, receipt_plan_digest)
            or expected_principal is None
            or not secrets.compare_digest(expected_principal, principal_bytes)
        ):
            raise DuplicateExecutionReceiptError("duplicate task receipts conflict")

    if (
        expected_payload_digest is None
        or not secrets.compare_digest(expected_payload_digest, live_payload_digest)
        or expected_principal is None
        or not secrets.compare_digest(expected_principal, requested_by.encode("utf-8"))
    ):
        raise DuplicateExecutionReceiptError("queued task does not match durable receipt")


async def authorize_duplicate_execution_boundary(
    engine: AsyncEngine,
    *,
    task_id: int,
    task_payload: dict[str, Any],
    requested_by: str,
    target_lock_key: str,
    lease_owner: uuid.UUID | None,
    lease_token: int | None,
    recovery_checkpoint: dict[str, Any] | None = None,
) -> bool:
    """Atomically validate durable authority and cross one external boundary.

    The receipt rows are locked before the task row to preserve the same lock
    ordering used by preview consumption.  The authoritative database payload,
    principal and every immutable receipt must all match the worker's claimed
    snapshot.  A normal create is authorized only while ``external_started_at``
    is NULL.  A PAUSE-only recovery must present the exact persisted recovery
    checkpoint and an already-crossed boundary, so it can never replay create.
    """
    if (
        lease_owner is None
        or not isinstance(lease_owner, uuid.UUID)
        or lease_token is None
        or isinstance(lease_token, bool)
        or int(lease_token) <= 0
    ):
        return False

    supplied_payload = _json_object(task_payload, label="claimed task payload")
    supplied_target = supplied_payload.get("target_id")
    if (
        not isinstance(target_lock_key, str)
        or not target_lock_key
        or not isinstance(supplied_target, str)
        or not secrets.compare_digest(
            target_lock_key.encode("utf-8"),
            supplied_target.encode("utf-8"),
        )
    ):
        raise DuplicateExecutionReceiptError(
            "advisory lock target does not match claimed task target"
        )
    supplied_payload_digest = _canonical_payload_digest(supplied_payload)
    supplied_principal = requested_by.encode("utf-8") if isinstance(requested_by, str) else b""
    if not supplied_principal:
        raise DuplicateExecutionReceiptError("claimed task principal is invalid")

    is_recovery = recovery_checkpoint is not None
    supplied_checkpoint: dict[str, Any] | None = None
    checkpoint_json: str | None = None
    if is_recovery:
        supplied_checkpoint = _json_object(
            recovery_checkpoint,
            label="claimed recovery checkpoint",
        )
        if (
            supplied_checkpoint.get("checkpoint_type") != DUPLICATE_ADSET_STRUCTURE_KIND
            or supplied_checkpoint.get("recovery_requested") is not True
        ):
            raise DuplicateExecutionReceiptError("recovery checkpoint is not authoritative")
        checkpoint_json = json.dumps(
            supplied_checkpoint,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    async with engine.begin() as conn:
        # Keep ordering compatible with the generic tracker/task boundary.
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": target_lock_key},
        )
        receipt_rows = (
            (
                await conn.execute(
                    text(
                        """
                        SELECT principal, task_payload, plan_digest, consumed_at
                        FROM adset_duplicate_previews
                        WHERE task_id = :task_id
                        ORDER BY token_digest
                        FOR UPDATE
                        """
                    ),
                    {"task_id": int(task_id)},
                )
            )
            .mappings()
            .all()
        )
        task_row = (
            (
                await conn.execute(
                    text(
                        """
                    SELECT payload, requested_by, result, external_started_at
                    FROM task_queue
                    WHERE id = :task_id
                      AND task_type = 'meta_api_mutation'
                      AND status = 'running'
                      AND lease_owner = :lease_owner
                      AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                      AND cancel_requested_at IS NULL
                      AND (deadline_at IS NULL OR deadline_at > clock_timestamp())
                    FOR UPDATE
                    """
                    ),
                    {
                        "task_id": int(task_id),
                        "lease_owner": lease_owner,
                        "lease_token": int(lease_token),
                    },
                )
            )
            .mappings()
            .first()
        )
        if task_row is None:
            return False

        authoritative_payload = _json_object(
            task_row.get("payload"),
            label="authoritative task payload",
        )
        authoritative_principal = task_row.get("requested_by")
        if not isinstance(authoritative_principal, str) or not authoritative_principal:
            raise DuplicateExecutionReceiptError("authoritative task principal is invalid")
        if not secrets.compare_digest(
            _canonical_payload_digest(authoritative_payload),
            supplied_payload_digest,
        ) or not secrets.compare_digest(
            authoritative_principal.encode("utf-8"),
            supplied_principal,
        ):
            raise DuplicateExecutionReceiptError(
                "claimed task does not match authoritative task row"
            )

        _verify_receipt_rows(
            receipt_rows,
            task_payload=authoritative_payload,
            requested_by=authoritative_principal,
            require_consumed=True,
        )

        external_started_at = task_row.get("external_started_at")
        if is_recovery:
            authoritative_checkpoint = _json_object(
                task_row.get("result"),
                label="authoritative recovery checkpoint",
            )
            if (
                external_started_at is None
                or supplied_checkpoint is None
                or not secrets.compare_digest(
                    _canonical_payload_digest(authoritative_checkpoint),
                    _canonical_payload_digest(supplied_checkpoint),
                )
            ):
                raise DuplicateExecutionReceiptError(
                    "claimed recovery does not match authoritative checkpoint"
                )
            result = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET updated_at = clock_timestamp()
                    WHERE id = :task_id
                      AND status = 'running'
                      AND lease_owner = :lease_owner
                      AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                      AND cancel_requested_at IS NULL
                      AND (deadline_at IS NULL OR deadline_at > clock_timestamp())
                      AND external_started_at IS NOT NULL
                      AND result = CAST(:checkpoint AS JSONB)
                    """
                ),
                {
                    "task_id": int(task_id),
                    "lease_owner": lease_owner,
                    "lease_token": int(lease_token),
                    "checkpoint": checkpoint_json,
                },
            )
        else:
            if external_started_at is not None:
                return False
            if task_row.get("result") is not None:
                raise DuplicateExecutionReceiptError(
                    "create task has an unexpected durable checkpoint"
                )
            result = await conn.execute(
                text(
                    """
                    UPDATE task_queue
                    SET external_started_at = clock_timestamp(),
                        updated_at = clock_timestamp()
                    WHERE id = :task_id
                      AND status = 'running'
                      AND lease_owner = :lease_owner
                      AND lease_token = :lease_token
                      AND lease_expires_at > clock_timestamp()
                      AND cancel_requested_at IS NULL
                      AND (deadline_at IS NULL OR deadline_at > clock_timestamp())
                      AND external_started_at IS NULL
                    """
                ),
                {
                    "task_id": int(task_id),
                    "lease_owner": lease_owner,
                    "lease_token": int(lease_token),
                },
            )
    return (result.rowcount or 0) > 0


__all__ = [
    "DuplicateExecutionReceiptError",
    "authorize_duplicate_execution_boundary",
]
