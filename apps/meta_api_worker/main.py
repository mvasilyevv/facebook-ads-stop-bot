# -*- coding: utf-8 -*-
"""meta_api_worker main loop.

Этап 5: реальная диспетчеризация mutations через AuditedMetaApiClient +
dispatch_mutation. До Этапа 5 здесь была заглушка с NotImplementedError.

Состояние процесса:
- metrics: process liveness and queue depth are exported through Prometheus
- reconcile: делегирован каноническому reconciler_worker (общий по task_type, с bump
  attempt_count) — локальный reconcile-loop убран, чтобы не было двух reconciler'ов
- idle: spinning poll с asyncio.sleep
- graceful: SIGTERM/SIGINT → завершить текущий цикл и закрыть ресурсы

Маршрутизация ошибок:
- PermanentError / TokenInvalidError / NotFoundError / PermissionError → mark_failed (retry бесполезен)
- RateLimitedError / TemporaryError / SessionUnavailableError → requeue_for_retry
- BrowserOperationRejectedError с причиной из BROWSER_OPERATION_PERMANENT_REJECTIONS →
  mark_failed с исходом REJECTED: отправки не было, но повтор той же задачи не лечит
- NotImplementedError (новый mutation_kind без handler) → mark_failed
- MutationValidationError (осознанная валидационная ошибка в handler'е) → mark_failed
- голый ValueError (неожиданный, баг в коде) → requeue (защитный retry, логируется как аномалия)
- любое другое Exception → requeue (защитный retry на transient)
- ИСКЛЮЧЕНИЕ для необратимого duplicate_adset_structure: transient/
  ValueError/Exception → mark_failed (НЕ requeue), т.к. ответ мог потеряться после
  коммита Meta и retry создал бы дубль кампании. См. _IRREVERSIBLE_KINDS.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.adset_duplicates.execution_guard import (
    DuplicateExecutionReceiptError,
    authorize_duplicate_execution_boundary,
)
from core.adset_duplicates.plan_integrity import DUPLICATE_ADSET_STRUCTURE_KIND
from core.commands.service import CommandService
from core.db import WORKER_ENGINE_KWARGS
from core.deadlines import bind_absolute_deadline
from core.meta_api.audit import AuditedMetaApiClient
from core.meta_api.autostop_alert import maybe_alert_autostop_channel_down
from core.meta_api.bulk import locked_status_targets
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    AmbiguousResultError,
    BrowserReadinessRejectedError,
    LoginRequiredError,
    MutationValidationError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
    TokenInvalidError,
    unretryable_browser_rejection,
)
from core.meta_api.errors import (
    PermissionError as MetaPermissionError,
)
from core.meta_api.freshness import (
    defer_auto_stop_for_fresh_snapshot,
    load_meta_snapshot_freshness,
)
from core.meta_api.fsm_sync import (
    is_deactivating_bulk,
    sync_fsm_after_mutation_in_transaction,
)
from core.meta_api.mutations import dispatch_mutation
from core.meta_api.mutations._batch_helpers import (
    build_batch_payload,
    make_batch_entry,
    parse_batch_response,
)
from core.meta_api.mutations.duplicate_adset_structure import (
    DuplicateAdsetStructureHandler,
    DuplicateAdsetStructurePartialError,
    DuplicateProgressCallback,
)
from core.meta_api.ownership import check_mutation_ownership, load_owner_tag
from core.meta_api.queue import (
    checkpoint_duplicate_progress,
    claim_browser_ready_mutation_task,
    defer_duplicate_recovery,
    mark_task_failed,
    mark_task_succeeded,
    release_task_after_browser_readiness_rejection,
    requeue_task,
    requeue_task_proven_not_committed,
)
from core.meta_api.schemas import IRREVERSIBLE_MUTATION_KINDS, MetaMutationPayload
from core.observer.enable_grace import (
    EnableGraceUnsafeError,
    PreparedEnableGrace,
    persist_enable_grace,
    prepare_enable_grace,
)
from core.observer.queries import load_scanning_enabled
from core.tasks.action_reason import browser_rejection_not_retryable_reason
from core.tasks.queue import (
    Task,
    defer_unknown_reconciliation,
    mark_cancelled,
    mark_external_call_started,
    refresh_task_queue_metrics,
    requeue_unknown_for_reconciliation,
    resolve_status_reconciliation_not_applied,
    touch_task_running,
)
from core.tasks.wakeup import TaskQueueWakeup
from core.worker_metrics import (
    mark_worker_db_poll_success,
    mark_worker_heartbeat,
    start_worker_metrics_server,
)

logger = logging.getLogger("meta_api_worker")

# Kept as the worker-level injection point for unit tests and custom runtimes.
# It now receives an AsyncConnection and always runs inside terminalization.
sync_fsm_after_mutation = sync_fsm_after_mutation_in_transaction

WORKER_NAME = os.environ.get("META_API_WORKER_NAME", "meta_api").strip() or "meta_api"
IDLE_SLEEP_SECONDS = 1
_WORKER_INSTANCE_ID = uuid.uuid4()
_CLAIM_LANES = tuple(
    lane.strip()
    for lane in os.environ.get(
        "META_API_WORKER_LANES",
        "interactive,bulk,background",
    ).split(",")
    if lane.strip()
)
_TASK_LEASE_SECONDS = max(30, int(os.environ.get("META_API_LEASE_SECONDS", str(30 * 60))))
_KNOWN_CLAIM_LANES = frozenset({"money", "interactive", "bulk", "background"})


def _resolve_touch_interval(lease_seconds: int, configured: str | None) -> float:
    """Keep renewal strictly inside the active lease or fail at process boot."""
    interval = float(configured) if configured else max(1.0, lease_seconds / 3)
    if interval <= 0 or interval >= lease_seconds:
        raise RuntimeError(
            "META_API_TASK_TOUCH_INTERVAL_SEC must be greater than zero and "
            "strictly less than META_API_LEASE_SECONDS"
        )
    return interval


def _validate_worker_lane_contract(worker_name: str, lanes: tuple[str, ...]) -> None:
    """Fail closed before a mutation worker can claim an unsafe lane."""
    unknown = set(lanes) - _KNOWN_CLAIM_LANES
    if not lanes or unknown:
        raise RuntimeError(f"invalid Meta worker lane configuration: {sorted(unknown) or 'empty'}")
    if worker_name == "autopause":
        if lanes != ("money",):
            raise RuntimeError("autopause worker must claim exactly the money lane")
        return
    if "money" in lanes:
        raise RuntimeError("only the autopause worker may claim the money lane")


_validate_worker_lane_contract(WORKER_NAME, _CLAIM_LANES)
_BROWSER_OPERATION_CALLER = "autopause" if WORKER_NAME == "autopause" else "meta_api"


def _require_claimed_task(task: Task) -> None:
    """Reject anything other than the durable, fenced queue claim."""
    if not isinstance(task, Task):
        raise TypeError("meta_api_worker requires a claimed Task")
    if not isinstance(task.lease_owner, uuid.UUID) or isinstance(task.lease_token, bool):
        raise ValueError("meta_api_worker task has no valid lease fence")
    if task.lease_token <= 0:
        raise ValueError("meta_api_worker task has no valid lease fence")
    if task.external_started_at is not None and not isinstance(task.external_started_at, datetime):
        raise ValueError("meta_api_worker task has invalid external boundary state")


# Lease expiry is the reconciler's canonical abandonment signal. Renew at one
# third of the lease by default; a stale override fails process boot rather than
# allowing a live external operation to lose its fence.
_TASK_TOUCH_INTERVAL_SECONDS = _resolve_touch_interval(
    _TASK_LEASE_SECONDS,
    os.environ.get("META_API_TASK_TOUCH_INTERVAL_SEC"),
)

# requested_by авто-стопа (observer → pause_ad). Совпадает с writers._create_pause_mutation.
_AUTO_STOP_REQUESTED_BY = "bot_auto_stop"
_SAFETY_COMPENSATION_ENABLE_GRACE = "activation_without_grace"


def _is_safety_compensation(payload: MetaMutationPayload) -> bool:
    """Return whether PAUSE is backed by a direct post-boundary Meta read.

    These commands deliberately bypass the regular snapshot-freshness gate:
    they reassert a newer stop decision after reconciliation has already
    confirmed that the ad is ACTIVE.  Require a reason-specific source task id
    so an arbitrary bot_auto_stop payload cannot opt itself out of the gate.
    """

    if payload.mutation_kind != "pause_ad":
        return False
    if payload.params.get("safety_compensation") != _SAFETY_COMPENSATION_ENABLE_GRACE:
        return False
    source_task_id = payload.params.get("supersedes_activation_task_id")
    return (
        isinstance(source_task_id, int)
        and not isinstance(source_task_id, bool)
        and source_task_id > 0
    )


# Per-ad эскалация недоставленной паузы (см. core/meta_api/autostop_alert.py): если конкретная
# auto-stop pause_ad висит недоставленной дольше N секунд — точечный «выключи вручную» с именем
# объявления и спендом. Дополняет channel-level CRITICAL. Дефолты переопределяются из env.
_UNDELIVERED_AFTER_SEC = int(os.environ.get("AUTOSTOP_UNDELIVERED_AFTER_SEC", str(10 * 60)))
_TIMEZONE_REFRESH_SECONDS = max(
    300,
    int(os.environ.get("META_ACCOUNT_TIMEZONE_REFRESH_SECONDS", str(6 * 60 * 60))),
)
_TIMEZONE_RETRY_SECONDS = max(
    30,
    int(os.environ.get("META_ACCOUNT_TIMEZONE_RETRY_SECONDS", "300")),
)


@dataclass(frozen=True)
class AutostopAlertContext:
    """Параметры CRITICAL-алерта auto-stop, прокинутые из main_loop в process_one_task.

    engine используется для записи в durable notification outbox.
    """

    engine: Any  # AsyncEngine для recipients-рассылки


class LeaseRenewalError(RuntimeError):
    """The worker could not prove continued ownership of the running task."""


async def _prepare_enable_grace_for_payload(
    engine: AsyncEngine,
    *,
    payload: MetaMutationPayload,
    require_disabled: bool = True,
) -> PreparedEnableGrace | None:
    """Validate a curator hold before crossing the external activation boundary."""
    if payload.mutation_kind != "activate_ad":
        return None
    intent = payload.params.get("enable_grace")
    if intent is None:
        return None
    if not isinstance(intent, dict) or set(intent) != {"spend_cap"}:
        raise EnableGraceUnsafeError("enable_grace requires only an absolute spend_cap")

    from core.config import get_settings

    return await prepare_enable_grace(
        engine,
        fb_ad_id=str(payload.target_id),
        ad_account_id=payload.ad_account_id,
        requested_spend_cap=intent["spend_cap"],
        grace_seconds=get_settings().enable_reco_hold_grace_seconds,
        require_disabled=require_disabled,
    )


async def _mark_confirmed_with_grace(
    engine: AsyncEngine,
    task: Task,
    *,
    payload: MetaMutationPayload,
    result: dict[str, Any],
    prepared_grace: PreparedEnableGrace | None,
) -> bool:
    """Atomically commit grace, FSM, task and incident/outbox projections."""

    async def commit_effect(conn) -> None:
        if prepared_grace is not None:
            await persist_enable_grace(conn, prepared=prepared_grace)
        await sync_fsm_after_mutation(conn, payload, result)

    return await mark_task_succeeded(
        engine,
        task_id=task.id,
        result=result,
        transactional_effect=commit_effect,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )


async def _mark_failed_with_fsm(
    engine: AsyncEngine,
    task: Task,
    *,
    payload: MetaMutationPayload,
    error: str,
    result: dict[str, Any],
) -> bool:
    """Atomically terminalize UNKNOWN/partial work with confirmed per-ad FSM."""

    async def commit_effect(conn) -> None:
        await sync_fsm_after_mutation(conn, payload, result)

    return await mark_task_failed(
        engine,
        task_id=task.id,
        error=error,
        result=result,
        transactional_effect=commit_effect,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )


async def _compensate_confirmed_activation_without_grace(
    engine: AsyncEngine,
    task: Task,
    *,
    payload: MetaMutationPayload,
    configured_status: str,
    error: EnableGraceUnsafeError,
) -> bool:
    """Atomically expose risk and enqueue PAUSE for ACTIVE-without-grace."""
    transition_token = uuid.uuid4()
    base_result = {
        "outcome": "UNKNOWN",
        "reconcile_required": False,
        "reason": "enable_grace_compensation_pending",
        "external_outcome": "CONFIRMED",
        "external_status": configured_status,
        "compensation_action": "pause_ad",
    }

    async def commit_compensation(conn) -> None:
        receipt = await CommandService(engine).enqueue_verified_pause_compensation(
            fb_ad_id=str(payload.target_id),
            idempotency_key=f"enable-grace-compensation:{task.id}:{payload.target_id}",
            reason=_SAFETY_COMPENSATION_ENABLE_GRACE,
            source_task_id=int(task.id),
            observed_delivery_status=configured_status,
            max_attempts=15,
            connection=conn,
        )
        await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'stop_sent',
                    current_stage = 'stop',
                    open_state_token = COALESCE(open_state_token, :transition_token),
                    last_transition_at = NOW(),
                    updated_at = NOW()
                WHERE ad_id = (
                    SELECT id FROM fb_ads WHERE fb_ad_id = :fb_ad_id
                )
                  AND alert_state IN ('normal', 'disabled')
                """
            ),
            {
                "fb_ad_id": str(payload.target_id),
                "transition_token": transition_token,
            },
        )
        await conn.execute(
            text(
                """
                UPDATE task_queue
                SET result = COALESCE(result, '{}'::jsonb)
                    || jsonb_build_object(
                        'compensation_task_id',
                        CAST(:compensation_task_id AS BIGINT),
                        'compensation_state',
                        CAST(:compensation_state AS TEXT)
                    ),
                    updated_at = NOW()
                WHERE id = :task_id
                  AND status = 'failed'
                  AND lease_owner = :lease_owner
                  AND lease_token = :lease_token
                """
            ),
            {
                "task_id": int(task.id),
                "compensation_task_id": receipt.task_id,
                "compensation_state": receipt.state,
                "lease_owner": task.lease_owner,
                "lease_token": task.lease_token,
            },
        )

    return await mark_task_failed(
        engine,
        task_id=task.id,
        error=f"confirmed activation lacked durable grace; PAUSE queued: {error}",
        result=base_result,
        transactional_effect=commit_compensation,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _build_meta_client(engine: AsyncEngine) -> AuditedMetaApiClient:
    """Сконструировать клиент Marketing API с auditing.

    host/port — из env с дефолтами под локальный browser-agent.
    """
    return AuditedMetaApiClient(
        engine=engine,
        initiated_by=WORKER_NAME,
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
    )


async def execute_mutation(
    payload: MetaMutationPayload,
    *,
    client: MetaApiClient,
    progress_callback: DuplicateProgressCallback | None = None,
) -> dict[str, Any]:
    """Исполнить mutation через dispatch_mutation.

    Доменные ошибки Meta пробрасываются как есть — process_one_task маршрутизирует.
    """
    if payload.mutation_kind == "duplicate_adset_structure":
        return await DuplicateAdsetStructureHandler().execute(
            client,
            payload,
            progress_callback=progress_callback,
        )
    return await dispatch_mutation(client, payload)


async def _touch_loop(engine: AsyncEngine, task: Task, interval_seconds: float) -> None:
    """Renew the fenced lease; any uncertainty is a hard control-plane stop."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            still_running = await touch_task_running(
                engine,
                task_id=task.id,
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
                lease_seconds=_TASK_LEASE_SECONDS,
            )
        except Exception as exc:
            raise LeaseRenewalError(
                f"lease renewal failed for task {task.id}: {type(exc).__name__}"
            ) from exc
        if not still_running:
            raise LeaseRenewalError(f"lease renewal rejected for task {task.id}: fence lost")


async def _wait_for_task_control(engine: AsyncEngine, task: Task) -> str:
    """Poll the DB-authoritative cancellation/deadline while an RPC is active."""
    while True:
        await asyncio.sleep(1)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT cancel_requested_at, deadline_at, status, lease_expires_at
                        FROM task_queue
                        WHERE id = :task_id
                          AND lease_owner = :lease_owner
                          AND lease_token = :lease_token
                        """
                    ),
                    {
                        "task_id": task.id,
                        "lease_owner": task.lease_owner,
                        "lease_token": task.lease_token,
                    },
                )
            ).first()
        if row is None or row.status != "running":
            return "lease_lost"
        if row.lease_expires_at is None or row.lease_expires_at <= datetime.now(UTC):
            return "lease_expired"
        if row.cancel_requested_at is not None:
            return "cancel_requested"
        if row.deadline_at is not None and row.deadline_at <= datetime.now(UTC):
            return "deadline_exceeded"


