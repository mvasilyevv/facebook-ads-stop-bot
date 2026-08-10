"""Durable command lifecycle for campaign-run abort and safe resume.

Campaign creation is irreversible after the first Meta boundary.  This module
therefore treats ``task_queue`` as the cancellation authority and permits a
resume only from a proven pre-boundary terminal checkpoint.  Client
idempotency keys are bound through ``command_idempotency_receipts`` so a lost
HTTP response cannot create a second campaign task.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.campaign_builder.config import CampaignConfig
from core.tasks.queue import (
    create_task,
    transition_correlated_incident_in_transaction,
)

CampaignRunControlAction = Literal["abort", "resume"]
CampaignRunControlState = Literal[
    "queued",
    "running",
    "confirmed",
    "failed",
    "cancelled",
    "unknown",
]

_RECEIPT_ACTION = {
    "abort": "abort_campaign_run",
    "resume": "resume_campaign_run",
}
_ACTIVE_RUN_STATUSES = frozenset({"queued", "uniquifying", "uploading", "creating"})
_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_ACTIVE_TASK_STATUSES = frozenset({"pending", "retrying", "running"})
_TERMINAL_TASK_STATUSES = frozenset({"succeeded", "failed", "cancelled"})
_RESUMABLE_REASONS = frozenset(
    {
        "absolute_deadline_exceeded_before_external_call",
        "campaign_run_abort_before_execution",
        "cancel_requested_before_external_call",
        "creator_dependencies_unavailable",
        "pre_external_attempts_exhausted",
        "run_cancelled_before_external_call",
    }
)


class CampaignRunNotFoundError(LookupError):
    """The requested campaign run does not exist."""


class CampaignRunIdempotencyConflictError(RuntimeError):
    """The supplied key is already bound to different command semantics."""


class CampaignRunControlUnavailableError(RuntimeError):
    """The requested control is unsafe or no longer applicable."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class CampaignRunCommandReceipt:
    action: CampaignRunControlAction
    run_id: uuid.UUID
    task_id: int
    state: CampaignRunControlState
    run_status: str
    created: bool
    correlation_id: uuid.UUID
    reason: str


@dataclass(frozen=True, slots=True)
class CampaignRunControlOption:
    available: bool
    reason: str


@dataclass(frozen=True, slots=True)
class CampaignRunControls:
    abort: CampaignRunControlOption
    resume: CampaignRunControlOption


def _mapping(row: Any) -> Mapping[str, Any]:
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping
    if isinstance(row, Mapping):
        return row
    raise TypeError("campaign lifecycle query must return a named row")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _contains_created_object(value: Any) -> bool:
    """Fail closed when a created-object checkpoint contains any value."""
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_contains_created_object(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_created_object(item) for item in value)
    if isinstance(value, str):
        return bool(value.strip())
    return bool(value)


def campaign_task_state(*, status: str, result: Mapping[str, Any]) -> CampaignRunControlState:
    outcome = str(result.get("outcome") or "").upper()
    if outcome == "UNKNOWN" or result.get("reconcile_required") is True:
        return "unknown"
    if status in {"pending", "retrying"}:
        return "queued"
    if status == "running":
        return "running"
    if status == "succeeded":
        # Queue status is transport state, not proof that Meta confirmed the
        # irreversible result.  Missing or contradictory terminal evidence
        # must remain visible as UNKNOWN instead of producing a false green.
        return "confirmed" if outcome == "CONFIRMED" else "unknown"
    if status == "cancelled":
        return "cancelled"
    return "failed"


def _abort_state(*, status: str, result: Mapping[str, Any]) -> CampaignRunControlState:
    """Map the target task to the abort command's own lifecycle."""
    outcome = str(result.get("outcome") or "").upper()
    if outcome == "UNKNOWN" or result.get("reconcile_required") is True:
        return "unknown"
    if status in {"pending", "retrying", "running"}:
        return "running"
    if status == "cancelled":
        # A cancelled queue row is only a confirmed pre-boundary abort when
        # its durable terminal evidence says the external operation was
        # rejected.  Legacy/partial rows without that proof are UNKNOWN.
        return "confirmed" if outcome == "REJECTED" else "unknown"
    # A succeeded campaign task means the cooperative abort lost the race.
    return "failed"


