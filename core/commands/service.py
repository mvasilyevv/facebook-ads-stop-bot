"""One durable entry point for operator money-actions.

Web, Telegram callbacks and automatic decisions enqueue through this service so
they share validation, idempotency, scheduler lane and correlation semantics.
The service never reports an external Meta result: enqueue success means only
``queued``.  Confirmation is produced later by the fenced worker lifecycle.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from core.meta_api.account_tz import currency_evidence_is_fresh, validated_timezone_name
from core.meta_api.identity import require_ad_account_id
from core.meta_api.queue import create_mutation_task
from core.meta_api.schemas import MetaMutationPayload
from core.money import validated_currency_code
from core.observer.scan_tasks import (
    OBSERVER_SCAN_DEADLINE_SECONDS,
    enqueue_observer_scan,
    lock_observer_scan_publication,
)
from core.wording import delivery_status_ru

if TYPE_CHECKING:
    from core.commands.campaign_runs import CampaignRunCommandReceipt

AdActionKind = Literal["pause_ad", "activate_ad"]
TransactionAuthorizer = Callable[[AsyncConnection, str | None], Awaitable[None]]
CommandState = Literal["queued", "running", "confirmed", "failed", "cancelled", "unknown"]

_ACTIVE_DELIVERY_STATUSES = frozenset({"ACTIVE", "ON", "ENABLED", "DELIVERING"})
_INACTIVE_DELIVERY_STATUSES = frozenset({"OFF", "PAUSED", "INACTIVE", "DISABLED"})
_ACTION_SOURCE_STATUSES: dict[str, frozenset[str]] = {
    "pause_ad": _ACTIVE_DELIVERY_STATUSES,
    "activate_ad": _INACTIVE_DELIVERY_STATUSES,
}
_ACTION_RESULT_STATUSES: dict[str, frozenset[str]] = {
    "pause_ad": _INACTIVE_DELIVERY_STATUSES,
    "activate_ad": _ACTIVE_DELIVERY_STATUSES,
}
_SAFETY_COMPENSATION_SOURCE_PARAM = {
    "activation_without_grace": "supersedes_activation_task_id",
}

_OPERATOR_IDEMPOTENCY_DOMAIN = b"fb-agent:operator-command:v1\0"
_AUTOSTOP_RETRY_IDEMPOTENCY_DOMAIN = b"fb-agent:auto-pause-retry:v1\0"


def principal_scoped_idempotency_key(*, principal: str, client_key: str) -> str:
    """Bind a browser command key to the authenticated operator identity.

    Durable client storage intentionally survives crashes and reloads.  A raw
    key can therefore outlive a logout or Telegram account switch.  Namespace
    it at the trusted server boundary so another principal can never replay a
    previous operator's receipt, while the same principal keeps crash-safe
    retries.  Internal automation and Telegram callback keys remain unchanged.
    """
    normalized_principal = principal.strip()
    normalized_key = client_key.strip()
    if not normalized_principal or len(normalized_principal) > 64:
        raise ValueError("operator principal must contain 1..64 characters")
    if not normalized_key or len(normalized_key) > 128:
        raise ValueError("idempotency_key must contain 1..128 characters")
    digest = hashlib.sha256(
        _OPERATOR_IDEMPOTENCY_DOMAIN
        + normalized_principal.encode("utf-8")
        + b"\0"
        + normalized_key.encode("utf-8")
    ).hexdigest()
    return f"operator:v1:{digest}"


class CommandNotFoundError(LookupError):
    """The requested command target does not exist in the canonical catalog."""


class CommandConflictError(RuntimeError):
    """An idempotency key is already bound to different command semantics."""


class CommandIdentityError(ValueError):
    """The catalog target has no safe, explicit Meta account identity."""


class CommandPreconditionError(RuntimeError):
    """The command target does not satisfy the locked enqueue preconditions."""


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    task_id: int
    created: bool
    state: CommandState
    correlation_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _VerifiedSafetyCompensation:
    observed_delivery_status: str


class CommandService:
    """Validate and enqueue an ad command without executing it inline."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def enqueue_scan_retry(
        self,
        *,
        requested_by: str,
        idempotency_key: str,
        reason: str = "operator_retry_scan",
        connection: AsyncConnection | None = None,
    ) -> CommandReceipt:
        """Queue one interactive scan or return the already active scan.

        ``reason`` — диагностическая метка в очереди. Ручной скан из настроек и
        повтор после разлогина идут одним путём, но в задаче должны остаться
        разными: иначе разбор очереди начинается с вопроса, чего это повтор.
        """
        normalized_requested_by = requested_by.strip()
        normalized_idempotency_key = idempotency_key.strip()
        if not normalized_requested_by or len(normalized_requested_by) > 64:
            raise ValueError("requested_by must contain 1..64 characters")
        if not normalized_idempotency_key or len(normalized_idempotency_key) > 128:
            raise ValueError("idempotency_key must contain 1..128 characters")

        async def _enqueue(conn: AsyncConnection) -> CommandReceipt:
            # Serialize every scan publisher. The active-task check is server-side,
            # so scheduler ticks and different tabs cannot create parallel work.
            await lock_observer_scan_publication(conn)
            active = (
                await conn.execute(
                    text(
                        """
                        SELECT id, status, result, correlation_id, lane, priority
                        FROM task_queue
                        WHERE task_type = 'observer_scan'
                          AND status IN ('pending', 'retrying', 'running')
                          AND cancel_requested_at IS NULL
                          AND COALESCE(payload->>'dependency_state', '') <> 'waiting'
                        ORDER BY
                          CASE status WHEN 'running' THEN 0 ELSE 1 END,
                          priority DESC,
                          created_at,
                          id
                        LIMIT 1
                        FOR UPDATE
                        """
                    )
                )
            ).first()
            if active is not None:
                if str(active.status) in {"pending", "retrying"}:
                    await conn.execute(
                        text(
                            """
                            UPDATE task_queue
                            SET lane = 'interactive',
                                priority = GREATEST(priority, 75),
                                deadline_at = GREATEST(
                                  COALESCE(deadline_at, '-infinity'::timestamptz),
                                  available_at + make_interval(secs => :deadline_seconds),
                                  clock_timestamp() + make_interval(secs => :deadline_seconds)
                                ),
                                updated_at = clock_timestamp()
                            WHERE id = :task_id
                              AND status IN ('pending', 'retrying')
                              AND cancel_requested_at IS NULL
                            """
                        ),
                        {
                            "task_id": int(active.id),
                            "deadline_seconds": OBSERVER_SCAN_DEADLINE_SECONDS,
                        },
                    )
                return CommandReceipt(
                    task_id=int(active.id),
                    created=False,
                    state=_command_state(active.status, active.result),
                    correlation_id=uuid.UUID(str(active.correlation_id)),
                )

            try:
                receipt = await enqueue_observer_scan(
                    self._engine,
                    requested_by=normalized_requested_by,
                    reason=reason,
                    idempotency_key=normalized_idempotency_key,
                    lane="interactive",
                    priority=75,
                    connection=conn,
                )
            except RuntimeError as exc:
                if "idempotency key" in str(exc):
                    raise CommandConflictError(str(exc)) from exc
                raise

            row = (
                await conn.execute(
                    text(
                        """
                        SELECT status, result, correlation_id
                        FROM task_queue
                        WHERE id = :task_id
                        """
                    ),
                    {"task_id": receipt.task_id},
                )
            ).first()
            if row is None:
                raise RuntimeError("observer scan command row disappeared")
            return CommandReceipt(
                task_id=receipt.task_id,
                created=receipt.created,
                state=_command_state(row.status, row.result),
                correlation_id=uuid.UUID(str(row.correlation_id)),
            )

        if connection is not None:
            return await _enqueue(connection)
        async with self._engine.begin() as conn:
            return await _enqueue(conn)

    async def abort_campaign_run(
        self,
        *,
        run_id: uuid.UUID,
        requested_by: str,
        idempotency_key: str,
        connection: AsyncConnection | None = None,
    ) -> CampaignRunCommandReceipt:
        """Request a fenced cooperative stop for the current campaign task."""
        from core.commands.campaign_runs import CampaignRunCommandService

        return await CampaignRunCommandService(self._engine).abort(
            run_id=run_id,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            connection=connection,
        )

    async def resume_campaign_run(
        self,
        *,
        run_id: uuid.UUID,
        requested_by: str,
        idempotency_key: str,
        connection: AsyncConnection | None = None,
    ) -> CampaignRunCommandReceipt:
        """Queue one new task only from a proven pre-boundary checkpoint."""
        from core.commands.campaign_runs import CampaignRunCommandService

        return await CampaignRunCommandService(self._engine).resume(
            run_id=run_id,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            connection=connection,
        )

    async def enqueue_ad_action(
        self,
        *,
        action_kind: AdActionKind,
        fb_ad_id: str,
        requested_by: str,
        idempotency_key: str,
        correlation_id: uuid.UUID | None = None,
        max_attempts: int = 5,
        params: Mapping[str, object] | None = None,
        created_by_chat_id: int | None = None,
        connection: AsyncConnection | None = None,
        expected_delivery_status: str | None = None,
        expected_as_of: datetime | None = None,
        transaction_authorizer: TransactionAuthorizer | None = None,
    ) -> CommandReceipt:
        return await self._enqueue_ad_action(
            action_kind=action_kind,
            fb_ad_id=fb_ad_id,
            requested_by=requested_by,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            max_attempts=max_attempts,
            params=params,
            created_by_chat_id=created_by_chat_id,
            connection=connection,
            expected_delivery_status=expected_delivery_status,
            expected_as_of=expected_as_of,
            verified_safety_compensation=None,
            transaction_authorizer=transaction_authorizer,
        )

    async def enqueue_verified_pause_compensation(
        self,
        *,
        fb_ad_id: str,
        idempotency_key: str,
        reason: Literal["activation_without_grace"],
        source_task_id: int,
        observed_delivery_status: str,
        correlation_id: uuid.UUID | None = None,
        max_attempts: int = 15,
        connection: AsyncConnection | None = None,
    ) -> CommandReceipt:
        """Reassert PAUSE after a worker's direct post-boundary Meta read.

        This is deliberately separate from the shared operator command entry
        point. Only activation-grace recovery can supply the proof, and it may
        supersede terminal command barriers but never active work.
        """

        source_param = _SAFETY_COMPENSATION_SOURCE_PARAM.get(reason)
        if source_param is None:
            raise ValueError("unsupported safety compensation reason")
        if (
            isinstance(source_task_id, bool)
            or not isinstance(source_task_id, int)
            or source_task_id <= 0
        ):
            raise ValueError("source_task_id must be a positive integer")
        observed_status = _require_actionable_delivery(
            action_kind="pause_ad",
            delivery_status=observed_delivery_status,
        )
        return await self._enqueue_ad_action(
            action_kind="pause_ad",
            fb_ad_id=fb_ad_id,
            requested_by="bot_auto_stop",
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            max_attempts=max_attempts,
            params={
                "safety_compensation": reason,
                source_param: source_task_id,
            },
            created_by_chat_id=None,
            connection=connection,
            expected_delivery_status=None,
            expected_as_of=None,
            verified_safety_compensation=_VerifiedSafetyCompensation(
                observed_delivery_status=observed_status,
            ),
            transaction_authorizer=None,
        )

    async def _enqueue_ad_action(
        self,
        *,
        action_kind: AdActionKind,
        fb_ad_id: str,
        requested_by: str,
        idempotency_key: str,
        correlation_id: uuid.UUID | None,
        max_attempts: int,
        params: Mapping[str, object] | None,
        created_by_chat_id: int | None,
        connection: AsyncConnection | None,
        expected_delivery_status: str | None,
        expected_as_of: datetime | None,
        verified_safety_compensation: _VerifiedSafetyCompensation | None,
        transaction_authorizer: TransactionAuthorizer | None,
    ) -> CommandReceipt:
        if action_kind not in {"pause_ad", "activate_ad"}:
            raise ValueError(f"unsupported ad action: {action_kind}")
        target_id = fb_ad_id.strip()
        if not target_id:
            raise ValueError("fb_ad_id must not be empty")
        command_key = idempotency_key.strip()
        if not command_key or len(command_key) > 128:
            raise ValueError("idempotency_key must contain 1..128 characters")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if (expected_delivery_status is None) != (expected_as_of is None):
            raise ValueError("command preconditions must be provided together")

        effective_correlation_id = correlation_id or uuid.uuid4()
        requested_priority = (
            200 if action_kind == "pause_ad" and requested_by == "bot_auto_stop" else 100
        )

        async def _enqueue(conn: AsyncConnection) -> CommandReceipt:
            # Lock and resolve the client key before looking for a reusable
            # active command.  Otherwise K1 can temporarily reuse active K0,
            # disappear with a lost HTTP response, then create a second task
            # after K0 becomes terminal.
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(1129270861, hashtext(:idempotency_key))"),
                {"idempotency_key": command_key},
            )

            bound = await _get_bound_command(conn, idempotency_key=command_key)
            rejected_autostop_source = None
            if bound is not None:
                receipt = _receipt_for_semantics(
                    bound,
                    action_kind=action_kind,
                    target_id=target_id,
                )
                if _is_rejected_autostop_retry_source(
                    bound,
                    action_kind=action_kind,
                    requested_by=requested_by,
                ):
                    rejected_autostop_source = bound
                else:
                    await _strengthen_active_task(
                        conn,
                        task_id=receipt.task_id,
                        max_attempts=max_attempts,
                        priority=requested_priority,
                    )
                    if transaction_authorizer is not None:
                        await transaction_authorizer(conn, None)
                    return receipt

            # A queue key without its command receipt is corrupt in the clean
            # schema.  Detect it before same-target command reuse can mask the
            # conflict, but never adopt or execute that orphan row.
            if rejected_autostop_source is None and await _queue_key_exists(
                conn,
                idempotency_key=command_key,
            ):
                raise CommandConflictError(
                    "idempotency key exists outside the command receipt ledger"
                )

            await conn.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:target_id))"),
                {"target_id": target_id},
            )
            if rejected_autostop_source is not None:
                await _reopen_rejected_autostop_incident(
                    conn,
                    correlation_id=uuid.UUID(str(rejected_autostop_source.correlation_id)),
                )

            # An ambiguous external result is still authoritative control-plane
            # state.  A terminal CONFIRMED result remains a barrier only until
            # the first post-command observation.  That closes the
            # fast-confirmation window where two browser tabs can mint distinct
            # client keys while the operator catalog still contains the
            # pre-command state.
            #
            # Once a post-command metric exists, the catalog is authoritative:
            # matching delivery clears the barrier, while contradictory
            # delivery is a divergence that may require one new corrective
            # command.  Keeping the old CONFIRMED task as a barrier in that
            # latter case would permanently disable auto-pause for an ad which
            # is observably spending again.
            latest = (
                await conn.execute(
                    text(
                        """
                        SELECT task.id, task.correlation_id, task.status,
                               task.result, task.completed_at, task.updated_at,
                               task.manual_review_observation,
                               task.payload->>'mutation_kind' AS action_kind,
                               EXISTS (
                                 SELECT 1
                                 FROM fb_ads AS observed_ad
                                 JOIN ad_metrics AS observed_metric
                                   ON observed_metric.ad_id = observed_ad.id
                                 WHERE observed_ad.fb_ad_id = :target_id
                                   AND observed_metric.cycle_ts >
                                       COALESCE(task.completed_at, task.updated_at)
                               ) AS has_post_evidence
                        FROM task_queue AS task
                        WHERE task.task_type = 'meta_api_mutation'
                          AND task.payload->>'mutation_kind'
                              IN ('pause_ad', 'activate_ad')
                          AND task.payload->>'target_id' = :target_id
                        ORDER BY task.id DESC
                        LIMIT 1
                        """
                    ),
                    {"target_id": target_id},
                )
            ).first()
            barrier = _target_barrier(latest)
            if barrier is not None and not _verified_safety_supersedes_barrier(
                barrier,
                verified_safety_compensation,
            ):
                barrier_status = str(barrier.status).lower()
                if str(barrier.action_kind) != action_kind:
                    lifecycle = (
                        "active"
                        if barrier_status in {"pending", "retrying", "running"}
                        else "unresolved"
                    )
                    raise CommandConflictError(
                        f"ad {target_id} already has {lifecycle} {barrier.action_kind} command"
                    )
                # A later safety channel may require a stronger retry budget
                # than the channel that won the enqueue race.  Terminal UNKNOWN
                # tasks remain immutable while their new alias is persisted.
                if barrier_status in {"pending", "retrying", "running"}:
                    await _strengthen_active_task(
                        conn,
                        task_id=int(barrier.id),
                        max_attempts=max_attempts,
                        priority=requested_priority,
                    )
                if transaction_authorizer is not None:
                    await transaction_authorizer(conn, None)
                if rejected_autostop_source is None:
                    await _bind_command_key(
                        conn,
                        idempotency_key=command_key,
                        task_id=int(barrier.id),
                        action_kind=action_kind,
                        target_id=target_id,
                    )
                return CommandReceipt(
                    task_id=int(barrier.id),
                    created=False,
                    state=_command_state(barrier.status, barrier.result),
                    correlation_id=uuid.UUID(str(barrier.correlation_id)),
                )

            target = (
                await conn.execute(
                    text(
                        """
                        SELECT c.ad_account_id, a.ad_name, a.delivery_status,
                               account_snapshot.timezone_name AS cabinet_timezone,
                               account_snapshot.currency,
                               account_snapshot.currency_observed_at,
                               (
                                 SELECT MAX(m.cycle_ts)
                                 FROM ad_metrics m
                                 WHERE m.ad_id = a.id
                               ) AS metrics_as_of
                        FROM fb_ads a
                        JOIN fb_adsets s ON s.id = a.adset_id
                        JOIN fb_campaigns c ON c.id = s.campaign_id
                        LEFT JOIN meta_account_snapshot account_snapshot
                          ON account_snapshot.account_id = c.ad_account_id
                        WHERE a.fb_ad_id = :fb_ad_id
                        LIMIT 1
                        FOR UPDATE OF a
                        """
                    ),
                    {"fb_ad_id": target_id},
                )
            ).first()
            if target is None:
                raise CommandNotFoundError(f"ad {target_id} not found")
            if expected_delivery_status is not None and expected_as_of is not None:
                current_delivery = str(target.delivery_status or "").strip().upper()
                expected_delivery = expected_delivery_status.strip().upper()
                if current_delivery != expected_delivery or target.metrics_as_of != expected_as_of:
                    raise CommandPreconditionError(
                        f"ad {target_id} changed after operator confirmation"
                    )
            delivery_for_guard = (
                verified_safety_compensation.observed_delivery_status
                if verified_safety_compensation is not None
                else target.delivery_status
            )
            current_delivery = _require_actionable_delivery(
                action_kind=action_kind,
                delivery_status=delivery_for_guard,
            )
            try:
                target_account_id = require_ad_account_id(target.ad_account_id)
            except ValueError as exc:
                raise CommandIdentityError(
                    f"ad {target_id} has no explicit ad_account_id; command rejected"
                ) from exc
            target_currency = validated_currency_code(getattr(target, "currency", None))
            currency_observed_at = getattr(target, "currency_observed_at", None)
            if target_currency != "USD" or not currency_evidence_is_fresh(
                currency_observed_at,
                now=datetime.now(UTC),
            ):
                raise CommandPreconditionError(
                    f"ad {target_id} requires confirmed USD currency before enqueue"
                )
            has_status_divergence = _is_reconciled_confirmed(
                latest
            ) and not _delivery_matches_result(
                action_kind=str(latest.action_kind),
                delivery_status=current_delivery,
            )
            divergence_incident_key = (
                _status_divergence_incident_key(
                    target_id=target_id,
                    previous_task_id=int(latest.id),
                )
                if has_status_divergence
                else None
            )
            if transaction_authorizer is not None:
                await transaction_authorizer(conn, divergence_incident_key)
            target_timezone = validated_timezone_name(getattr(target, "cabinet_timezone", None))
            context_issues: list[str] = []
            if target_timezone is None:
                context_issues.append("cabinet_timezone_unknown")
            context_observed_at = currency_observed_at.isoformat()

            # A confirmed task with post-command evidence is no longer a
            # barrier.  If the evidence contradicts its requested state, bind a
            # critical recurring incident to the new corrective command before
            # enqueue.  The incident, outbox event, task and command receipt
            # share this transaction and therefore commit or roll back together.
            if has_status_divergence:
                await _open_status_divergence_incident(
                    conn,
                    target_id=target_id,
                    target_label=str(target.ad_name or "Объявление"),
                    previous_task_id=int(latest.id),
                    previous_action_kind=str(latest.action_kind),
                    observed_delivery_status=current_delivery,
                    correlation_id=effective_correlation_id,
                )

            payload = MetaMutationPayload(
                mutation_kind=action_kind,
                target_id=target_id,
                ad_account_id=target_account_id,
                params={
                    "requested_via": requested_by[:64],
                    **dict(params or {}),
                },
                currency=target_currency,
                cabinet_timezone=target_timezone,
                account_context_observed_at=context_observed_at,
                account_context_issues=tuple(context_issues),
            )
            task_correlation_id = effective_correlation_id
            task_idempotency_key = command_key
            if rejected_autostop_source is not None:
                predecessor_task_id = int(
                    latest.id if latest is not None else rejected_autostop_source.id
                )
                task_idempotency_key = _autostop_retry_idempotency_key(
                    command_key=command_key,
                    predecessor_task_id=predecessor_task_id,
                )
                task_correlation_id = uuid.UUID(str(rejected_autostop_source.correlation_id))
            task_id = await create_mutation_task(
                self._engine,
                payload=payload,
                requested_by=requested_by[:64],
                idempotency_key=task_idempotency_key,
                max_attempts=max_attempts,
                priority=requested_priority,
                created_by_chat_id=created_by_chat_id,
                correlation_id=task_correlation_id,
                connection=conn,
            )
            if task_id is not None:
                await _bind_command_key(
                    conn,
                    idempotency_key=task_idempotency_key,
                    task_id=task_id,
                    action_kind=action_kind,
                    target_id=target_id,
                )
                return CommandReceipt(
                    task_id=task_id,
                    created=True,
                    state="queued",
                    correlation_id=task_correlation_id,
                )

            # With the command-key lock and atomic task+receipt transaction an
            # unbound queue conflict is an invariant violation.  Fail closed;
            # never adopt a row created through another runtime path.
            raise CommandConflictError("idempotency key exists outside the command receipt ledger")

        # Observer persists catalog/FSM/incident/outbox and the money command in
        # one transaction.  Reuse that connection verbatim: opening another
        # ``engine.begin()`` here would create a crash window and could deadlock
        # on the catalog row or per-ad advisory lock.
        if connection is not None:
            return await _enqueue(connection)
        async with self._engine.begin() as conn:
            return await _enqueue(conn)