async def _preflight_task_control(engine: AsyncEngine, task: Task) -> str | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT cancel_requested_at, deadline_at, status, lease_expires_at
                    FROM task_queue
                    WHERE id = :task_id
                      AND lease_owner = :lease_owner
                      AND lease_token = :lease_token
                    """
                ),
                {
                    "task_id": task.id,
                    "lease_owner": task.lease_owner,
                    "lease_token": task.lease_token,
                },
            )
        ).first()
    if row is None or row.status != "running":
        return "lease_lost"
    if row.lease_expires_at is None or row.lease_expires_at <= datetime.now(UTC):
        return "lease_expired"
    if row.cancel_requested_at is not None:
        return "cancel_requested"
    if row.deadline_at is not None and row.deadline_at <= datetime.now(UTC):
        return "deadline_exceeded"
    return None


async def _cross_external_mutation_boundary(
    engine: AsyncEngine,
    task: Task,
    payload: MetaMutationPayload,
) -> bool:
    """Confirm the live claim, then atomically record an external mutation."""
    control_reason = await _preflight_task_control(engine, task)
    if control_reason is not None:
        if control_reason == "cancel_requested":
            await mark_cancelled(
                engine,
                task_id=task.id,
                reason="cancelled before external call",
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
        else:
            await mark_task_failed(
                engine,
                task_id=task.id,
                error=f"command rejected before external call: {control_reason}",
                result={"outcome": "REJECTED", "reason": control_reason},
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
        return False
    if payload.mutation_kind == DUPLICATE_ADSET_STRUCTURE_KIND:
        recovery_checkpoint = task.result if _is_duplicate_recovery_task(task, payload) else None
        try:
            return await authorize_duplicate_execution_boundary(
                engine,
                task_id=task.id,
                task_payload=task.payload,
                requested_by=task.requested_by,
                target_lock_key=str(payload.target_id),
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
                recovery_checkpoint=recovery_checkpoint,
            )
        except DuplicateExecutionReceiptError as exc:
            failure_result: dict[str, Any] = {
                "outcome": "REJECTED",
                "reason": "duplicate_plan_integrity",
            }
            if recovery_checkpoint is not None:
                failure_result = {
                    **recovery_checkpoint,
                    "outcome": "UNKNOWN",
                    "reconcile_required": True,
                    "manual_review_required": True,
                    "phase": "recovery_checkpoint_invalid",
                    "recovery_integrity_error": str(exc),
                }
            applied = await mark_task_failed(
                engine,
                task_id=task.id,
                error=f"duplicate plan integrity rejected: {exc}",
                result=failure_result,
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
            if not applied:
                logger.warning(
                    "meta_api: task id=%s duplicate plan integrity mark_failed lost status race",
                    task.id,
                )
            return False

    return await mark_external_call_started(
        engine,
        task_id=task.id,
        target_lock_key=str(payload.target_id),
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )


async def _execute_with_touch(
    engine: AsyncEngine,
    task: Task,
    payload: MetaMutationPayload,
    *,
    client: MetaApiClient,
    touch_interval_seconds: float = _TASK_TOUCH_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """execute_mutation с фоновым heartbeat-touch updated_at (MID-10).

    Долгие mutation (upload видео, медленная обработка Meta) могут исполняться дольше
    30-мин reconciler-таймаута. Без освежения updated_at reconcile_stuck_running увёл бы
    задачу в 'retrying' → повторное исполнение = дубль/двойной открут. Пока идёт
    execute_mutation, фоновый _touch_loop держит updated_at свежим. По завершении
    (успех/исключение) touch-таск отменяется. Исключения mutation пробрасываются как есть.
    """
    touch_task = asyncio.create_task(_touch_loop(engine, task, touch_interval_seconds))
    control_task = asyncio.create_task(_wait_for_task_control(engine, task))
    execution_task: asyncio.Task[dict[str, Any]] | None = None
    try:

        async def run_mutation() -> dict[str, Any]:
            if payload.mutation_kind != "duplicate_adset_structure":
                with bind_absolute_deadline(task.deadline_at):
                    return await execute_mutation(payload, client=client)

            async def persist_progress(checkpoint: dict[str, Any]) -> None:
                applied = await checkpoint_duplicate_progress(
                    engine,
                    task_id=task.id,
                    checkpoint=checkpoint,
                    lease_owner=task.lease_owner,
                    lease_token=task.lease_token,
                )
                if not applied:
                    raise RuntimeError(
                        "duplicate checkpoint rejected: task is no longer the active create run"
                    )

            with bind_absolute_deadline(task.deadline_at):
                return await execute_mutation(
                    payload,
                    client=client,
                    progress_callback=persist_progress,
                )

        execution_task = asyncio.create_task(run_mutation())
        done, _ = await asyncio.wait(
            {execution_task, control_task, touch_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if touch_task in done:
            try:
                touch_task.result()
                reason = "lease_renewal_stopped"
            except asyncio.CancelledError:
                reason = "lease_renewal_cancelled"
            except Exception as exc:  # noqa: BLE001 — ownership is no longer proven
                logger.warning(
                    "meta_api: task id=%s lease renewal failed; cancelling external operation",
                    task.id,
                    exc_info=True,
                )
                reason = f"lease_renewal_failed:{type(exc).__name__}"
        elif control_task in done:
            try:
                reason = control_task.result()
            except asyncio.CancelledError:
                reason = "control_monitor_cancelled"
            except Exception as exc:  # noqa: BLE001 — losing control is externally ambiguous
                logger.warning(
                    "meta_api: task id=%s control monitor failed; cancelling external operation",
                    task.id,
                    exc_info=True,
                )
                reason = f"control_monitor_failed:{type(exc).__name__}"
        else:
            return execution_task.result()

        # Drain the mutation before publishing UNKNOWN/retry.  If the DB control
        # poller failed and we merely returned, this task would keep sending in
        # the background after its lease/state had already moved on.
        execution_task.cancel()
        try:
            await execution_task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 — the outcome is UNKNOWN either way
            logger.debug(
                "meta_api: task id=%s external operation settled while being cancelled",
                task.id,
                exc_info=True,
            )
        raise AmbiguousResultError(
            f"external operation interrupted: {reason}",
            code=-2,
            endpoint=str(payload.target_id),
        )
    finally:
        # Also covers cancellation of process_one_task itself while asyncio.wait
        # is pending.  No external mutation task may outlive its fenced owner.
        background_tasks = tuple(
            background_task
            for background_task in (execution_task, control_task, touch_task)
            if background_task is not None
        )
        for background_task in background_tasks:
            if not background_task.done():
                background_task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)


# ====================== классификация ошибок ======================


# Permanent ошибки → mark_failed (retry не имеет смысла).
# MutationValidationError(ValueError) — осознанная ошибка валидации payload в handler'е.
# Голый ValueError (случайный, из-за бага) — НЕ здесь, он попадёт в отдельную ветку.
_PERMANENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TokenInvalidError,
    NotFoundError,
    MetaPermissionError,
    PermanentError,
    NotImplementedError,
    MutationValidationError,
)

# Temporary ошибки → requeue_for_retry.
_TEMPORARY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
)

# Необратимые mutations: создают новые объекты в Meta (кампании/копии). Если ответ
# потерян УЖЕ ПОСЛЕ коммита на стороне Meta (gRPC DEADLINE/UNAVAILABLE, битый JSON,
# ValueError на постобработке успешного ответа), повторный вызов = ДУБЛЬ кампании +
# двойной открут бюджета. idempotency_key (на enqueue) от retry той же строки не
# защищает. Поэтому transient/неожиданные ошибки для них НЕ ретраим, а уводим в
# mark_failed с явным сигналом «проверь Meta вручную».
# Единый источник правды — core.meta_api.schemas.IRREVERSIBLE_MUTATION_KINDS (его же
# использует reconciler-крэш-путь). Локальный алиас — для краткости в этом модуле.
_IRREVERSIBLE_KINDS: frozenset[str] = IRREVERSIBLE_MUTATION_KINDS


async def _fail_irreversible(
    engine: AsyncEngine,
    task: Task,
    payload: MetaMutationPayload,
    exc: BaseException,
    *,
    reason: str,
) -> None:
    """Завершить необратимую mutation как UNKNOWN (без retry).

    Money-safety: ответ Meta мог потеряться после коммита → повторный вызов создал бы
    дубль кампании. Поэтому отсутствие подтверждённого ответа никогда не становится
    ``REJECTED`` по умолчанию: сохраняем явный UNKNOWN + manual-review contract.
    """
    logger.error(
        "meta_api: task id=%s kind=%s — необратимая mutation, ошибка (%s) возможно ПОСЛЕ "
        "коммита Meta. НЕ ретраим (риск дубля кампании). ПРОВЕРЬ Meta вручную! err=%r",
        task.id,
        payload.mutation_kind,
        reason,
        exc,
    )
    applied = await mark_task_failed(
        engine,
        task_id=task.id,
        error=f"irreversible_no_retry ({reason}): проверь Meta вручную — возможен дубль: {exc!r}",
        result={
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "manual_review_required": True,
            "operation": payload.mutation_kind,
            "target_id": str(payload.target_id),
            "reason": reason,
        },
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )
    if not applied:
        logger.warning(
            "meta_api: task id=%s mark_failed (irreversible) не применился — гонка", task.id
        )


# ====================== асимметричный стоп ======================

# mutation_kind, которые ВЫКЛЮЧАЮТ открут (снижают трату) — разрешены даже на паузе.
_DEACTIVATING_KINDS = frozenset({"pause_ad"})


@dataclass(frozen=True)
class MutationResultAssessment:
    """Fail-closed interpretation of a mutation handler acknowledgement."""

    state: Literal["confirmed", "rejected", "partial", "invalid"]
    reason: str


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def assess_mutation_result(
    payload: MetaMutationPayload,
    result: Any,
) -> MutationResultAssessment:
    """Validate the full worker/handler boundary before publishing a terminal state.

    An HTTP/gRPC return is not itself success. Every handler must provide an
    exact boolean acknowledgement and a canonical list of affected IDs. A
    malformed result is externally ambiguous because the Meta write boundary
    has already been crossed, so callers must terminalize it as ``UNKNOWN``.
    """
    if not isinstance(result, dict):
        return MutationResultAssessment("invalid", "handler_result_not_object")

    acknowledged = result.get("success")
    if not isinstance(acknowledged, bool):
        return MutationResultAssessment("invalid", "handler_success_not_boolean")

    modified_ids = result.get("modified_ids", [])
    if not isinstance(modified_ids, list) or any(
        not isinstance(item, str) or not item.strip() for item in modified_ids
    ):
        return MutationResultAssessment("invalid", "handler_modified_ids_invalid")
    if len(set(modified_ids)) != len(modified_ids):
        return MutationResultAssessment("invalid", "handler_modified_ids_not_unique")

    if payload.mutation_kind != "bulk_status_change" and acknowledged is False:
        if modified_ids:
            return MutationResultAssessment(
                "invalid",
                "handler_rejection_contains_modified_ids",
            )
        return MutationResultAssessment("rejected", "handler_explicit_rejection")

    if payload.mutation_kind in {"pause_ad", "activate_ad"}:
        if modified_ids != [str(payload.target_id)]:
            return MutationResultAssessment(
                "invalid",
                "status_handler_modified_ids_mismatch",
            )
        return MutationResultAssessment("confirmed", "handler_confirmed")

    if payload.mutation_kind != "bulk_status_change":
        if not modified_ids:
            return MutationResultAssessment(
                "invalid",
                "irreversible_handler_has_no_modified_ids",
            )
        return MutationResultAssessment("confirmed", "handler_confirmed")

    succeeded = result.get("succeeded")
    failed = result.get("failed")
    sub_results = result.get("sub_results")
    if (
        not _is_nonnegative_int(succeeded)
        or not _is_nonnegative_int(failed)
        or not isinstance(sub_results, list)
    ):
        return MutationResultAssessment("invalid", "bulk_result_shape_invalid")
    if succeeded + failed != len(sub_results):
        return MutationResultAssessment("invalid", "bulk_result_counts_mismatch")

    successful_ids: list[str] = []
    seen_ids: set[str] = set()
    for item in sub_results:
        if not isinstance(item, dict):
            return MutationResultAssessment("invalid", "bulk_sub_result_not_object")
        object_id = item.get("id")
        item_success = item.get("success")
        if (
            not isinstance(object_id, str)
            or not object_id.strip()
            or object_id in seen_ids
            or not isinstance(item_success, bool)
        ):
            return MutationResultAssessment("invalid", "bulk_sub_result_invalid")
        seen_ids.add(object_id)
        if item_success:
            successful_ids.append(object_id)

    if len(successful_ids) != succeeded or successful_ids != modified_ids:
        return MutationResultAssessment("invalid", "bulk_modified_ids_mismatch")
    if acknowledged is False:
        if succeeded > 0 and failed > 0:
            return MutationResultAssessment("partial", "bulk_partially_applied")
        if succeeded == 0 and failed > 0:
            return MutationResultAssessment("rejected", "bulk_all_rejected")
        return MutationResultAssessment("invalid", "bulk_acknowledgement_conflict")
    if succeeded == 0 and failed > 0:
        return MutationResultAssessment("rejected", "bulk_all_rejected")
    if succeeded > 0 and failed > 0:
        return MutationResultAssessment("partial", "bulk_partially_applied")
    if succeeded == 0:
        return MutationResultAssessment("invalid", "bulk_empty_acknowledgement")
    return MutationResultAssessment("confirmed", "handler_confirmed")


def _is_activating_mutation(payload: MetaMutationPayload) -> bool:
    """True если mutation ВКЛЮЧАЕТ/тратит (на паузе сканирования откладывается).

    Асимметричный стоп пропускает только ВЫКЛЮЧАЮЩИЕ действия (они снижают риск
    открута), всё остальное на паузе блокирует. Выключающие: pause_ad и
    bulk_status_change с action pause/paused. Activate, duplicate and bulk activate
    откладываются: на стопе кабинет не трогаем сверх выключения.
    """
    kind = payload.mutation_kind
    if kind in _DEACTIVATING_KINDS:
        return False
    if kind == "bulk_status_change":
        # Canonical action=pause bulk is allowed even while scanning is paused.
        return not is_deactivating_bulk(payload.params or {})
    return True


def _is_duplicate_recovery_task(task: Task, payload: MetaMutationPayload) -> bool:
    checkpoint = task.result
    return (
        payload.mutation_kind == "duplicate_adset_structure"
        and isinstance(checkpoint, dict)
        and checkpoint.get("checkpoint_type") == "duplicate_adset_structure"
        and checkpoint.get("recovery_requested") is True
    )


async def _recover_duplicate_task(
    engine: AsyncEngine,
    task: Task,
    payload: MetaMutationPayload,
    *,
    client: MetaApiClient,
) -> None:
    """Execute a PAUSE-only recovery; never replay the original create plan."""
    checkpoint = dict(task.result or {})
    handler = DuplicateAdsetStructureHandler()
    try:
        created, cleanup_failures = await handler.recover_checkpoint(
            client,
            payload,
            checkpoint,
        )
    except asyncio.CancelledError:
        # Leave the task running with its checkpoint. Reconciler will schedule
        # another PAUSE-only recovery after the stale timeout.
        raise
    except Exception as exc:  # noqa: BLE001 — malformed checkpoint cannot be retried safely
        invalid_result = {
            **checkpoint,
            "phase": "recovery_checkpoint_invalid",
            "recovery_error": repr(exc),
        }
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"duplicate recovery checkpoint invalid: {exc!r}",
            result=invalid_result,
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if not applied:
            logger.warning(
                "meta_api: duplicate recovery task=%s invalid-checkpoint "
                "mark_failed lost status race",
                task.id,
            )
        return

    recovery_attempt = int(checkpoint.get("recovery_attempt") or 0) + 1
    recovered_result = {
        **checkpoint,
        "created_ids": created,
        "recovery_requested": True,
        "recovery_attempt": recovery_attempt,
        "cleanup_failures": cleanup_failures,
        "recovery_checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if cleanup_failures:
        recovered_result["phase"] = "recovery_retrying"
        applied = await defer_duplicate_recovery(
            engine,
            task_id=task.id,
            checkpoint=recovered_result,
            error=(
                f"duplicate crash recovery PAUSE incomplete: cleanup_failures={cleanup_failures!r}"
            ),
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if applied:
            logger.error(
                "meta_api: duplicate recovery task=%s incomplete, PAUSE retry scheduled: %s",
                task.id,
                cleanup_failures,
            )
        return

    recovered_result.update(
        {
            "phase": "recovery_paused",
            "recovered_after_crash": True,
        }
    )
    applied = await mark_task_failed(
        engine,
        task_id=task.id,
        error="duplicate_adset_structure crash recovery completed: all checkpointed IDs PAUSED",
        result=recovered_result,
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )
    if not applied:
        logger.warning(
            "meta_api: duplicate recovery task=%s final mark_failed lost status race",
            task.id,
        )
        return


_CONFIGURED_META_STATUS_VALUES = frozenset({"ACTIVE", "PAUSED", "DELETED", "ARCHIVED"})


async def _reconcile_unknown_bulk_status(
    engine: AsyncEngine,
    task: Task,
    payload: MetaMutationPayload,
    *,
    client: MetaApiClient,
) -> bool:
    """Resolve an ambiguous batch by one read-only per-ad batch, never resend."""
    action = str(payload.params.get("action") or "").strip().lower()
    desired = {
        "activate": "ACTIVE",
        "active": "ACTIVE",
        "pause": "PAUSED",
        "paused": "PAUSED",
    }.get(action)
    raw_execution_ids = payload.params.get("ad_ids")
    execution_ids = tuple(
        sorted(
            {
                str(ad_id).strip()
                for ad_id in (raw_execution_ids if isinstance(raw_execution_ids, list) else [])
                if str(ad_id).strip()
            }
        )
    )
    if desired is None or not execution_ids:
        await mark_task_failed(
            engine,
            task_id=task.id,
            error="ambiguous bulk status has no valid reconciliation context",
            result={
                "outcome": "UNKNOWN",
                "reconcile_required": False,
                "per_ad": [],
            },
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        return True

    per_ad: list[dict[str, Any]] = []
    confirmed_ids: list[str] = []
    async with locked_status_targets(engine, ad_ids=execution_ids) as target_locks:
        if target_locks.busy_ad_id is not None:
            await defer_unknown_reconciliation(
                engine,
                task=task,
                error=(f"bulk status reconciliation target lock busy: {target_locks.busy_ad_id}"),
            )
            return True
        try:
            entries = [
                make_batch_entry(
                    method="GET",
                    relative_url=f"{ad_id}?fields=status,effective_status",
                )
                for ad_id in execution_ids
            ]
            with bind_absolute_deadline(task.deadline_at):
                response = await client.execute_graph_call(
                    method="POST",
                    endpoint="/",
                    query_params={"batch": build_batch_payload(entries)},
                    timeout_ms=10_000,
                    ad_account_id=payload.ad_account_id,
                )
            parsed = parse_batch_response(
                response,
                expected_count=len(execution_ids),
            )
        except Exception as exc:  # noqa: BLE001 — read failure is terminal UNKNOWN
            parsed = [
                {
                    "index": index,
                    "success": False,
                    "code": 0,
                    "body": None,
                    "error": type(exc).__name__,
                }
                for index in range(len(execution_ids))
            ]

        for index, ad_id in enumerate(execution_ids):
            item = parsed[index] if index < len(parsed) else {}
            body = item.get("body") if isinstance(item, dict) else None
            configured_raw = body.get("status") if isinstance(body, dict) else None
            effective_raw = body.get("effective_status") if isinstance(body, dict) else None
            configured = configured_raw.strip().upper() if isinstance(configured_raw, str) else None
            effective = effective_raw.strip().upper() if isinstance(effective_raw, str) else None
            if (
                item.get("success") is True
                and configured in _CONFIGURED_META_STATUS_VALUES
                and configured == desired
            ):
                confirmed_ids.append(ad_id)
                per_ad.append(
                    {
                        "id": ad_id,
                        "outcome": "CONFIRMED",
                        "status": configured,
                        "effective_status": effective,
                    }
                )
                continue

            if isinstance(item, dict):
                reason = str(item.get("error") or "desired_configured_status_not_confirmed")
            else:
                reason = "missing_reconciliation_result"
            per_ad.append(
                {
                    "id": ad_id,
                    "outcome": "UNKNOWN",
                    "status": configured,
                    "effective_status": effective,
                    "reason": reason,
                }
            )

        unknown_ids = [str(item["id"]) for item in per_ad if item.get("outcome") == "UNKNOWN"]
        result = {
            "outcome": "UNKNOWN" if unknown_ids else "CONFIRMED",
            "reconcile_required": False,
            "reconciled_after_unknown": True,
            "desired_status": desired,
            "modified_ids": confirmed_ids,
            "confirmed_ids": confirmed_ids,
            "unknown_ids": unknown_ids,
            "succeeded": len(confirmed_ids),
            "failed": len(unknown_ids),
            "per_ad": per_ad,
        }
        if unknown_ids:
            await _mark_failed_with_fsm(
                engine,
                task,
                payload=payload,
                error=(
                    "ambiguous bulk status remained partially unknown after per-ad "
                    f"reconciliation: {','.join(unknown_ids)}"
                ),
                result=result,
            )
        else:
            await _mark_confirmed_with_grace(
                engine,
                task,
                payload=payload,
                result=result,
                prepared_grace=None,
            )
    return True


async def _reconcile_unknown_status_action(
    engine: AsyncEngine,
    task: Task,
    payload: MetaMutationPayload,
    *,
    client: MetaApiClient,
) -> bool:
    """Read actual Meta state before any retry after an ambiguous response.

    Returns ``True`` when the task reached a terminal state. ``False`` means the
    read confirmed that the desired state was not applied, so an idempotent
    status mutation may be sent once more under the current deadline.
    """
    if payload.mutation_kind == "bulk_status_change":
        return await _reconcile_unknown_bulk_status(
            engine,
            task,
            payload,
            client=client,
        )
    status_kinds = {
        "pause_ad": "PAUSED",
        "activate_ad": "ACTIVE",
    }
    desired = status_kinds.get(payload.mutation_kind)
    if desired is None:
        await mark_task_failed(
            engine,
            task_id=task.id,
            error="ambiguous external result requires manual reconciliation",
            result={"outcome": "UNKNOWN", "reconcile_required": True},
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        return True
    try:
        with bind_absolute_deadline(task.deadline_at):
            observed = await client.execute_graph_call(
                method="GET",
                endpoint=f"/{payload.target_id}",
                query_params={"fields": "effective_status,status"},
                timeout_ms=10_000,
                ad_account_id=payload.ad_account_id,
            )
    except BrowserReadinessRejectedError as exc:
        await release_task_after_browser_readiness_rejection(
            engine,
            task=task,
            target_lock_key=str(payload.target_id),
            error=f"status reconciliation browser readiness rejected: {exc!r}",
        )
        return True
    except (AmbiguousResultError, TemporaryError, SessionUnavailableError) as exc:
        await requeue_unknown_for_reconciliation(
            engine,
            task=task,
            error=f"status reconciliation unavailable: {exc!r}",
        )
        return True
    except Exception as exc:  # noqa: BLE001 — result remains genuinely unknown
        await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"status reconciliation failed: {exc!r}",
            result={"outcome": "UNKNOWN", "reconcile_required": True},
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        return True
    if not isinstance(observed, dict):
        await requeue_unknown_for_reconciliation(
            engine,
            task=task,
            error="status reconciliation returned a non-object response",
        )
        return True

    raw_configured_status = observed.get("status")
    raw_effective_status = observed.get("effective_status")
    if not isinstance(raw_configured_status, str):
        await requeue_unknown_for_reconciliation(
            engine,
            task=task,
            error="status reconciliation returned a non-string configured status",
        )
        return True
    configured_status = raw_configured_status.strip().upper()
    if configured_status not in _CONFIGURED_META_STATUS_VALUES:
        await requeue_unknown_for_reconciliation(
            engine,
            task=task,
            error="status reconciliation returned an unsupported configured status",
        )
        return True
    if raw_effective_status is None:
        effective_status = ""
    elif isinstance(raw_effective_status, str):
        effective_status = raw_effective_status.strip().upper()
    else:
        await requeue_unknown_for_reconciliation(
            engine,
            task=task,
            error="status reconciliation returned a non-string effective status",
        )
        return True
    # Meta effective_status is inherited from parent campaign/ad set. It cannot
    # confirm an ad-level status write: an ACTIVE ad under a PAUSED parent is
    # effectively paused but will resume spending when that parent activates.
    actual = configured_status
    if actual != desired:
        resolution = await resolve_status_reconciliation_not_applied(
            engine,
            task_id=task.id,
            effective_status=actual,
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if resolution == "cancelled":
            return True
        if resolution == "failed":
            return True
        # ``running`` means the previous write was proven absent and its
        # external boundary was atomically cleared.  A normal preflight plus a
        # new boundary now guards exactly one safe resend.
        if resolution == "running":
            # Keep the claimed in-memory snapshot coherent with the atomic DB
            # transition.  If that one safe resend is itself ambiguous,
            # requeue_unknown_for_reconciliation must schedule its final
            # read-only verification instead of mistaking this stale snapshot
            # for a failed reconciliation read.
            task.result = {
                key: value
                for key, value in (task.result or {}).items()
                if key not in {"outcome", "reconcile_required"}
            }
            task.result.update(
                {
                    "reconciled_not_applied": True,
                    "effective_status": actual,
                }
            )
            task.external_started_at = None
            return False
        return True
    result = {
        "outcome": "CONFIRMED",
        "reconciled_after_unknown": True,
        "status": configured_status,
        "effective_status": effective_status or None,
    }
    try:
        prepared_grace = await _prepare_enable_grace_for_payload(
            engine,
            payload=payload,
            require_disabled=False,
        )
    except EnableGraceUnsafeError as exc:
        await _compensate_confirmed_activation_without_grace(
            engine,
            task,
            payload=payload,
            configured_status=configured_status,
            error=exc,
        )
        return True
    await _mark_confirmed_with_grace(
        engine,
        task,
        payload=payload,
        result=result,
        prepared_grace=prepared_grace,
    )
    return True


async def _execute_and_finalize_mutation(
    engine: AsyncEngine,
    task: Task,
    payload: MetaMutationPayload,
    *,
    client: MetaApiClient,
    prepared_grace: PreparedEnableGrace | None,
) -> None:
    """Execute one already-crossed mutation and project its terminal state."""
    result = await _execute_with_touch(engine, task, payload, client=client)
    assessment = assess_mutation_result(payload, result)
    if assessment.state == "invalid":
        failure_result = {
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "manual_review_required": True,
            "reason": "handler_contract_violation",
            "contract_error": assessment.reason,
            "operation": payload.mutation_kind,
            "target_id": str(payload.target_id),
        }
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"mutation handler contract violated: {assessment.reason}",
            result=failure_result,
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s contract-violation finalization lost fence",
                task.id,
            )
        else:
            logger.error(
                "meta_api: task id=%s kind=%s returned an invalid acknowledgement (%s); "
                "terminal UNKNOWN, manual reconciliation required",
                task.id,
                payload.mutation_kind,
                assessment.reason,
            )
        return
    if assessment.state == "partial":
        partial_result = {
            **result,
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "manual_review_required": True,
            "reason": assessment.reason,
        }
        applied = await _mark_failed_with_fsm(
            engine,
            task,
            payload=payload,
            error=(
                "mutation partially applied; terminal UNKNOWN and manual "
                f"reconciliation required: {result!r}"
            )[:500],
            result=partial_result,
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s partial finalization lost fence",
                task.id,
            )
        else:
            logger.error(
                "meta_api: task id=%s kind=%s applied only partially "
                "(succeeded=%s failed=%s); terminal UNKNOWN",
                task.id,
                payload.mutation_kind,
                result.get("succeeded"),
                result.get("failed"),
            )
        return
    if assessment.state == "rejected":
        err = f"mutation_logical_fail: handler вернул провал без exception result={result!r}"
        failure_result = {
            **result,
            "outcome": "REJECTED",
            "reason": assessment.reason,
        }
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=err[:500],
            result=failure_result,
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (logical fail) не применился — гонка",
                task.id,
            )
        else:
            logger.error(
                "meta_api: task id=%s kind=%s — логический провал mutation (без exception), "
                "mark_failed. ПРОВЕРЬ вручную! result=%r",
                task.id,
                payload.mutation_kind,
                result,
            )
        return
    result = {**result, "outcome": "CONFIRMED"}
    applied = await _mark_confirmed_with_grace(
        engine,
        task,
        payload=payload,
        result=result,
        prepared_grace=prepared_grace,
    )
    if not applied:
        logger.warning(
            "meta_api: task id=%s mark_succeeded не применился "
            "(status != running) — гонка с другим воркером, пропускаю",
            task.id,
        )
        return
    logger.info("meta_api: task id=%s succeeded", task.id)


async def process_one_task(
    engine: AsyncEngine,
    task: Task,
    *,
    client: MetaApiClient,
    alert_ctx: AutostopAlertContext | None = None,
) -> None:
    """Полный жизненный цикл одной задачи с обязательным Meta client."""
    _require_claimed_task(task)
    try:
        payload = MetaMutationPayload.from_dict(task.payload)
    except (KeyError, ValueError) as exc:
        logger.error("Невалидный payload в task id=%s: %s", task.id, exc)
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"invalid payload: {exc}",
            result={"outcome": "REJECTED", "reason": "invalid_payload"},
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (invalid payload) не применился "
                "— гонка с другим воркером",
                task.id,
            )
        return

    # Reconciler can turn only a checkpointed stale duplicate into a recovery
    # claim. Handle it before scanning/owner gates: PAUSE lowers spend risk and
    # must remain possible even if the source disappeared from the local catalog.
    if _is_duplicate_recovery_task(task, payload):
        if not await _cross_external_mutation_boundary(engine, task, payload):
            return
        await _recover_duplicate_task(
            engine,
            task,
            payload,
            client=client,
        )
        return

    stored_result = task.result
    if isinstance(stored_result, dict) and stored_result.get("reconcile_required") is True:
        try:
            reconciled = await _reconcile_unknown_status_action(
                engine,
                task,
                payload,
                client=client,
            )
        except Exception as exc:  # noqa: BLE001 — keep UNKNOWN durable and retry read-only
            logger.exception(
                "meta_api: task id=%s reconciliation lifecycle failed; "
                "deferring without another external write",
                task.id,
            )
            await defer_unknown_reconciliation(
                engine,
                task=task,
                error=f"reconciliation lifecycle failed: {exc!r}",
                delay_seconds=5,
            )
            return
        if reconciled:
            return

    # Асимметричный стоп: на паузе сканирования откладываем АКТИВИРУЮЩИЕ mutations
    # (activate/bulk activate/create/duplicate/budget/...), пропуская только
    # ВЫКЛЮЧАЮЩИЕ (pause_*, bulk pause) — они снижают риск открута. Отложенная
    # задача уходит в retry и исполнится после снятия паузы; если пауза длится
    # дольше лимита попыток, команда завершается ошибкой вместо скрытого запуска.
    if _is_activating_mutation(payload) and not await load_scanning_enabled(engine):
        retried = await requeue_task(
            engine,
            task=task,
            error="scanning_paused: активирующая mutation отложена до снятия паузы",
        )
        if retried:
            logger.info(
                "meta_api: task id=%s (%s) отложена — сканирование на паузе (асимметричный стоп)",
                task.id,
                payload.mutation_kind,
            )
        else:
            logger.warning(
                "meta_api: task id=%s (%s) отменена — пауза дольше лимита попыток",
                task.id,
                payload.mutation_kind,
            )
        return

    # Owner-scoping на исполнении: last-line-of-defense против действий с ЧУЖИМИ
    # объявлениями в шаренном кабинете. Резолвим target → campaign_name из каталога
    # и сверяем owner_tag. Строгая политика: чужое не трогаем (permanent fail); своё,
    # но ещё не в каталоге (скан отстал) — ВЫКЛЮЧАЮЩИЕ в requeue (скан догонит),
    # ВКЛЮЧАЮЩИЕ в fail. owner_tag пуст → гейт пропускает всё (фильтр выключен).
    owner_tag = await load_owner_tag(engine)
    ownership = await check_mutation_ownership(engine, payload, owner_tag=owner_tag)
    if not ownership.allowed:
        if ownership.not_found and not _is_activating_mutation(payload):
            retried = await requeue_task(
                engine, task=task, error=f"owner_scoping_not_found: {ownership.reason}"
            )
            if retried:
                logger.info(
                    "meta_api: task id=%s отложена — цель не в каталоге, скан догонит (%s)",
                    task.id,
                    ownership.reason,
                )
            else:
                logger.warning(
                    "meta_api: task id=%s owner_scoping not_found — исчерпаны попытки: %s",
                    task.id,
                    ownership.reason,
                )
            return
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"owner_scoping_reject: {ownership.reason}",
            result={"outcome": "REJECTED", "reason": "owner_scoping"},
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if applied:
            logger.warning(
                "meta_api: task id=%s ОТКЛОНЕНА owner-scoping (чужое/неизвестное): %s foreign=%s",
                task.id,
                ownership.reason,
                ownership.foreign_ids,
            )
        else:
            logger.warning(
                "meta_api: task id=%s owner_scoping mark_failed не применился — гонка",
                task.id,
            )
        return

    # Auto-stop is a money decision. A task may sit or retry for a long time, so
    # re-check freshness immediately before the external boundary. Manual and
    # bulk operations are intentionally outside this automatic-decision gate.
    if (
        payload.mutation_kind == "pause_ad"
        and task.requested_by == _AUTO_STOP_REQUESTED_BY
        and not _is_safety_compensation(payload)
    ):
        freshness = await load_meta_snapshot_freshness(
            engine,
            fb_ad_id=str(payload.target_id),
        )
        if not freshness.fresh:
            deferred = await defer_auto_stop_for_fresh_snapshot(
                engine,
                task_id=task.id,
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
            if deferred:
                logger.info(
                    "meta_api: auto-stop task id=%s deferred for fresh Meta snapshot "
                    "latest=%s scan_id=%s decision_confirmed=%s",
                    task.id,
                    freshness.latest_cycle_at,
                    freshness.scan_id,
                    freshness.decision_confirmed,
                )
            return

    try:
        prepared_grace = await _prepare_enable_grace_for_payload(
            engine,
            payload=payload,
        )
    except EnableGraceUnsafeError as exc:
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"enable_grace_precondition: {exc}",
            result={"outcome": "REJECTED", "reason": "enable_grace_precondition"},
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if applied:
            logger.warning(
                "meta_api: curator activation task id=%s rejected before external call: %s",
                task.id,
                exc,
            )
        return

    logger.info(
        "meta_api: исполняю task id=%s kind=%s target=%s",
        task.id,
        payload.mutation_kind,
        payload.target_id,
    )

    external_boundary_crossed = False
    try:
        if not await _cross_external_mutation_boundary(engine, task, payload):
            logger.info(
                "meta_api: task id=%s отменена/закрыта до внешнего вызова kind=%s target=%s",
                task.id,
                payload.mutation_kind,
                payload.target_id,
            )
            return
        external_boundary_crossed = True
        await _execute_and_finalize_mutation(
            engine,
            task,
            payload,
            client=client,
            prepared_grace=prepared_grace,
        )
        return
    except DuplicateAdsetStructurePartialError as exc:
        # This executor may have created many campaigns/adsets/ads before one
        # Graph call failed. The handler has already attempted to PAUSE every
        # known id. Never retry: a lost create response could duplicate spend.
        logger.error(
            "meta_api: task id=%s duplicate_adset_structure partial fail — "
            "created_ids=%s failed_steps=%s cleanup_failures=%s",
            task.id,
            exc.created_ids,
            exc.failed_steps,
            exc.cleanup_failures,
        )
        partial_result = {
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "manual_review_required": True,
            "checkpoint_type": "duplicate_adset_structure",
            "checkpoint_version": 2,
            "phase": "recovery_retrying" if exc.cleanup_failures else "failed_cleanup",
            "partial_fail": True,
            "created_ids": exc.created_ids,
            "failed_steps": exc.failed_steps,
            "cleanup_failures": exc.cleanup_failures,
            "recovery_requested": bool(exc.cleanup_failures),
        }
        error = (
            "duplicate_adset_structure_partial_fail: "
            f"created_ids={exc.created_ids!r} failed={exc.failed_steps!r} "
            f"cleanup_failures={exc.cleanup_failures!r}"
        )[:2000]
        if exc.cleanup_failures:
            # Do not terminally close while any known object failed to PAUSE.
            # This is cleanup-only retry; the create plan is never replayed.
            applied = await defer_duplicate_recovery(
                engine,
                task_id=task.id,
                checkpoint=partial_result,
                error=error,
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
        else:
            applied = await mark_task_failed(
                engine,
                task_id=task.id,
                error=error,
                result=partial_result,
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
        if not applied:
            logger.warning(
                "meta_api: task id=%s finalize/recovery (adset structure partial) "
                "не применился — гонка с другим воркером",
                task.id,
            )
        return
    except AmbiguousResultError as exc:
        # A timeout/cancellation after external_started_at is never treated as
        # success or blind failure. Status changes get a read-before-retry pass;
        # create/duplicate/budget operations stop UNKNOWN for operator review.
        if payload.mutation_kind in {
            "pause_ad",
            "activate_ad",
            "bulk_status_change",
        }:
            await requeue_unknown_for_reconciliation(
                engine,
                task=task,
                error=f"ambiguous external result: {exc!r}",
            )
            return
        await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"ambiguous irreversible/non-status result: {exc!r}",
            result={
                "outcome": "UNKNOWN",
                "reconcile_required": True,
                "manual_review_required": True,
                "operation": payload.mutation_kind,
                "target_id": str(payload.target_id),
                "reason": "ambiguous_result",
            },
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        return
    except _PERMANENT_EXCEPTIONS as exc:
        failure_result: dict[str, Any] = {
            "outcome": "REJECTED",
            "reason": type(exc).__name__,
        }
        if isinstance(exc, LoginRequiredError):
            failure_result["requires_facebook_login"] = True
        elif isinstance(exc, TokenInvalidError):
            failure_result["requires_meta_reauth"] = True
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=repr(exc),
            result=failure_result,
            lease_owner=task.lease_owner,
            lease_token=task.lease_token,
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed не применился "
                "(status != running) — гонка с другим воркером, пропускаю",
                task.id,
            )
        else:
            logger.warning("meta_api: task id=%s → permanent fail: %s", task.id, exc)
        return
    except _TEMPORARY_EXCEPTIONS as exc:
        # Необратимые kinds не ретраим: transient мог прилететь после коммита Meta → дубль.
        # SessionUnavailableError is a proven pre-send family (circuit-open,
        # FAILED_PRECONDITION, browser-agent without a token). Other transient
        # failures may have crossed the Meta boundary and are never replayed for
        # an irreversible duplicate operation.
        # Три независимых вопроса об одной ошибке, и склеивать их нельзя:
        #   1. каков исход — known_not_committed отвечает «отправки не было»;
        #   2. тратить ли попытку — readiness_rejected отвечает «нет»;
        #   3. поможет ли повтор вообще — unretryable_rejection отвечает по коду
        #      причины, названному browser-agent'ом.
        # Ответ на (1) от ответа на (3) не зависит: неисправимый отказ остаётся
        # доказанным REJECTED, меняется только то, вернётся ли задача в очередь.
        readiness_rejected = isinstance(exc, BrowserReadinessRejectedError)
        known_not_committed = isinstance(exc, SessionUnavailableError)
        unretryable_rejection = unretryable_browser_rejection(exc)
        if external_boundary_crossed and not known_not_committed:
            if payload.mutation_kind in {
                "pause_ad",
                "activate_ad",
                "bulk_status_change",
            }:
                await requeue_unknown_for_reconciliation(
                    engine,
                    task=task,
                    error=f"ambiguous temporary error after external boundary: {exc!r}",
                )
                # UNKNOWN changes the retry strategy, but it must not suppress the
                # critical transport-health signal for an automatic money action.
                # PostgreSQL outbox deduplicates the resulting notification event.
                if alert_ctx is not None and task.requested_by == _AUTO_STOP_REQUESTED_BY:
                    await maybe_alert_autostop_channel_down(
                        exc=exc,
                        fb_ad_id=payload.target_id,
                        engine=alert_ctx.engine,
                    )
            else:
                await mark_task_failed(
                    engine,
                    task_id=task.id,
                    error=f"ambiguous temporary error after external boundary: {exc!r}",
                    result={
                        "outcome": "UNKNOWN",
                        "reconcile_required": True,
                        "manual_review_required": True,
                        "operation": payload.mutation_kind,
                        "target_id": str(payload.target_id),
                        "reason": "temporary_after_external_boundary",
                    },
                    lease_owner=task.lease_owner,
                    lease_token=task.lease_token,
                )
            return
        if payload.mutation_kind in _IRREVERSIBLE_KINDS and not known_not_committed:
            await _fail_irreversible(engine, task, payload, exc, reason="temporary")
            return
        if unretryable_rejection is not None:
            # Запрос собран неверно или вызывающему не разрешено это делать:
            # повтор той же задачи вернёт тот же отказ. Исход прежний —
            # REJECTED, отправки не было и сверять нечего, — но задача
            # закрывается сразу, а не крутится до исчерпания попыток, пряча
            # поломку вызывающего под видом недоступного канала.
            # Причина названа кодом из закрытого словаря: сырой текст отказа
            # наружу не выносится, детали остаются в логе рядом с exc_info.
            logger.error(
                "meta_api: task id=%s → rejected before send, retry cannot fix it "
                "(kind=%s reason=%s)",
                task.id,
                payload.mutation_kind,
                unretryable_rejection.reason_code,
                exc_info=True,
            )
            rejected_result: dict[str, Any] = {
                "outcome": "REJECTED",
                "reason": "browser_rejection_not_retryable",
                "pre_dispatch": True,
                "pre_dispatch_reason_code": unretryable_rejection.reason_code,
            }
            operator_reason = browser_rejection_not_retryable_reason(
                unretryable_rejection.reason_code
            )
            if operator_reason:
                rejected_result["operator_reason"] = operator_reason
            applied = await mark_task_failed(
                engine,
                task_id=task.id,
                error=(
                    "browser rejected the request before send and a retry cannot "
                    f"fix it: {unretryable_rejection.reason_code}"
                ),
                result=rejected_result,
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
            if not applied:
                logger.warning(
                    "meta_api: task id=%s mark_failed (unretryable rejection) "
                    "не применился — гонка с другим воркером",
                    task.id,
                )
            # Повторов больше не будет, а объявление продолжает тратить бюджет:
            # money-сигнал «команда не дошла до кабинета» нужен здесь тем более.
            if alert_ctx is not None and task.requested_by == _AUTO_STOP_REQUESTED_BY:
                await maybe_alert_autostop_channel_down(
                    exc=exc,
                    fb_ad_id=payload.target_id,
                    engine=alert_ctx.engine,
                )
            return
        if readiness_rejected:
            pre_send_status = await release_task_after_browser_readiness_rejection(
                engine,
                task=task,
                target_lock_key=str(payload.target_id),
                error=repr(exc),
            )
            retried = pre_send_status == "retrying"
        elif known_not_committed:
            pre_send_status = await requeue_task_proven_not_committed(
                engine,
                task=task,
                target_lock_key=str(payload.target_id),
                error=repr(exc),
            )
            retried = pre_send_status == "retrying"
        else:
            pre_send_status = None
            retried = await requeue_task(engine, task=task, error=repr(exc))
        if retried:
            logger.warning("meta_api: task id=%s → retrying (temporary): %s", task.id, exc)
        elif pre_send_status == "cancelled":
            logger.info(
                "meta_api: task id=%s cancelled after proven pre-send reject",
                task.id,
            )
        else:
            logger.error("meta_api: task id=%s → exhausted retries (temporary): %s", task.id, exc)
        # Money-сигнал: auto-stop pause_ad не доходит до Meta из-за мёртвого Vision-канала.
        # Каждый signal достигает durable outbox; PostgreSQL подавляет дубли.
        if alert_ctx is not None and task.requested_by == _AUTO_STOP_REQUESTED_BY:
            await maybe_alert_autostop_channel_down(
                exc=exc,
                fb_ad_id=payload.target_id,
                engine=alert_ctx.engine,
            )
        return
    except ValueError as exc:
        if external_boundary_crossed and payload.mutation_kind in {
            "pause_ad",
            "activate_ad",
            "bulk_status_change",
        }:
            await requeue_unknown_for_reconciliation(
                engine,
                task=task,
                error=f"ambiguous post-call value error: {exc!r}",
            )
            return
        # Необратимые kinds: ValueError мог прийти на постобработке УЖЕ успешного ответа
        # (парсинг id/batch-тела) → retry создал бы дубль. Уводим в failed.
        if payload.mutation_kind in _IRREVERSIBLE_KINDS:
            await _fail_irreversible(engine, task, payload, exc, reason="value_error_postprocess")
            return
        # Голый ValueError — скорее всего баг в коде или неожиданный Graph-ответ.
        # Не mark_failed (без retry потеряем задачу навсегда), а requeue с аномалия-логом.
        logger.error(
            "meta_api: task id=%s неожиданный ValueError (возможно баг) — requeue: %s",
            task.id,
            exc,
            exc_info=True,
        )
        retried = await requeue_task(engine, task=task, error=f"unexpected_value_error: {exc!r}")
        if retried:
            logger.warning("meta_api: task id=%s → retrying (unexpected ValueError)", task.id)
        else:
            logger.error(
                "meta_api: task id=%s → final fail (unexpected ValueError, retries exhausted)",
                task.id,
            )
        return
    except Exception as exc:  # noqa: BLE001 — защитная сетка на неклассифицированное
        if external_boundary_crossed:
            if payload.mutation_kind in {
                "pause_ad",
                "activate_ad",
                "bulk_status_change",
            }:
                await requeue_unknown_for_reconciliation(
                    engine,
                    task=task,
                    error=f"ambiguous unclassified post-call error: {exc!r}",
                )
            else:
                await mark_task_failed(
                    engine,
                    task_id=task.id,
                    error=f"ambiguous unclassified post-call error: {exc!r}",
                    result={
                        "outcome": "UNKNOWN",
                        "reconcile_required": True,
                        "manual_review_required": True,
                        "operation": payload.mutation_kind,
                        "target_id": str(payload.target_id),
                        "reason": "unclassified_after_external_boundary",
                    },
                    lease_owner=task.lease_owner,
                    lease_token=task.lease_token,
                )
            return
        # Необратимые kinds: неклассифицированная ошибка после возможного коммита → не ретраим.
        if payload.mutation_kind in _IRREVERSIBLE_KINDS:
            await _fail_irreversible(engine, task, payload, exc, reason="unknown")
            return
        retried = await requeue_task(engine, task=task, error=repr(exc))
        if retried:
            logger.warning("meta_api: task id=%s → retrying (unknown): %s", task.id, exc)
        else:
            logger.error("meta_api: task id=%s → final fail (unknown): %s", task.id, exc)


# ====================== sub-loops ======================


async def metrics_loop(stop: asyncio.Event, engine: AsyncEngine | None = None) -> None:
    """Refresh Prometheus process and queue metrics."""
    interval = 15.0
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        if engine is not None:
            try:
                await refresh_task_queue_metrics(engine)
            except Exception:  # noqa: BLE001
                logger.debug("task queue metric refresh failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def task_loop(
    engine: AsyncEngine,
    stop: asyncio.Event,
    *,
    client: MetaApiClient,
    alert_ctx: AutostopAlertContext | None = None,
    wakeup: TaskQueueWakeup | None = None,
) -> None:
    """Главный цикл claim → execute → mark."""
    _validate_worker_lane_contract(WORKER_NAME, _CLAIM_LANES)
    next_timezone_refresh_at = 0.0
    while not stop.is_set():
        try:
            claim = await claim_browser_ready_mutation_task(
                engine,
                lanes=_CLAIM_LANES,
                worker_id=_WORKER_INSTANCE_ID,
                lease_seconds=_TASK_LEASE_SECONDS,
            )
        except Exception:  # noqa: BLE001
            logger.exception("ошибка readiness-gated claim")
            if wakeup is not None:
                await wakeup.wait_for_work(stop)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
                except asyncio.TimeoutError:
                    pass
            continue

        mark_worker_db_poll_success(WORKER_NAME)
        if claim.queue_empty or claim.task is None:
            # Refresh durable IANA names outside the money path. The schedule is
            # process-local only; PostgreSQL remains the sole timezone authority.
            loop_now = asyncio.get_running_loop().time()
            if loop_now >= next_timezone_refresh_at:
                try:
                    from core.meta_api.account_tz import refresh_account_timezones

                    updated = await refresh_account_timezones(engine, client)
                    retry_after = (
                        _TIMEZONE_REFRESH_SECONDS if updated > 0 else _TIMEZONE_RETRY_SECONDS
                    )
                    next_timezone_refresh_at = loop_now + retry_after
                except Exception:  # noqa: BLE001
                    next_timezone_refresh_at = loop_now + _TIMEZONE_RETRY_SECONDS
                    logger.debug("durable account timezone refresh skipped", exc_info=True)
            # Money: this PostgreSQL scan and notification intent never depend on Redis.
            try:
                from core.meta_api.autostop_alert import escalate_undelivered_autostop_pauses

                await escalate_undelivered_autostop_pauses(
                    engine,
                    requested_by=_AUTO_STOP_REQUESTED_BY,
                    stuck_after_seconds=_UNDELIVERED_AFTER_SEC,
                )
            except Exception:  # noqa: BLE001
                logger.debug("undelivered-pause escalation пропущена", exc_info=True)
            if wakeup is not None:
                await wakeup.wait_for_work(stop)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
                except asyncio.TimeoutError:
                    pass
            continue

        vision_profile_id = str(claim.browser_profile_id or "").strip()
        if not vision_profile_id:
            raise RuntimeError("browser-ready claim returned no canonical Vision profile")
        with client.operation_authority(
            caller=_BROWSER_OPERATION_CALLER,
            task_id=claim.task.id,
            lease_owner=claim.task.lease_owner,
            lease_token=claim.task.lease_token,
            vision_profile_id=vision_profile_id,
            browser_readiness_generation=claim.browser_readiness_generation,
        ):
            await process_one_task(
                engine,
                claim.task,
                client=client,
                alert_ctx=alert_ctx,
            )


# ====================== entrypoint ======================


async def main_loop(database_url: str | None = None) -> None:
    start_worker_metrics_server(WORKER_NAME)
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)

    # MetaApiClient — eager-init: gRPC channel создаётся без блокировки;
    # реальный fail только при первом ExecuteGraphCallV5, маршрутизируется в requeue.
    meta_client = _build_meta_client(engine)
    await meta_client.start()

    # CRITICAL-алерт «канал auto-stop мёртв» (#2). Рассылается всем recipients через engine.
    alert_ctx = AutostopAlertContext(engine=engine)

    stop = asyncio.Event()
    wakeup = TaskQueueWakeup(
        db_url,
        task_type="meta_api_mutation",
        lanes=_CLAIM_LANES,
    )
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("meta_api_worker запущен (MetaApiClient ready)")
    try:
        await asyncio.gather(
            task_loop(
                engine,
                stop,
                client=meta_client,
                alert_ctx=alert_ctx,
                wakeup=wakeup,
            ),
            metrics_loop(stop, engine),
            wakeup.run(stop),
        )
    finally:
        try:
            await meta_client.close()
        except Exception:  # noqa: BLE001
            logger.exception("meta_client.close() упал")
        await engine.dispose()
        logger.info("meta_api_worker остановлен")