def _abort_receipt_reason(
    *,
    status: str,
    result: Mapping[str, Any],
    external_started_at: Any = None,
) -> str:
    state = _abort_state(status=status, result=result)
    if state == "running":
        return (
            "cooperative_abort_requested_after_external_boundary"
            if external_started_at is not None
            else "cooperative_abort_requested"
        )
    if state == "confirmed":
        return "aborted_before_external_boundary"
    if state == "unknown":
        return "abort_result_unknown_after_external_boundary"
    if status == "succeeded":
        return "abort_not_applied_campaign_completed"
    return "abort_not_applied"


def _campaign_upload_root() -> Path:
    configured = os.environ.get("CAMPAIGN_UPLOAD_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path.home() / "Documents" / "FB_Agent_Campaign_Uploads"


def _resume_source_unavailable_reason(config: Mapping[str, Any]) -> str | None:
    try:
        campaign_config = CampaignConfig.model_validate(dict(config))
    except Exception:  # noqa: BLE001 - public contract exposes a stable reason only
        return "invalid_config_checkpoint"

    upload_id = str(campaign_config.creo_root or "").strip()
    if not upload_id or Path(upload_id).name != upload_id or upload_id in {".", ".."}:
        return "invalid_media_checkpoint"
    upload_dir = _campaign_upload_root() / upload_id
    if not upload_dir.is_dir():
        return "media_checkpoint_missing"

    for campaign in campaign_config.campaigns:
        if not campaign.concept_refs:
            return "media_checkpoint_empty"
        for ref in campaign.concept_refs:
            if not ref or Path(ref).name != ref:
                return "invalid_media_checkpoint"
            if not (upload_dir / ref).is_file():
                return "media_checkpoint_incomplete"
    return None


def resume_unavailable_reason(
    *,
    run_status: str,
    run_config: Mapping[str, Any],
    created_meta_ids: Mapping[str, Any],
    task: Mapping[str, Any] | None,
) -> str | None:
    """Return ``None`` only for a provable pre-boundary restart checkpoint."""
    if run_status not in {"failed", "cancelled"}:
        return "run_already_succeeded" if run_status == "succeeded" else "run_not_terminal"
    if task is None:
        return "campaign_task_missing"

    task_status = str(task.get("task_status") or task.get("status") or "")
    if task_status not in {"failed", "cancelled"}:
        return "campaign_task_not_terminal"
    if task.get("external_started_at") is not None:
        return "external_boundary_crossed"
    if _contains_created_object(created_meta_ids):
        return "created_meta_objects_present"

    result = _json_object(task.get("task_result", task.get("result")))
    if (
        str(result.get("outcome") or "").upper() != "REJECTED"
        or result.get("reconcile_required") is True
    ):
        return "terminal_outcome_not_rejected"
    if _contains_created_object(result.get("created_ids")):
        return "created_meta_objects_present"
    if str(result.get("reason") or "") not in _RESUMABLE_REASONS:
        return "checkpoint_reason_not_resumable"

    return _resume_source_unavailable_reason(run_config)


def campaign_run_controls(
    *,
    run_status: str,
    run_config: Mapping[str, Any],
    created_meta_ids: Mapping[str, Any],
    task: Mapping[str, Any] | None,
) -> CampaignRunControls:
    """Build deterministic button availability for web and TMA."""
    if task is None:
        abort_reason = "campaign_task_missing"
    else:
        task_status = str(task.get("task_status") or task.get("status") or "")
        if task.get("cancel_requested_at") is not None and task_status in _ACTIVE_TASK_STATUSES:
            abort_reason = "abort_already_requested"
        elif (
            task_status in {"pending", "retrying"}
            and run_status == "queued"
            and task.get("external_started_at") is None
        ):
            abort_reason = "abort_available"
        elif task_status == "running" and run_status in _ACTIVE_RUN_STATUSES:
            abort_reason = "abort_available"
        elif run_status == "cancelled":
            abort_reason = "run_already_cancelled"
        elif run_status == "succeeded":
            abort_reason = "run_already_succeeded"
        elif run_status == "failed":
            abort_reason = "run_already_failed"
        else:
            abort_reason = "run_task_state_inconsistent"

    resume_reason = resume_unavailable_reason(
        run_status=run_status,
        run_config=run_config,
        created_meta_ids=created_meta_ids,
        task=task,
    )
    return CampaignRunControls(
        abort=CampaignRunControlOption(
            available=abort_reason == "abort_available",
            reason=abort_reason,
        ),
        resume=CampaignRunControlOption(
            available=resume_reason is None,
            reason=resume_reason or "pre_external_checkpoint_available",
        ),
    )


async def _lock_idempotency_key(
    conn: AsyncConnection,
    *,
    idempotency_key: str,
) -> None:
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(1129270861, hashtext(:idempotency_key))"),
        {"idempotency_key": idempotency_key},
    )