async def _get_bound_command(
    conn: AsyncConnection,
    *,
    idempotency_key: str,
) -> Any | None:
    return (
        await conn.execute(
            text(
                """
                SELECT
                    receipt.task_id AS id,
                    receipt.action_kind AS bound_action_kind,
                    receipt.target_id AS bound_target_id,
                    task.task_type,
                    task.payload,
                    task.correlation_id,
                    task.status,
                    task.result
                FROM command_idempotency_receipts AS receipt
                JOIN task_queue AS task ON task.id = receipt.task_id
                WHERE receipt.idempotency_key = :idempotency_key
                LIMIT 1
                """
            ),
            {"idempotency_key": idempotency_key},
        )
    ).first()


async def _queue_key_exists(
    conn: AsyncConnection,
    *,
    idempotency_key: str,
) -> bool:
    return bool(
        await conn.scalar(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM task_queue
                    WHERE idempotency_key = :idempotency_key
                )
                """
            ),
            {"idempotency_key": idempotency_key},
        )
    )


def _receipt_for_semantics(
    row: Any,
    *,
    action_kind: AdActionKind,
    target_id: str,
) -> CommandReceipt:
    payload = row.payload if isinstance(row.payload, dict) else {}
    task_action = payload.get("mutation_kind")
    task_target = str(payload.get("target_id") or "")
    bound_action = row.bound_action_kind
    bound_target = str(row.bound_target_id or "")
    if (
        row.task_type != "meta_api_mutation"
        or task_action != action_kind
        or task_target != target_id
        or bound_action != action_kind
        or bound_target != target_id
    ):
        raise CommandConflictError("idempotency key is bound to another command")
    return CommandReceipt(
        task_id=int(row.id),
        created=False,
        state=_command_state(row.status, row.result),
        correlation_id=uuid.UUID(str(row.correlation_id)),
    )


async def _bind_command_key(
    conn: AsyncConnection,
    *,
    idempotency_key: str,
    task_id: int,
    action_kind: AdActionKind,
    target_id: str,
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
                "action_kind": action_kind,
                "target_id": target_id,
            },
        )
    ).first()
    if inserted is not None:
        return

    bound = await _get_bound_command(conn, idempotency_key=idempotency_key)
    if bound is None:
        raise CommandConflictError("idempotency binding disappeared during reconciliation")
    if (
        int(bound.id) != task_id
        or bound.bound_action_kind != action_kind
        or str(bound.bound_target_id) != target_id
    ):
        raise CommandConflictError("idempotency key is bound to another command")


async def _strengthen_active_task(
    conn: AsyncConnection,
    *,
    task_id: int,
    max_attempts: int,
    priority: int,
) -> None:
    await conn.execute(
        text(
            """
            UPDATE task_queue
            SET max_attempts = GREATEST(max_attempts, :max_attempts),
                priority = GREATEST(priority, :priority),
                updated_at = NOW()
            WHERE id = :task_id
              AND status IN ('pending','retrying','running')
            """
        ),
        {
            "task_id": task_id,
            "max_attempts": max_attempts,
            "priority": priority,
        },
    )


async def _reopen_rejected_autostop_incident(
    conn: AsyncConnection,
    *,
    correlation_id: uuid.UUID,
) -> None:
    """Repair pre-fix terminal incidents before a replacement waits in queue."""
    await conn.execute(
        text(
            """
            UPDATE incidents
            SET status = 'open',
                resolved_at = NULL,
                updated_at = NOW()
            WHERE correlation_id = :correlation_id
              AND status = 'failed'
            """
        ),
        {"correlation_id": correlation_id},
    )


def _is_rejected_autostop_retry_source(
    row: Any,
    *,
    action_kind: AdActionKind,
    requested_by: str,
) -> bool:
    """Allow a new generation only after a proven terminal auto-pause reject."""
    if action_kind != "pause_ad" or requested_by != "bot_auto_stop":
        return False
    if str(row.status or "").lower() not in {"failed", "cancelled"}:
        return False
    result = row.result if isinstance(row.result, Mapping) else {}
    return (
        str(result.get("outcome") or "").upper() == "REJECTED"
        and result.get("reconcile_required") is not True
    )


def _autostop_retry_idempotency_key(
    *,
    command_key: str,
    predecessor_task_id: int,
) -> str:
    """Derive one stable queue/receipt key for the next auto-pause generation."""
    digest = hashlib.sha256(
        _AUTOSTOP_RETRY_IDEMPOTENCY_DOMAIN
        + command_key.encode("utf-8")
        + b"\0"
        + str(predecessor_task_id).encode("ascii")
    ).hexdigest()
    return f"auto:pause_ad:retry:{digest}"


def _command_state(status: object, result: Any) -> CommandState:
    """Map durable task state without claiming a terminal retry is queued."""
    normalized = str(status or "").lower()
    payload = result if isinstance(result, dict) else {}
    outcome = str(payload.get("outcome") or "").upper()
    if outcome == "UNKNOWN" or payload.get("reconcile_required") is True:
        return "unknown"
    if normalized in {"pending", "retrying"}:
        return "queued"
    if normalized == "running":
        return "running"
    if normalized == "succeeded":
        return "confirmed"
    if normalized == "cancelled":
        return "cancelled"
    return "failed"


def _target_barrier(latest: Any | None) -> Any | None:
    """Return the latest task only while it still blocks a new command."""
    if latest is None:
        return None
    status = str(latest.status or "").lower()
    result = latest.result if isinstance(latest.result, Mapping) else {}
    outcome = str(result.get("outcome") or "").upper()
    if status in {"pending", "retrying", "running"}:
        return latest
    if outcome == "UNKNOWN" or result.get("reconcile_required") is True:
        # Терминальный неизвестный исход держит барьер до тех пор, пока его
        # никто не разобрал: слепая повторная команда поверх неизвестности —
        # это ровно тот случай, ради которого барьер и заведён.
        #
        # Но без выхода барьер становится вечным: авто-стоп для этого
        # объявления после него не создаст ни одной задачи, даже если
        # сканирование увидит объявление снова активным (FSM вернёт
        # disabled→normal, новый инцидент придёт с новым ключом — и упрётся
        # сюда же). Зафиксированная ручная сверка снимает барьер: оператор
        # видел фактическое состояние в Ads Manager, и это единственная
        # сверка, доступная для терминального UNKNOWN. Исход задачи при этом
        # остаётся UNKNOWN — сняли барьер, а не переписали результат.
        #
        # Барьер снимается и для наблюдения «всё ещё активен»: иначе оператор,
        # который своими глазами увидел работающий объект, не может отправить
        # команду вообще. Создание и дублирование сюда не попадают — этот
        # запрос читает только pause_ad/activate_ad.
        if getattr(latest, "manual_review_observation", None):
            return None
        return latest
    if status == "succeeded" and outcome == "CONFIRMED" and not bool(latest.has_post_evidence):
        return latest
    return None


def _verified_safety_supersedes_barrier(
    barrier: Any,
    proof: _VerifiedSafetyCompensation | None,
) -> bool:
    """Allow a direct ACTIVE read to supersede only terminal history.

    Pending/running work remains serialized by the regular target barrier.
    Generic operator, Telegram and automatic commands cannot supply ``proof``.
    """

    if proof is None:
        return False
    return str(barrier.status or "").lower() not in {
        "pending",
        "retrying",
        "running",
    }


def _is_reconciled_confirmed(latest: Any | None) -> bool:
    if latest is None:
        return False
    result = latest.result if isinstance(latest.result, Mapping) else {}
    return (
        str(latest.status or "").lower() == "succeeded"
        and str(result.get("outcome") or "").upper() == "CONFIRMED"
        and result.get("reconcile_required") is not True
        and bool(latest.has_post_evidence)
    )


def _normalize_delivery_status(value: object) -> str:
    return str(value or "").strip().upper()


def _require_actionable_delivery(
    *,
    action_kind: AdActionKind,
    delivery_status: object,
) -> str:
    normalized = _normalize_delivery_status(delivery_status)
    if normalized not in _ACTION_SOURCE_STATUSES[action_kind]:
        shown = normalized or "UNKNOWN"
        raise CommandPreconditionError(f"{action_kind} is not allowed from delivery status {shown}")
    return normalized


def _delivery_matches_result(
    *,
    action_kind: str,
    delivery_status: object,
) -> bool:
    result_statuses = _ACTION_RESULT_STATUSES.get(action_kind)
    return result_statuses is not None and (
        _normalize_delivery_status(delivery_status) in result_statuses
    )


async def _open_status_divergence_incident(
    conn: AsyncConnection,
    *,
    target_id: str,
    target_label: str,
    previous_task_id: int,
    previous_action_kind: str,
    observed_delivery_status: str,
    correlation_id: uuid.UUID,
) -> None:
    # Lazy import keeps the command entry point independent from Telegram
    # rendering while reusing the durable incident/outbox transaction helper.
    from core.telegram.worker_notify import notify_recurring_incident_in_transaction

    was_pause = previous_action_kind == "pause_ad"
    await notify_recurring_incident_in_transaction(
        conn,
        incident_key=_status_divergence_incident_key(
            target_id=target_id,
            previous_task_id=previous_task_id,
        ),
        audience="owners",
        event_type="meta_status_divergence",
        severity="critical",
        title=f"Статус в Facebook разошёлся: {target_label}",
        summary=(
            f"После задачи #{previous_task_id} объявление снова показывается как "
            f"{delivery_status_ru(observed_delivery_status)}."
        ),
        lines=[
            (
                "Отправляю команду выключить ещё раз"
                if was_pause
                else "Отправляю команду включить ещё раз"
            ),
            "Если не подтвердится, поменяй статус в Ads Manager вручную",
        ],
        risk=(
            "Объявление может продолжать тратить бюджет"
            if was_pause
            else "Объявление остаётся выключенным и не приносит трафик"
        ),
        resource_type="fb_ad",
        resource_id=target_id,
        correlation_id=correlation_id,
    )


def _status_divergence_incident_key(*, target_id: str, previous_task_id: int) -> str:
    return f"meta-status-divergence:{target_id}:{previous_task_id}"


__all__ = [
    "AdActionKind",
    "CommandConflictError",
    "CommandIdentityError",
    "CommandPreconditionError",
    "CommandNotFoundError",
    "CommandReceipt",
    "CommandService",
    "CommandState",
]
