# -*- coding: utf-8 -*-
"""campaign_creator_worker main loop — исполнение залива FB-кампаний (Волна 2, Подход A).

Состояние процесса (зеркало meta_api_worker):
- liveness: process-local Prometheus gauge (фоновый таск)
- idle: spinning poll с asyncio.sleep
- graceful: SIGTERM/SIGINT → завершить текущий цикл и закрыть ресурсы

Жизненный цикл одной задачи (task_type='campaign_create'):
    claim (FOR UPDATE SKIP LOCKED) → load CampaignRun по payload.run_id →
    queued → uniquifying → uploading → creating → execute_campaign_spec →
    succeeded (created_meta_ids) | failed.

Money-критичная классификация ошибок (через execute.classify_execution_error):
- permanent (валидация конфига, Meta permission/policy-reject) → run=failed + task mark_failed.
- transient до persisted external boundary → run=queued, task retry + backoff;
- любой неоднозначный сбой после boundary, включая первый POST без ack, → terminal
  UNKNOWN + manual review, без blind retry;
- partial-create сохраняет created_meta_ids для ручной сверки/чистки.

Money-инварианты: кампания всегда PAUSED (кривой запуск не тратит); idempotency_key
задачи (offer+date+хеш структуры) против двойного залива; budget hard-cap в CampaignConfig.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import shutil
import signal
import time
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.campaign_creator_worker import (
    _campaign_upload_root,
    _resolve_creo_dir,
    load_run,
    parse_run_config,
    resolve_concepts_from_config,
    set_run_status,
)
from apps.campaign_creator_worker import (
    claim_campaign_task as _claim,
)
from apps.campaign_creator_worker import (
    finalize_run_cancelled as _finalize_run_cancelled,
)
from apps.campaign_creator_worker import (
    finalize_run_failed as _finalize_run_failed,
)
from apps.campaign_creator_worker import (
    finalize_run_succeeded as _finalize_run_succeeded,
)
from core.campaign_builder.builder import build_campaign_spec
from core.campaign_builder.creative_ledger import record_creative
from core.campaign_builder.execute import (
    PartialCreateError,
    classify_execution_error,
    execute_campaign_spec,
)
from core.db import WORKER_ENGINE_KWARGS
from core.deadlines import bind_absolute_deadline
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import BrowserReadinessRejectedError
from core.meta_api.upload import MediaUploader
from core.public_identifiers import public_uuid
from core.safe_diagnostics import safe_exception_diagnostic
from core.tasks.irreversible_control import (
    CreatorTaskControl,
    CreatorTaskControlAbort,
    CreatorTaskFenceLost,
    run_with_task_control,
    seconds_until_deadline,
)
from core.tasks.queue import (
    Task,
    release_after_browser_readiness_rejection,
    requeue_for_retry,
)
from core.tasks.queue import (
    mark_failed as _queue_mark_failed,
)
from core.tasks.queue import (
    mark_succeeded as _queue_mark_succeeded,
)
from core.worker_metrics import (
    mark_worker_heartbeat,
    record_irreversible_safety_event,
    record_irreversible_task_outcome,
)

logger = logging.getLogger("campaign_creator_worker")

WORKER_NAME = "campaign_creator"
_METRICS_INTERVAL_SECONDS = 15.0
IDLE_SLEEP_SECONDS = 5

_PROCESS_STARTED_AT: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "campaign_creator_process_started_at",
    default=None,
)
_PROCESS_OUTCOME_RECORDED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "campaign_creator_process_outcome_recorded",
    default=False,
)


def _public_run_log_id(value: object) -> str:
    try:
        return public_uuid(value, prefix="run")
    except (TypeError, ValueError):
        return "run_invalid"


def _begin_process_metrics() -> None:
    _PROCESS_STARTED_AT.set(time.monotonic())
    _PROCESS_OUTCOME_RECORDED.set(False)


def _record_terminal_outcome(outcome: Literal["CONFIRMED", "REJECTED", "UNKNOWN"]) -> None:
    if _PROCESS_OUTCOME_RECORDED.get():
        return
    started_at = _PROCESS_STARTED_AT.get()
    record_irreversible_task_outcome(
        WORKER_NAME,
        "campaign_create",
        outcome,
        duration_seconds=time.monotonic() - started_at if started_at is not None else None,
    )
    if outcome == "UNKNOWN":
        record_irreversible_safety_event(
            WORKER_NAME,
            "campaign_create",
            "ambiguous_no_retry",
        )
    _PROCESS_OUTCOME_RECORDED.set(True)


def _record_stale_fence() -> None:
    record_irreversible_safety_event(WORKER_NAME, "campaign_create", "stale_fence")


async def finalize_run_failed(
    engine: AsyncEngine,
    run_id: str,
    *,
    task: Task,
    error: str,
    created_meta_ids: dict[str, Any] | None = None,
    task_result: dict[str, Any] | None = None,
    progress: dict[str, Any] | None = None,
) -> bool:
    applied = await _finalize_run_failed(
        engine,
        run_id,
        task=task,
        error=error,
        created_meta_ids=created_meta_ids,
        task_result=task_result,
        progress=progress,
    )
    if applied:
        outcome = "UNKNOWN" if (task_result or {}).get("outcome") == "UNKNOWN" else "REJECTED"
        _record_terminal_outcome(outcome)
    else:
        _record_stale_fence()
    return applied


async def finalize_run_succeeded(
    engine: AsyncEngine,
    run_id: str,
    *,
    task: Task,
    created_meta_ids: dict[str, Any],
    progress: dict[str, Any],
) -> bool:
    applied = await _finalize_run_succeeded(
        engine,
        run_id,
        task=task,
        created_meta_ids=created_meta_ids,
        progress=progress,
    )
    if applied:
        _record_terminal_outcome("CONFIRMED")
    else:
        _record_stale_fence()
    return applied


async def finalize_run_cancelled(
    engine: AsyncEngine,
    run_id: str,
    *,
    task: Task,
    reason: str,
) -> bool:
    applied = await _finalize_run_cancelled(
        engine,
        run_id,
        task=task,
        reason=reason,
    )
    if applied:
        _record_terminal_outcome("REJECTED")
    else:
        _record_stale_fence()
    return applied


async def mark_failed(
    engine: AsyncEngine,
    *,
    task_id: int,
    error: str,
    result: dict[str, Any] | None = None,
    lease_owner: UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    applied = await _queue_mark_failed(
        engine,
        task_id=task_id,
        error=error,
        result=result,
        lease_owner=lease_owner,
        lease_token=lease_token,
    )
    if applied:
        outcome = "UNKNOWN" if (result or {}).get("outcome") == "UNKNOWN" else "REJECTED"
        _record_terminal_outcome(outcome)
    else:
        _record_stale_fence()
    return applied


async def mark_succeeded(
    engine: AsyncEngine,
    *,
    task_id: int,
    result: dict[str, Any] | None = None,
    lease_owner: UUID | None = None,
    lease_token: int | None = None,
) -> bool:
    applied = await _queue_mark_succeeded(
        engine,
        task_id=task_id,
        result=result,
        lease_owner=lease_owner,
        lease_token=lease_token,
    )
    if applied:
        _record_terminal_outcome("CONFIRMED")
    else:
        _record_stale_fence()
    return applied


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _build_meta_client(engine: AsyncEngine) -> MetaApiClient:
    """Сконструировать клиент Marketing API (gRPC к browser-agent)."""
    return MetaApiClient(
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
        operation_engine=engine,
    )


async def _persist_partial_created_ids(
    engine: AsyncEngine,
    *,
    task: Task,
    created_ids: dict[str, Any],
    failed_step: str,
) -> bool:
    """created_ids partial-провала — в task_queue.result, не только в логи/campaign_run.

    Урок MID-24: у 8 старых failed-задач result был NULL, а id осиротевших объектов
    Meta жили только в ротируемых логах. campaign_run.created_meta_ids уже пишется
    (finalize_run_failed), но разбор очереди смотрит в task_queue — дублируем сюда.
    Пишем ДО mark_failed (guard status='running' тот же); best-effort — сбой записи
    не должен помешать mark_failed.
    """
    payload = {
        "outcome": "UNKNOWN",
        "reconcile_required": True,
        "manual_review_required": True,
        "operation": "campaign_create",
        "partial_fail": True,
        "failed_step": failed_step,
        "created_ids": created_ids,
    }
    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "UPDATE task_queue SET result = CAST(:r AS JSONB), updated_at = NOW() "
                    "WHERE id = :id AND status = 'running' "
                    "AND lease_owner = :lease_owner AND lease_token = :lease_token "
                    "AND lease_expires_at > clock_timestamp()"
                ),
                {
                    "id": task.id,
                    "r": json.dumps(payload),
                    "lease_owner": task.lease_owner,
                    "lease_token": task.lease_token,
                },
            )
        return (result.rowcount or 0) > 0
    except Exception as exc:  # noqa: BLE001 — best-effort, mark_failed важнее
        logger.warning(
            "campaign_create: не удалось записать created_ids в task_queue.result (task=%s, %s)",
            task.id,
            safe_exception_diagnostic(exc),
        )
        return False


class _FencedGraphClient:
    """Persist/check the task boundary immediately before every Meta RPC."""

    def __init__(self, delegate: MetaApiClient, control: CreatorTaskControl) -> None:
        self._delegate = delegate
        self._control = control

    async def execute_graph_call(self, **kwargs: Any) -> dict[str, Any]:
        method = str(kwargs.get("method") or "CALL").upper()
        endpoint = str(kwargs.get("endpoint") or "unknown")
        await self._control.begin_external(f"Meta {method} {endpoint}")
        record_irreversible_safety_event(
            WORKER_NAME,
            "campaign_create",
            "external_boundary",
        )
        return await self._delegate.execute_graph_call(**kwargs)


class _FencedUploader:
    """Apply the same task boundary to MediaUploader external methods."""

    def __init__(self, delegate: MediaUploader, control: CreatorTaskControl) -> None:
        self._delegate = delegate
        self._control = control

    async def upload_image(self, *args: Any, **kwargs: Any) -> str:
        await self._control.begin_external("MediaUploader.upload_image")
        record_irreversible_safety_event(WORKER_NAME, "campaign_create", "external_boundary")
        return await self._delegate.upload_image(*args, **kwargs)

    async def upload_video_from_bytes(self, *args: Any, **kwargs: Any) -> str:
        await self._control.begin_external("MediaUploader.upload_video_from_bytes")
        record_irreversible_safety_event(WORKER_NAME, "campaign_create", "external_boundary")
        return await self._delegate.upload_video_from_bytes(*args, **kwargs)

    async def wait_video_ready(self, *args: Any, **kwargs: Any) -> bool:
        await self._control.begin_external("MediaUploader.wait_video_ready")
        record_irreversible_safety_event(WORKER_NAME, "campaign_create", "external_boundary")
        return await self._delegate.wait_video_ready(*args, **kwargs)

    async def get_video_thumbnail_url(self, *args: Any, **kwargs: Any) -> str:
        await self._control.begin_external("MediaUploader.get_video_thumbnail_url")
        record_irreversible_safety_event(WORKER_NAME, "campaign_create", "external_boundary")
        return await self._delegate.get_video_thumbnail_url(*args, **kwargs)


def _campaign_unknown_result(
    task: Task,
    *,
    run_id: str,
    reason: str,
    created_ids: dict[str, Any] | None = None,
    failed_step: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "outcome": "UNKNOWN",
        "reconcile_required": True,
        "manual_review_required": True,
        "operation": "campaign_create",
        "run_id": run_id,
        "reason": reason,
    }
    if created_ids is not None:
        result["created_ids"] = created_ids
    if failed_step is not None:
        result["failed_step"] = failed_step
    if task.correlation_id is not None:
        result["correlation_id"] = str(task.correlation_id)
    return result


def _campaign_rejected_result(*, run_id: str, reason: str) -> dict[str, Any]:
    return {
        "outcome": "REJECTED",
        "operation": "campaign_create",
        "run_id": run_id,
        "reason": reason,
    }


def _browser_readiness_rejection(
    exc: BaseException,
) -> BrowserReadinessRejectedError | None:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, BrowserReadinessRejectedError):
            return current
        seen.add(id(current))
        current = current.__cause__
    return None


async def _finalize_campaign_control_abort(
    engine: AsyncEngine,
    task: Task,
    control: CreatorTaskControl,
    *,
    run_id: str,
    exc: CreatorTaskControlAbort,
) -> None:
    if exc.external_started or control.external_started:
        await finalize_run_failed(
            engine,
            run_id,
            task=task,
            error=f"campaign creation interrupted after external boundary: {exc.reason}",
            task_result=_campaign_unknown_result(
                task,
                run_id=run_id,
                reason=exc.reason,
            ),
            progress={"stage": "failed", "outcome": "UNKNOWN", "reason": exc.reason},
        )
        return
    record_irreversible_safety_event(
        WORKER_NAME,
        "campaign_create",
        "pre_boundary_stop",
    )
    if exc.reason == "cancel_requested":
        applied = await finalize_run_cancelled(
            engine,
            run_id,
            task=task,
            reason=task.cancel_reason or "campaign creation cancelled before external call",
        )
        if not applied:
            logger.warning("campaign_create: pre-boundary cancel lost fence task=%s", task.id)
        return
    await finalize_run_failed(
        engine,
        run_id,
        task=task,
        error="absolute task deadline exceeded before first Meta call",
        task_result=_campaign_rejected_result(
            run_id=run_id,
            reason="absolute_deadline_exceeded_before_external_call",
        ),
        progress={"stage": "failed", "outcome": "REJECTED", "reason": exc.reason},
    )


# ====================== обработка одной задачи ======================


async def process_one_task(
    engine: AsyncEngine,
    task: Task,
    *,
    client: MetaApiClient | None = None,
    uploader: MediaUploader | None = None,
) -> None:
    """Полный жизненный цикл одной задачи campaign_create.

    client/uploader опциональны для тестов; production main_loop всегда передаёт реальные.
    Result задачи в task_queue синхронизирован со статусом campaign_run.
    """
    _begin_process_metrics()
    run_id = (task.payload or {}).get("run_id")
    if not run_id:
        logger.error("campaign_create: task id=%s без run_id в payload", task.id)
        await _safe_mark_failed(
            engine,
            task,
            "invalid payload: нет run_id",
            result={"outcome": "REJECTED", "reason": "missing_run_id"},
        )
        return

    run = await load_run(engine, str(run_id))
    if run is None:
        logger.error(
            "campaign_create: task id=%s run=%s не найден",
            task.id,
            _public_run_log_id(run_id),
        )
        await _safe_mark_failed(
            engine,
            task,
            "campaign_run не найден",
            result=_campaign_rejected_result(run_id=str(run_id), reason="run_not_found"),
        )
        return

    # Уже терминальный run (succeeded/failed/cancelled) — задача-дубль/повтор после
    # reconciler-таймаута. Не переисполняем money-залив: закрываем задачу.
    if run.status in ("succeeded", "failed", "cancelled"):
        logger.warning(
            "campaign_create: task id=%s run %s уже в терминале (%s) — пропускаю",
            task.id,
            _public_run_log_id(run_id),
            run.status,
        )
        if run.status == "succeeded":
            await mark_succeeded(
                engine,
                task_id=task.id,
                result={"outcome": "CONFIRMED", "run_id": str(run_id)},
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
        else:
            await mark_failed(
                engine,
                task_id=task.id,
                error=f"run уже {run.status}",
                result=_campaign_rejected_result(
                    run_id=str(run_id), reason=f"run_already_{run.status}"
                ),
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
            )
        return

    # Money-safety (HIGH-2/HIGH-3): run уже В РАБОТЕ (uniquifying/uploading/creating)
    # ИЛИ уже имеет созданные Meta-объекты. Это значит, что другой воркер (live-zombie:
    # claim тем же task'ом без status-перехода) или прошлый запуск уже начал НЕОБРАТИМЫЙ
    # залив. Повторное исполнение = дубль кампании + двойной открут бюджета. НЕ исполняем:
    # уводим run+task в failed (осиротевшие объекты — на ручную проверку, retry запрещён).
    if run.status in ("uniquifying", "uploading", "creating") or await _run_has_created_meta_ids(
        engine, str(run_id)
    ):
        logger.error(
            "campaign_create: task id=%s run %s уже в работе/с созданными объектами "
            "(status=%s) — НЕ переисполняю (риск дубля кампании), помечаю failed",
            task.id,
            _public_run_log_id(run_id),
            run.status,
        )
        applied = await finalize_run_failed(
            engine,
            str(run_id),
            task=task,
            error=(
                f"run уже в работе/с созданными объектами (status={run.status}) — "
                "повторное исполнение запрещено (риск дубля кампании), проверь Meta вручную"
            ),
            task_result=_campaign_unknown_result(
                task,
                run_id=str(run_id),
                reason="preexisting_in_progress_or_created_objects",
            ),
            progress={
                "stage": "failed",
                "outcome": "UNKNOWN",
                "reason": "preexisting_in_progress_or_created_objects",
            },
        )
        if not applied:
            logger.warning(
                "campaign_create: stale fence while rejecting re-execute task=%s", task.id
            )
        return

    control = CreatorTaskControl(
        engine=engine,
        task=task,
        operation="campaign_create",
        target_id=str(run_id),
    )
    try:
        await control.check()
    except CreatorTaskControlAbort as exc:
        await _finalize_campaign_control_abort(engine, task, control, run_id=str(run_id), exc=exc)
        return
    except CreatorTaskFenceLost:
        _record_stale_fence()
        logger.warning("campaign_create: stale task fence before execution task=%s", task.id)
        return

    if control.external_started:
        applied = await finalize_run_failed(
            engine,
            str(run_id),
            task=task,
            error="task was claimed with a pre-existing external boundary",
            task_result=_campaign_unknown_result(
                task,
                run_id=str(run_id),
                reason="preexisting_external_boundary",
            ),
            progress={
                "stage": "failed",
                "outcome": "UNKNOWN",
                "reason": "preexisting_external_boundary",
            },
        )
        if not applied:
            logger.warning(
                "campaign_create: stale fence while rejecting pre-existing boundary task=%s",
                task.id,
            )
        return

    if client is None or uploader is None:
        logger.error("campaign_create: task id=%s — Meta-клиент/uploader не доступен", task.id)
        await finalize_run_failed(
            engine,
            str(run_id),
            task=task,
            error="MetaApiClient/MediaUploader не доступен (Vision?)",
            task_result=_campaign_rejected_result(
                run_id=str(run_id), reason="creator_dependencies_unavailable"
            ),
        )
        return

    await _execute_run(
        engine,
        task,
        run_id=str(run_id),
        config=run.config,
        client=client,
        uploader=uploader,
        control=control,
    )


async def _execute_run(
    engine: AsyncEngine,
    task: Task,
    *,
    run_id: str,
    config: dict[str, Any],
    client: MetaApiClient,
    uploader: MediaUploader,
    control: CreatorTaskControl,
) -> None:
    """Гоняет execute с записью прогресса/статуса и маршрутизацией ошибок."""
    # 1) Валидация конфига (pydantic) — permanent при ошибке.
    try:
        cfg = parse_run_config(config)
        concepts_by_campaign = resolve_concepts_from_config(cfg)
        spec = build_campaign_spec(cfg)
    except Exception as exc:  # noqa: BLE001 — валидация конфига/концептов = permanent
        logger.error(
            "campaign_create: task id=%s конфиг невалиден (%s)",
            task.id,
            safe_exception_diagnostic(exc),
        )
        await finalize_run_failed(
            engine,
            run_id,
            task=task,
            error=f"invalid config: {safe_exception_diagnostic(exc)}",
            task_result=_campaign_rejected_result(run_id=run_id, reason="invalid_config"),
        )
        return

    # Атомарный queued→uniquifying (cancel-гонка): если конкурентный cancel успел перевести
    # run в cancelled, переход НЕ пройдёт (expect='queued') → прерываемся ДО любого создания
    # объектов в Meta. Задачу терминируем как succeeded (обработана: run отменён, создавать нечего).
    if not await set_run_status(
        engine,
        run_id,
        "uniquifying",
        task=task,
        progress={"stage": "uniquifying"},
        expect="queued",
    ):
        try:
            await control.check()
        except CreatorTaskControlAbort as exc:
            await _finalize_campaign_control_abort(engine, task, control, run_id=run_id, exc=exc)
            return
        except CreatorTaskFenceLost:
            _record_stale_fence()
            logger.warning(
                "campaign_create: queued transition rejected by stale fence task=%s",
                task.id,
            )
            return
        logger.info(
            "campaign_create: task id=%s — run %s отменён до старта (cancel-гонка), пропуск без создания",
            task.id,
            _public_run_log_id(run_id),
        )
        await _safe_mark_failed(
            engine,
            task,
            "run отменён до старта (cancel-гонка) — пропуск без создания",
            result=_campaign_rejected_result(
                run_id=run_id, reason="run_cancelled_before_external_call"
            ),
        )
        _cleanup_upload_dir(cfg.creo_root)
        return

    async def on_progress(snapshot: dict[str, Any]) -> None:
        # Прогресс execute → status + progress run. Стадии execute
        # (uniquifying/uploading/creating) маппятся 1:1 в статус run. Best-effort,
        # не роняет залив (execute ловит).
        stage = snapshot.get("stage", "creating")
        run_status = stage if stage in ("uniquifying", "uploading", "creating") else "creating"
        applied = await set_run_status(
            engine,
            run_id,
            run_status,
            task=task,
            progress=snapshot,
        )
        if not applied:
            # ``set_run_status`` deliberately rejects a row after an operator
            # sets cancel_requested_at.  Re-read the DB-authoritative control
            # state before calling this a stale fence: cooperative abort must
            # finalize REJECTED/UNKNOWN, not strand the task as ``running``.
            await control.check()
            raise CreatorTaskFenceLost(
                f"campaign progress rejected for stale task fence task={task.id}"
            )

    async def _record(code: str, kind: str, creative_id: str) -> None:
        # Реестр — best-effort аудит: его сбой не должен ронять успешный залив.
        try:
            await control.check()
            async with engine.begin() as conn:
                await record_creative(
                    conn,
                    offer_code=cfg.offer_code,
                    code=code,
                    kind=kind,
                    meta_creative_id=creative_id,
                    run_id=run_id,
                )
        except Exception as exc:  # noqa: BLE001 — best-effort аудит
            logger.warning(
                "реестр креатива не записан: code=%s (%s)",
                code,
                safe_exception_diagnostic(exc),
            )

    fenced_client = _FencedGraphClient(client, control)
    fenced_uploader = _FencedUploader(uploader, control)
    timeout_seconds = seconds_until_deadline(task.deadline_at)

    async def _execute() -> Any:
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign=concepts_by_campaign,
            client=fenced_client,
            uploader=fenced_uploader,
            on_progress=on_progress,
            on_creative_created=_record,
        )

    try:
        with bind_absolute_deadline(task.deadline_at):
            async with asyncio.timeout(timeout_seconds):
                result = await run_with_task_control(control, _execute)
    except CreatorTaskControlAbort as exc:
        await _finalize_campaign_control_abort(engine, task, control, run_id=run_id, exc=exc)
        logger.warning(
            "campaign_create: task=%s stopped reason=%s external=%s",
            task.id,
            exc.reason,
            exc.external_started,
        )
        return
    except CreatorTaskFenceLost:
        _record_stale_fence()
        logger.warning(
            "campaign_create: task=%s lost lease; active external work cancelled",
            task.id,
        )
        return
    except asyncio.TimeoutError:
        if control.external_started:
            await finalize_run_failed(
                engine,
                run_id,
                task=task,
                error="campaign creation exceeded absolute deadline after external boundary",
                task_result=_campaign_unknown_result(
                    task, run_id=run_id, reason="absolute_deadline_exceeded"
                ),
                progress={
                    "stage": "failed",
                    "outcome": "UNKNOWN",
                    "reason": "absolute_deadline_exceeded",
                },
            )
        else:
            await finalize_run_failed(
                engine,
                run_id,
                task=task,
                error="absolute task deadline exceeded before first Meta call",
                task_result=_campaign_rejected_result(
                    run_id=run_id,
                    reason="absolute_deadline_exceeded_before_external_call",
                ),
            )
        return
    except PartialCreateError as exc:
        # Часть объектов уже в Meta — НЕ ретраим (дубли). run=failed + осиротевшие id.
        logger.error(
            "campaign_create: task id=%s PARTIAL FAIL — осиротевшие объекты в Meta, "
            "нужна ручная чистка! created_ids=%s step=%s",
            task.id,
            exc.created_ids,
            exc.failed_step,
        )
        await _persist_partial_created_ids(
            engine,
            task=task,
            created_ids=exc.created_ids,
            failed_step=exc.failed_step,
        )
        await finalize_run_failed(
            engine,
            run_id,
            task=task,
            error=(
                f"partial_fail (step={exc.failed_step}): проверь Meta вручную; "
                f"{safe_exception_diagnostic(exc)}"
            ),
            created_meta_ids=exc.created_ids,
            task_result=_campaign_unknown_result(
                task,
                run_id=run_id,
                reason="partial_or_ack_lost",
                created_ids=exc.created_ids,
                failed_step=exc.failed_step,
            ),
            progress={
                "stage": "failed",
                "outcome": "UNKNOWN",
                "reason": "partial_or_ack_lost",
                "failed_step": exc.failed_step,
            },
        )
        # Концепты НЕ чистим при ошибке — нужны для ретрая (повтор залива тем же config).
        # Старые upload-папки подметает retention в cleanup_worker.
        return
    except Exception as exc:  # noqa: BLE001 — единая маршрутизация по classify
        readiness_rejection = _browser_readiness_rejection(exc)
        if readiness_rejection is not None:

            async def reset_run_for_readiness(
                conn,
                task_status: str,
            ) -> None:
                run_status = "cancelled" if task_status == "cancelled" else "queued"
                progress = {
                    "stage": run_status,
                    "reason": "browser_readiness_rejected",
                }
                updated = await conn.execute(
                    text(
                        """
                        UPDATE campaign_run
                        SET status = :status,
                            progress = CAST(:progress AS jsonb),
                            updated_at = clock_timestamp()
                        WHERE id = :run_id
                        """
                    ),
                    {
                        "status": run_status,
                        "progress": json.dumps(progress),
                        "run_id": run_id,
                    },
                )
                if (updated.rowcount or 0) != 1:
                    raise RuntimeError("campaign run disappeared during readiness release")

            released = await release_after_browser_readiness_rejection(
                engine,
                task=task,
                error=safe_exception_diagnostic(readiness_rejection),
                transactional_effect=reset_run_for_readiness,
            )
            if released == "retrying":
                logger.warning(
                    "campaign_create: task=%s browser readiness rejected; "
                    "released without attempt burn",
                    task.id,
                )
            elif released != "cancelled":
                logger.warning(
                    "campaign_create: task=%s readiness release lost its fence",
                    task.id,
                )
            return
        if control.external_started:
            await finalize_run_failed(
                engine,
                run_id,
                task=task,
                error=(
                    f"ambiguous failure after external boundary: {safe_exception_diagnostic(exc)}"
                ),
                task_result=_campaign_unknown_result(
                    task, run_id=run_id, reason="external_result_ambiguous"
                ),
                progress={
                    "stage": "failed",
                    "outcome": "UNKNOWN",
                    "reason": "external_result_ambiguous",
                },
            )
            return
        kind = classify_execution_error(exc)
        if kind == "transient":
            # Money-safety: transient по classify_execution_error возможен ТОЛЬКО до
            # инициации POST campaign (irreversible_attempted=False) — объект гарантированно
            # не создан. Сбрасываем run обратно в 'queued' ПЕРЕД requeue: иначе он застрял
            # в 'uniquifying' (set_run_status выше), и re-claim guard в process_one_task
            # («run уже в работе» → failed) зарубил бы легитимный transient-retry.
            if task.attempt_count + 1 >= task.max_attempts:
                await finalize_run_failed(
                    engine,
                    run_id,
                    task=task,
                    error=(
                        "transient exhausted before external call: "
                        f"{safe_exception_diagnostic(exc)}"
                    ),
                    task_result=_campaign_rejected_result(
                        run_id=run_id, reason="pre_external_attempts_exhausted"
                    ),
                )
                return
            reset = await set_run_status(engine, run_id, "queued", task=task)
            if not reset:
                logger.warning(
                    "campaign_create: run reset rejected by stale fence task=%s", task.id
                )
                return
            # Сеть/rate-limit/Vision — задача в requeue с backoff, run снова queued.
            retried = await requeue_for_retry(
                engine,
                task_id=task.id,
                error=safe_exception_diagnostic(exc),
                attempt_count=task.attempt_count,
                max_attempts=task.max_attempts,
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
                lane=task.lane,
            )
            if retried:
                logger.warning(
                    "campaign_create: task id=%s → retrying (transient; %s)",
                    task.id,
                    safe_exception_diagnostic(exc),
                )
            else:
                logger.error(
                    "campaign_create: task id=%s → исчерпаны попытки (transient; %s)",
                    task.id,
                    safe_exception_diagnostic(exc),
                )
            return
        # permanent: валидация/Meta permission/policy → run=failed, без retry.
        logger.error(
            "campaign_create: task id=%s → permanent fail (%s)",
            task.id,
            safe_exception_diagnostic(exc),
        )
        await finalize_run_failed(
            engine,
            run_id,
            task=task,
            error=f"permanent before external call: {safe_exception_diagnostic(exc)}",
            task_result=_campaign_rejected_result(
                run_id=run_id, reason="permanent_pre_external_failure"
            ),
        )
        # Концепты НЕ чистим — оставляем для ретрая (retention подметёт старое).
        return

    # Успех: created_meta_ids в run + task succeeded.
    final_progress = {"stage": "succeeded", **result.created_meta_ids}
    applied = await finalize_run_succeeded(
        engine,
        run_id,
        task=task,
        created_meta_ids=result.created_meta_ids,
        progress=final_progress,
    )
    if not applied:
        logger.warning(
            "campaign_create: atomic success rejected by stale fence task id=%s",
            task.id,
        )
        return
    logger.info(
        "campaign_create: task id=%s succeeded (run %s)",
        task.id,
        _public_run_log_id(run_id),
    )
    _cleanup_upload_dir(cfg.creo_root)


async def _safe_mark_failed(
    engine: AsyncEngine,
    task: Task,
    error: str,
    *,
    result: dict[str, Any] | None = None,
) -> None:
    """mark_failed с логом гонки (status != running)."""
    applied = await mark_failed(
        engine,
        task_id=task.id,
        error=error,
        result=result or {"outcome": "REJECTED"},
        lease_owner=task.lease_owner,
        lease_token=task.lease_token,
    )
    if not applied:
        logger.warning(
            "campaign_create: task id=%s mark_failed не применился (гонка с воркером)", task.id
        )


def _cleanup_upload_dir(creo_root: str | None) -> None:
    """Best-effort удаление папки загруженных концептов на УСПЕХЕ/ОТМЕНЕ прогона.

    Оригиналы фото/видео нужны до успешного залива (уникализированные байты уже ушли в
    Meta) ИЛИ для ретрая после ошибки. Поэтому при ошибке (partial/permanent/exhausted)
    папку НЕ чистим — пользователь может «Повторить залив» тем же config; старые папки
    подметает retention в cleanup_worker. Зовётся только при success и cancel-гонке.
    Защита: upload ID резолвится только как подпапка внутри корня загрузок.
    Сбой не роняет задачу.
    """
    if not creo_root:
        return
    try:
        target = _resolve_creo_dir(creo_root).resolve()
        root = _campaign_upload_root().resolve()
        if root not in target.parents:
            return  # путь вне корня загрузок — не наш, не трогаем
        shutil.rmtree(target, ignore_errors=True)
        logger.info("campaign_create: upload-папка прогона очищена")
    except Exception as exc:  # noqa: BLE001 — best-effort, не роняет обработку задачи
        logger.warning(
            "campaign_create: не удалось очистить upload-папку (%s)",
            safe_exception_diagnostic(exc),
        )


def _sweep_stale_upload_dirs(max_age_days: float = 7.0) -> None:
    """Retention: подметает upload-папки старше max_age_days.

    При ошибке залива папку концептов оставляем для «Повторить залив», поэтому без
    подметания неуспешные/заброшенные папки копились бы. Зовётся при старте воркера
    (он рестартится на деплоях — достаточно часто). Best-effort, не роняет старт.
    """
    root = _campaign_upload_root()
    if not root.exists():
        return
    cutoff = time.time() - max_age_days * 86400.0
    removed = 0
    try:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
    except OSError:
        return
    if removed:
        logger.info(
            "campaign_create: retention — удалено %d upload-папок старше %.0fд",
            removed,
            max_age_days,
        )


async def _run_has_created_meta_ids(engine: AsyncEngine, run_id: str) -> bool:
    """True, если у campaign_run уже есть хоть один созданный Meta-объект.

    created_meta_ids — JSONB вида {"campaigns": [...], "adsets": [...], ...}. Любой
    непустой список означает, что необратимый залив уже начался → переисполнять нельзя.
    Дешёвый guard поверх status-проверки (belt-and-suspenders против live-zombie/reclaim).
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM campaign_run,
                             jsonb_each(COALESCE(created_meta_ids, '{}'::jsonb)) AS kv
                        WHERE id = :rid
                          AND jsonb_typeof(kv.value) = 'array'
                          AND jsonb_array_length(kv.value) > 0
                    )
                    """
                ),
                {"rid": run_id},
            )
        ).first()
    return bool(row[0]) if row else False


# ====================== sub-loops ======================


async def metrics_loop(stop: asyncio.Event) -> None:
    """Refresh the process-local Prometheus liveness gauge."""
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        try:
            await asyncio.wait_for(stop.wait(), timeout=_METRICS_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def task_loop(
    engine: AsyncEngine,
    stop: asyncio.Event,
    *,
    client: MetaApiClient,
    uploader: MediaUploader,
) -> None:
    """Главный цикл claim → execute → mark."""
    while not stop.is_set():
        try:
            claim = await _claim(engine)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "ошибка claim_campaign_task (%s)",
                safe_exception_diagnostic(exc),
            )
            await _sleep_or_stop(stop)
            continue

        if claim.queue_empty or claim.task is None:
            await _sleep_or_stop(stop)
            continue

        try:
            vision_profile_id = str(claim.browser_profile_id or "").strip()
            if not vision_profile_id:
                raise RuntimeError(
                    "browser-ready campaign claim returned no canonical Vision profile"
                )
            with client.operation_authority(
                caller="campaign_creator",
                task_id=claim.task.id,
                lease_owner=claim.task.lease_owner,
                lease_token=claim.task.lease_token,
                vision_profile_id=vision_profile_id,
                browser_readiness_generation=claim.browser_readiness_generation,
            ):
                await process_one_task(engine, claim.task, client=client, uploader=uploader)
        except Exception as exc:  # noqa: BLE001 — неожиданная ошибка (напр. БД в фазе pre-execute гардов)
            logger.error(
                "campaign_create: unexpected crash task id=%s — terminal UNKNOWN (%s)",
                claim.task.id,
                safe_exception_diagnostic(exc),
            )
            run_id = str((claim.task.payload or {}).get("run_id") or "")
            try:
                if run_id:
                    await finalize_run_failed(
                        engine,
                        run_id,
                        task=claim.task,
                        error="unexpected campaign creator worker crash",
                        task_result=_campaign_unknown_result(
                            claim.task,
                            run_id=run_id,
                            reason="unexpected_worker_crash",
                        ),
                        progress={
                            "stage": "failed",
                            "outcome": "UNKNOWN",
                            "reason": "unexpected_worker_crash",
                        },
                    )
                else:
                    await _safe_mark_failed(
                        engine,
                        claim.task,
                        "unexpected campaign creator worker crash",
                        result={
                            "outcome": "UNKNOWN",
                            "reconcile_required": True,
                            "manual_review_required": True,
                            "reason": "unexpected_worker_crash",
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "campaign_create: UNKNOWN finalize also failed task=%s (%s)",
                    claim.task.id,
                    safe_exception_diagnostic(exc),
                )
            await _sleep_or_stop(stop)


async def _sleep_or_stop(stop: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
    except asyncio.TimeoutError:
        pass


# ====================== entrypoint ======================


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)

    meta_client = _build_meta_client(engine)
    await meta_client.start()
    uploader = MediaUploader(meta_client)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("campaign_creator_worker запущен (MetaApiClient ready)")
    _sweep_stale_upload_dirs()  # retention старых upload-папок (концепты неуспешных заливов)
    try:
        await asyncio.gather(
            task_loop(engine, stop, client=meta_client, uploader=uploader),
            metrics_loop(stop),
        )
    finally:
        try:
            await meta_client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "meta_client.close() не завершён (%s)",
                safe_exception_diagnostic(exc),
            )
        await engine.dispose()
        logger.info("campaign_creator_worker остановлен")