async def _load_bound_receipt(
    conn: AsyncConnection,
    *,
    idempotency_key: str,
) -> Any | None:
    return (
        await conn.execute(
            text(
                """
                SELECT receipt.action_kind AS bound_action_kind,
                       receipt.target_id AS bound_target_id,
                       task.id AS task_id,
                       task.task_type,
                       task.status AS task_status,
                       task.payload,
                       task.result AS task_result,
                       task.external_started_at,
                       task.cancel_requested_at,
                       task.correlation_id,
                       run.status AS run_status
                FROM command_idempotency_receipts AS receipt
                JOIN task_queue AS task ON task.id = receipt.task_id
                LEFT JOIN campaign_run AS run
                  ON run.id::text = receipt.target_id
                WHERE receipt.idempotency_key = :idempotency_key
                LIMIT 1
                """
            ),
            {"idempotency_key": idempotency_key},
        )
    ).first()


def _receipt_from_bound(
    row: Any,
    *,
    action: CampaignRunControlAction,
    run_id: uuid.UUID,
) -> CampaignRunCommandReceipt:
    values = _mapping(row)
    payload = _json_object(values["payload"])
    expected_action = _RECEIPT_ACTION[action]
    if (
        values["bound_action_kind"] != expected_action
        or str(values["bound_target_id"]) != str(run_id)
        or values["task_type"] != "campaign_create"
        or str(payload.get("run_id") or "") != str(run_id)
        or values["run_status"] is None
    ):
        raise CampaignRunIdempotencyConflictError("idempotency key is bound to another command")
    result = _json_object(values["task_result"])
    task_status = str(values["task_status"])
    state = (
        _abort_state(status=task_status, result=result)
        if action == "abort"
        else campaign_task_state(status=task_status, result=result)
    )
    reason = (
        _abort_receipt_reason(
            status=task_status,
            result=result,
            external_started_at=values["external_started_at"],
        )
        if action == "abort"
        else "resume_task_lifecycle"
    )
    return CampaignRunCommandReceipt(
        action=action,
        run_id=run_id,
        task_id=int(values["task_id"]),
        state=state,
        run_status=str(values["run_status"]),
        created=False,
        correlation_id=uuid.UUID(str(values["correlation_id"])),
        reason=reason,
    )


async def _bind_receipt(
    conn: AsyncConnection,
    *,
    idempotency_key: str,
    task_id: int,
    action: CampaignRunControlAction,
    run_id: uuid.UUID,
) -> None:
    inserted = (
        await conn.execute(
            text(
                """
                INSERT INTO command_idempotency_receipts
                    (idempotency_key, task_id, action_kind, target_id)
                VALUES (:idempotency_key, :task_id, :action_kind, :target_id)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING idempotency_key
                """
            ),
            {
                "idempotency_key": idempotency_key,
                "task_id": task_id,
                "action_kind": _RECEIPT_ACTION[action],
                "target_id": str(run_id),
            },
        )
    ).first()
    if inserted is None:
        bound = await _load_bound_receipt(conn, idempotency_key=idempotency_key)
        if bound is None:
            raise CampaignRunIdempotencyConflictError("idempotency binding disappeared")
        _receipt_from_bound(bound, action=action, run_id=run_id)


async def _load_locked_run(conn: AsyncConnection, *, run_id: uuid.UUID) -> Any:
    run = (
        await conn.execute(
            text(
                """
                SELECT id, status, config, progress, created_meta_ids
                FROM campaign_run
                WHERE id = :run_id
                FOR UPDATE
                """
            ),
            {"run_id": run_id},
        )
    ).first()
    if run is None:
        raise CampaignRunNotFoundError(str(run_id))
    return run


async def _load_locked_latest_task(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
) -> Any | None:
    return (
        await conn.execute(
            text(
                """
                SELECT id AS task_id, task_type, status AS task_status, payload,
                       result AS task_result, requested_by, attempt_count,
                       max_attempts, external_started_at, cancel_requested_at,
                       cancel_reason, correlation_id
                FROM task_queue
                WHERE task_type = 'campaign_create'
                  AND payload->>'run_id' = CAST(:run_id AS TEXT)
                ORDER BY id DESC
                LIMIT 1
                FOR UPDATE
                """
            ),
            {"run_id": str(run_id)},
        )
    ).first()


async def _lock_run_command_scope(
    conn: AsyncConnection,
    *,
    run_id: uuid.UUID,
) -> None:
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:run_scope))"),
        {"run_scope": f"campaign-run:{run_id}"},
    )


def _validate_inputs(*, idempotency_key: str, requested_by: str) -> tuple[str, str]:
    command_key = idempotency_key.strip()
    actor = requested_by.strip()
    if not command_key or len(command_key) > 128:
        raise ValueError("idempotency_key must contain 1..128 characters")
    if not actor or len(actor) > 64:
        raise ValueError("requested_by must contain 1..64 characters")
    return command_key, actor


def _created_by_chat_id(requested_by: str) -> int | None:
    if not requested_by.startswith("tma:"):
        return None
    try:
        chat_id = int(requested_by.removeprefix("tma:"))
    except ValueError:
        return None
    return chat_id if chat_id > 0 else None


class CampaignRunCommandService:
    """Apply campaign run control commands in one PostgreSQL transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def abort(
        self,
        *,
        run_id: uuid.UUID,
        idempotency_key: str,
        requested_by: str,
        connection: AsyncConnection | None = None,
    ) -> CampaignRunCommandReceipt:
        command_key, actor = _validate_inputs(
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )

        async def _abort(conn: AsyncConnection) -> CampaignRunCommandReceipt:
            await _lock_idempotency_key(conn, idempotency_key=command_key)
            bound = await _load_bound_receipt(conn, idempotency_key=command_key)
            if bound is not None:
                return _receipt_from_bound(bound, action="abort", run_id=run_id)

            await _lock_run_command_scope(conn, run_id=run_id)
            run = _mapping(await _load_locked_run(conn, run_id=run_id))
            task_row = await _load_locked_latest_task(conn, run_id=run_id)
            if task_row is None:
                raise CampaignRunControlUnavailableError("campaign_task_missing")
            task = _mapping(task_row)
            task_status = str(task["task_status"])
            run_status = str(run["status"])
            if run_status in _TERMINAL_RUN_STATUSES or task_status in _TERMINAL_TASK_STATUSES:
                reason = (
                    f"run_already_{run_status}"
                    if run_status in _TERMINAL_RUN_STATUSES
                    else f"campaign_task_already_{task_status}"
                )
                raise CampaignRunControlUnavailableError(reason)
            if run_status not in _ACTIVE_RUN_STATUSES or task_status not in _ACTIVE_TASK_STATUSES:
                raise CampaignRunControlUnavailableError("run_task_state_inconsistent")

            already_requested = task["cancel_requested_at"] is not None
            audit_reason = f"operator abort requested by {actor}"[:8000]
            if task_status in {"pending", "retrying"}:
                if run_status != "queued" or task["external_started_at"] is not None:
                    raise CampaignRunControlUnavailableError("run_task_state_inconsistent")
                cancelled = (
                    await conn.execute(
                        text(
                            """
                            UPDATE task_queue
                            SET cancel_requested_at = COALESCE(
                                    cancel_requested_at, clock_timestamp()
                                ),
                                cancel_reason = COALESCE(cancel_reason, :reason),
                                status = 'cancelled',
                                completed_at = clock_timestamp(),
                                last_error = :reason,
                                result = COALESCE(result, '{}'::jsonb)
                                    || jsonb_build_object(
                                        'outcome', 'REJECTED',
                                        'reason', 'campaign_run_abort_before_execution'
                                    ),
                                updated_at = clock_timestamp()
                            WHERE id = :task_id
                              AND status IN ('pending', 'retrying')
                              AND external_started_at IS NULL
                            RETURNING id, correlation_id, payload
                            """
                        ),
                        {
                            "task_id": int(task["task_id"]),
                            "reason": audit_reason,
                        },
                    )
                ).first()
                if cancelled is None:
                    raise CampaignRunControlUnavailableError("run_task_state_changed")
                updated_run = await conn.execute(
                    text(
                        """
                        UPDATE campaign_run
                        SET status = 'cancelled',
                            progress = jsonb_build_object(
                                'stage', 'cancelled',
                                'outcome', 'REJECTED',
                                'reason', 'campaign_run_abort_before_execution',
                                'checkpoint', 'pre_external'
                            ),
                            error = :reason,
                            updated_at = clock_timestamp()
                        WHERE id = :run_id AND status = 'queued'
                        """
                    ),
                    {"run_id": run_id, "reason": audit_reason},
                )
                if (updated_run.rowcount or 0) != 1:
                    raise CampaignRunControlUnavailableError("run_task_state_changed")
                payload = _json_object(cancelled.payload)
                correlation_id = uuid.UUID(str(cancelled.correlation_id))
                await transition_correlated_incident_in_transaction(
                    conn,
                    task_id=int(cancelled.id),
                    correlation_id=correlation_id,
                    phase="cancelled",
                    payload=payload,
                )
                state: CampaignRunControlState = "confirmed"
                response_run_status = "cancelled"
                response_reason = "aborted_before_external_boundary"
            else:
                requested = await conn.execute(
                    text(
                        """
                        UPDATE task_queue
                        SET cancel_requested_at = COALESCE(
                                cancel_requested_at, clock_timestamp()
                            ),
                            cancel_reason = COALESCE(cancel_reason, :reason),
                            updated_at = clock_timestamp()
                        WHERE id = :task_id AND status = 'running'
                        """
                    ),
                    {
                        "task_id": int(task["task_id"]),
                        "reason": audit_reason,
                    },
                )
                if (requested.rowcount or 0) != 1:
                    raise CampaignRunControlUnavailableError("run_task_state_changed")
                state = "running"
                response_run_status = run_status
                response_reason = (
                    "cooperative_abort_requested_after_external_boundary"
                    if task["external_started_at"] is not None
                    else "cooperative_abort_requested"
                )

            await _bind_receipt(
                conn,
                idempotency_key=command_key,
                task_id=int(task["task_id"]),
                action="abort",
                run_id=run_id,
            )
            return CampaignRunCommandReceipt(
                action="abort",
                run_id=run_id,
                task_id=int(task["task_id"]),
                state=state,
                run_status=response_run_status,
                created=not already_requested,
                correlation_id=uuid.UUID(str(task["correlation_id"])),
                reason=response_reason,
            )

        if connection is not None:
            return await _abort(connection)
        async with self._engine.begin() as conn:
            return await _abort(conn)

    async def resume(
        self,
        *,
        run_id: uuid.UUID,
        idempotency_key: str,
        requested_by: str,
        connection: AsyncConnection | None = None,
    ) -> CampaignRunCommandReceipt:
        command_key, actor = _validate_inputs(
            idempotency_key=idempotency_key,
            requested_by=requested_by,
        )

        async def _resume(conn: AsyncConnection) -> CampaignRunCommandReceipt:
            await _lock_idempotency_key(conn, idempotency_key=command_key)
            bound = await _load_bound_receipt(conn, idempotency_key=command_key)
            if bound is not None:
                return _receipt_from_bound(bound, action="resume", run_id=run_id)

            await _lock_run_command_scope(conn, run_id=run_id)
            run = _mapping(await _load_locked_run(conn, run_id=run_id))
            task_row = await _load_locked_latest_task(conn, run_id=run_id)
            task = _mapping(task_row) if task_row is not None else None
            unavailable = resume_unavailable_reason(
                run_status=str(run["status"]),
                run_config=_json_object(run["config"]),
                created_meta_ids=_json_object(run["created_meta_ids"]),
                task=task,
            )
            if unavailable is not None:
                raise CampaignRunControlUnavailableError(unavailable)
            assert task is not None

            previous_task_id = int(task["task_id"])
            previous_payload = _json_object(task["payload"])
            try:
                previous_generation = int(previous_payload.get("resume_generation") or 0)
            except (TypeError, ValueError):
                raise CampaignRunControlUnavailableError(
                    "invalid_resume_generation_checkpoint"
                ) from None
            generation = previous_generation + 1
            queue_key = f"campaign:resume:{run_id}:{previous_task_id}"
            correlation_id = uuid.uuid4()
            run_config = _json_object(run["config"])
            account_config = _json_object(run_config.get("account"))
            context_payload = {
                "account_id": str(account_config.get("act_id") or "").removeprefix("act_"),
                "currency": account_config.get("currency"),
                "currency_exponent": account_config.get("currency_exponent"),
                "cabinet_timezone": account_config.get("timezone_name"),
                "account_context_observed_at": account_config.get("account_context_observed_at"),
            }
            required_text_evidence = (
                context_payload["account_id"],
                context_payload["currency"],
                context_payload["cabinet_timezone"],
                context_payload["account_context_observed_at"],
            )
            exponent = context_payload["currency_exponent"]
            if (
                not all(required_text_evidence)
                or isinstance(exponent, bool)
                or not isinstance(exponent, int)
            ):
                raise CampaignRunControlUnavailableError("invalid_account_context_checkpoint")
            task_id = await create_task(
                self._engine,
                task_type="campaign_create",
                idempotency_key=queue_key,
                payload={
                    "run_id": str(run_id),
                    "resume_of_task_id": previous_task_id,
                    "resume_generation": generation,
                    "checkpoint": "pre_external",
                    **context_payload,
                },
                requested_by=actor,
                created_by_chat_id=_created_by_chat_id(actor),
                lane="bulk",
                correlation_id=correlation_id,
                connection=conn,
            )
            if task_id is None:
                raise CampaignRunControlUnavailableError("resume_task_idempotency_inconsistent")

            expected_created_meta_ids = _json_object(run["created_meta_ids"])
            reset = await conn.execute(
                text(
                    """
                    UPDATE campaign_run
                    SET status = 'queued',
                        progress = jsonb_build_object(
                            'stage', 'queued',
                            'checkpoint', 'pre_external',
                            'resumed_from_task_id',
                                CAST(:previous_task_id AS BIGINT),
                            'resume_generation',
                                CAST(:resume_generation AS INTEGER)
                        ),
                        error = NULL,
                        updated_at = clock_timestamp()
                    WHERE id = :run_id
                      AND status = :expected_status
                      AND created_meta_ids = CAST(:expected_created_meta_ids AS JSONB)
                    """
                ),
                {
                    "run_id": run_id,
                    "expected_status": str(run["status"]),
                    "expected_created_meta_ids": json.dumps(expected_created_meta_ids),
                    "previous_task_id": previous_task_id,
                    "resume_generation": generation,
                },
            )
            if (reset.rowcount or 0) != 1:
                raise CampaignRunControlUnavailableError("resume_checkpoint_changed")

            await _bind_receipt(
                conn,
                idempotency_key=command_key,
                task_id=task_id,
                action="resume",
                run_id=run_id,
            )
            return CampaignRunCommandReceipt(
                action="resume",
                run_id=run_id,
                task_id=task_id,
                state="queued",
                run_status="queued",
                created=True,
                correlation_id=correlation_id,
                reason="pre_external_checkpoint_resumed",
            )

        if connection is not None:
            return await _resume(connection)
        async with self._engine.begin() as conn:
            return await _resume(conn)


__all__ = [
    "CampaignRunCommandReceipt",
    "CampaignRunCommandService",
    "CampaignRunControlAction",
    "CampaignRunControlOption",
    "CampaignRunControlState",
    "CampaignRunControlUnavailableError",
    "CampaignRunControls",
    "CampaignRunIdempotencyConflictError",
    "CampaignRunNotFoundError",
    "campaign_run_controls",
    "campaign_task_state",
    "resume_unavailable_reason",
]
