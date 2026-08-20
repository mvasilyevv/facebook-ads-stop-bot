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
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.campaign_creator_worker import (
    TASK_TYPE,
    _campaign_upload_root,
    _resolve_creo_dir,
    load_run,
    parse_run_config,
    referenced_creo_roots,
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
from apps.campaign_creator_worker import (
    note_waiting_reason as _note_waiting_reason,
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
from core.meta_api.errors import (
    BrowserReadinessRejectedError,
    LoginRequiredError,
    MetaApiError,
    PreDispatchRejectedError,
)
from core.meta_api.upload import MediaUploader
from core.metrics import record_campaign_upload_storage
from core.observer.login_required import notify_login_required_incident_in_transaction
from core.tasks.action_reason import campaign_operator_reason
from core.tasks.irreversible_control import (
    CreatorTaskControl,
    CreatorTaskControlAbort,
    CreatorTaskFenceLost,
    run_with_task_control,
    seconds_until_deadline,
)
from core.tasks.queue import (
    Task,
    explain_browser_claim_block,
    release_after_browser_readiness_rejection,
    requeue_for_retry,
)
from core.tasks.queue import (
    mark_failed as _queue_mark_failed,
)
from core.tasks.queue import (
    mark_succeeded as _queue_mark_succeeded,
)
from core.telegram.worker_notify import notify_recurring_incident, resolve_recurring_incident
from core.wording import human_bytes_ru
from core.worker_liveness import poll_heartbeat_while_running, record_worker_heartbeat
from core.worker_metrics import (
    mark_worker_heartbeat,
    record_irreversible_safety_event,
    record_irreversible_task_outcome,
)

logger = logging.getLogger("campaign_creator_worker")

WORKER_NAME = "campaign_creator"
_METRICS_INTERVAL_SECONDS = 15.0
IDLE_SLEEP_SECONDS = 5

# ------------------- upload-store retention (issues #190, #192) -------------------

# Возраст, после которого upload-папка подметается, если не занята активным/
# повторяемым прогоном. Переопределимо на хосте — там, где размер набора
# крупнее обычного, ретеншн можно ослабить без релиза.
_UPLOAD_MAX_AGE_DAYS_ENV = "CAMPAIGN_UPLOAD_MAX_AGE_DAYS"
_DEFAULT_UPLOAD_MAX_AGE_DAYS = 7.0

# Предел суммарного объёма CAMPAIGN_UPLOAD_ROOT. При превышении подметаются
# самые старые НЕзанятые наборы, пока объём не уложится в предел (issue #190).
_UPLOAD_MAX_TOTAL_BYTES_ENV = "CAMPAIGN_UPLOAD_MAX_TOTAL_BYTES"
_DEFAULT_UPLOAD_MAX_TOTAL_BYTES = 5 * 1024**3  # 5 GiB
# Набор моложе этого возраста предел по объёму не трогает: папка появляется на диске
# в POST /upload раньше строки campaign_run, поэтому «на набор никто не ссылается» в
# первые минуты означает «его прямо сейчас загружают», а не «он ничей». Час с запасом
# перекрывает загрузку 500 МБ на медленном канале.
_UPLOAD_CAP_MIN_AGE_SECONDS = 3600.0

_UPLOAD_STORAGE_CHECK_INTERVAL_SECONDS = 15 * 60.0
CAMPAIGN_UPLOAD_STORAGE_INCIDENT_KEY = "storage:campaign-upload"

_PROCESS_STARTED_AT: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "campaign_creator_process_started_at",
    default=None,
)
_PROCESS_OUTCOME_RECORDED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "campaign_creator_process_outcome_recorded",
    default=False,
)


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
    login_required_ad_account_id: str | None = None,
) -> bool:
    applied = await _finalize_run_failed(
        engine,
        run_id,
        task=task,
        error=error,
        created_meta_ids=created_meta_ids,
        task_result=task_result,
        progress=progress,
        login_required_ad_account_id=login_required_ad_account_id,
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
    pre_dispatch: bool | None = None,
    pre_dispatch_reason_code: str | None = None,
    operator_reason: str | None = None,
) -> bool:
    """created_ids partial-провала — в task_queue.result, не только в логи/campaign_run.

    Урок MID-24: у 8 старых failed-задач result был NULL, а id осиротевших объектов
    Meta жили только в ротируемых логах. campaign_run.created_meta_ids уже пишется
    (finalize_run_failed), но разбор очереди смотрит в task_queue — дублируем сюда.
    Пишем ДО mark_failed (guard status='running' тот же); best-effort — сбой записи
    не должен помешать mark_failed.
    """
    payload: dict[str, Any] = {
        "outcome": "UNKNOWN",
        "reconcile_required": True,
        "manual_review_required": True,
        "operation": "campaign_create",
        "partial_fail": True,
        "failed_step": failed_step,
        "created_ids": created_ids,
    }
    # Признак пишем только доказанный: его отсутствие означает «неизвестно».
    if pre_dispatch is not None:
        payload["pre_dispatch"] = bool(pre_dispatch)
    if pre_dispatch_reason_code:
        payload["pre_dispatch_reason_code"] = pre_dispatch_reason_code
    # Промежуточная запись живёт до finalize_run_failed, но оператор может
    # открыть очередь именно в этот момент. Без причины он увидит «неизвестна»
    # там, где она уже известна.
    if operator_reason:
        payload["operator_reason"] = operator_reason
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
    except Exception:  # noqa: BLE001 — best-effort, mark_failed важнее
        logger.warning(
            "campaign_create: не удалось записать created_ids в task_queue.result (task=%s)",
            task.id,
            exc_info=True,
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
    pre_dispatch: bool | None = None,
    pre_dispatch_reason_code: str | None = None,
    diagnostics: dict[str, Any] | None = None,
    exc: BaseException | None = None,
) -> dict[str, Any]:
    """Результат неизвестного исхода: сверка обязательна, признаки — только доказанные.

    pre_dispatch=True — доказано, что последний отказ был до отправки запроса в Meta.
    None (ключа нет) — неизвестно; исход UNKNOWN и ручная сверка от признака не зависят.

    pre_dispatch_reason_code — код названной причины этого отказа; из него карточка
    инцидента берёт человеческий текст. Без причины ключа нет: «неизвестно» остаётся
    неизвестным, а не превращается в пустую строку у оператора.

    diagnostics — структурная причина отказа из ``_failure_diagnostics`` (закрытый
    набор безопасных полей, без Graph-текста/fbtrace_id). Идёт в ``task_queue.result``
    рядом с ``reason``, чтобы разбор задачи не требовал чтения исходников.

    exc — исключение, из цепочки которого берётся санитизированный текст отказа
    Meta для операторской причины. Без него у оператора остаётся код вида
    «100/1885316», по которому причину не установить.
    """
    result: dict[str, Any] = {
        "outcome": "UNKNOWN",
        "reconcile_required": True,
        "manual_review_required": True,
        "operation": "campaign_create",
        "run_id": run_id,
        "reason": reason,
    }
    operator_reason = _operator_failure_reason(
        reason_code=reason,
        failed_step=failed_step,
        diagnostics=diagnostics,
        pre_dispatch_reason_code=pre_dispatch_reason_code,
        exc=exc,
    )
    if operator_reason:
        result["operator_reason"] = operator_reason
    if created_ids is not None:
        result["created_ids"] = created_ids
    if failed_step is not None:
        result["failed_step"] = failed_step
    if pre_dispatch is not None:
        result["pre_dispatch"] = bool(pre_dispatch)
    if pre_dispatch_reason_code:
        result["pre_dispatch_reason_code"] = pre_dispatch_reason_code
    if diagnostics:
        result["diagnostics"] = diagnostics
    if task.correlation_id is not None:
        result["correlation_id"] = str(task.correlation_id)
    return result


def _campaign_rejected_result(
    *,
    run_id: str,
    reason: str,
    failed_step: str | None = None,
    exc: BaseException | None = None,
) -> dict[str, Any]:
    """Результат доказанного отказа: побочного эффекта нет, причина названа.

    ``operator_reason`` пишется здесь же, а не выводится потом из состояния:
    отказ по исчерпанному дедлайну и отказ по недоступному браузеру — разные
    события, и оператор должен видеть разный текст.
    """
    result: dict[str, Any] = {
        "outcome": "REJECTED",
        "operation": "campaign_create",
        "run_id": run_id,
        "reason": reason,
    }
    operator_reason = _operator_failure_reason(
        reason_code=reason,
        failed_step=failed_step,
        exc=exc,
    )
    if operator_reason:
        result["operator_reason"] = operator_reason
    return result


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


def _proven_pre_dispatch_rejection(exc: BaseException) -> PreDispatchRejectedError | None:
    """Найти в цепочке причин доказанный отказ ДО отправки запроса в Meta (#200).

    ``PreDispatchRejectedError`` — единственная семья, где известно наверняка, что
    конкретный внешний вызов не ушёл: execute.py заворачивает такую причину в
    ``CampaignExecutionError`` через ``__cause__``, поэтому обходим цепочку, а не
    проверяем только сам ``exc``.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, PreDispatchRejectedError):
            return current
        seen.add(id(current))
        current = current.__cause__
    return None


_FAILURE_DIAGNOSTIC_KEYS = (
    "exception_class",
    "reason_code",
    "code",
    "subcode",
    "endpoint",
    "stage",
    "failed_step",
)


def _failure_diagnostics(
    exc: BaseException,
    *,
    stage: str | None = None,
    failed_step: str | None = None,
) -> dict[str, Any]:
    """Причина отказа в закрытом наборе безопасных полей — ``_FAILURE_DIAGNOSTIC_KEYS``.

    Живая находка 20.08: ветка ack_lost строила статическую строку без причины,
    и исходное исключение не было видно нигде — ни в ``task_queue.last_error``,
    ни в ``campaign_run.error``, ни в логе. Эта функция — единая точка сбора
    причины для всех терминальных веток, которым её не хватало.

    ``MetaApiError.__repr__`` несёт ``fbtrace_id`` и полный Graph-текст (см.
    ``core/meta_api/errors.py``) — сюда он никогда не попадает: используются
    только именованные атрибуты (``code``/``subcode``/``endpoint``/
    ``reason_code``), никакого ``repr(exc)``/``str(exc)``.

    Обходим цепочку ``__cause__`` — ``execute.py`` заворачивает исходную
    причину через ``raise ... from cause`` (тот же приём, что и
    ``_proven_pre_dispatch_rejection``), поэтому самая говорящая причина обычно
    не в верхнем исключении, а глубже.
    """
    diagnostics: dict[str, Any] = {"exception_class": type(exc).__name__}
    if stage is not None:
        diagnostics["stage"] = stage
    if failed_step is not None:
        diagnostics["failed_step"] = failed_step
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        reason_code = getattr(current, "reason_code", None)
        if reason_code:
            diagnostics["reason_code"] = reason_code
            diagnostics["exception_class"] = type(current).__name__
        if isinstance(current, MetaApiError):
            diagnostics["exception_class"] = type(current).__name__
            if current.code is not None:
                diagnostics["code"] = current.code
            if current.subcode is not None:
                diagnostics["subcode"] = current.subcode
            if current.endpoint is not None:
                diagnostics["endpoint"] = current.endpoint
        current = current.__cause__
    return diagnostics


def _meta_reason(exc: BaseException) -> str | None:
    """Санитизированный текст отказа Meta из цепочки причин.

    ``MetaApiError.meta_message`` уже прошёл ``redact_sensitive_text``: токены,
    Bearer, UUID и содержимое query-строк из него вырезаны, а ``fbtrace_id`` в
    него не кладётся. Поэтому текст едет и в лог, и в операторскую причину — в
    отличие от ``str(exc)``/``repr(exc)``, которые несут Graph-сырьё.

    Без него код вида «100/1885316» — это всё, что остаётся оператору, когда
    залив встаёт (живая находка 20.08.2026).
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = getattr(current, "meta_message", None)
        if message:
            return str(message)
        current = current.__cause__
    return None


def _operator_failure_reason(
    *,
    reason_code: str,
    failed_step: str | None = None,
    diagnostics: dict[str, Any] | None = None,
    pre_dispatch_reason_code: str | None = None,
    exc: BaseException | None = None,
) -> str | None:
    """Причина отказа залива словами оператора — из события, а не из состояния.

    Шаг берётся из ``failed_step``, а если его нет — из стадии, которую уже
    собрал ``_failure_diagnostics``: обе ветки называют одно и то же место
    залива, и терять его из-за формы вызова нельзя.
    """
    facts = diagnostics or {}
    return campaign_operator_reason(
        reason_code=reason_code,
        failed_step=failed_step or facts.get("failed_step") or facts.get("stage"),
        rejection_reason_code=pre_dispatch_reason_code or facts.get("reason_code"),
        meta_message=_meta_reason(exc) if exc is not None else None,
    )


def _has_any_created_ids(created: dict[str, list[str]]) -> bool:
    return any(created.get(kind) for kind in created)


# Вердикт живой пробы предполёта (см. MetaApiClient._LIVE_PROBE_REJECT_VERDICTS),
# зашитый в текст BrowserReadinessRejectedError.
_LOGIN_REQUIRED_READINESS_VERDICT = "login_required"


def _is_proven_pre_dispatch(exc: BaseException) -> bool:
    """True когда в цепочке причин есть доказанный отказ ДО отправки запроса (#200).

    Обход именно цепочки, а не верхнего исключения: залив заворачивает причину в
    CampaignExecutionError, и проверка по типу верхнего объекта всегда давала бы
    False. Один цикл с защитой от петли — тот же приём, что в
    ``_requires_facebook_login``.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, PreDispatchRejectedError):
            return True
        # Явная отметка от слоя исполнения: транспорт подтвердил, что запрос не
        # уходил. Класс причины при этом может быть любым — например отказ
        # предохранителя, который в семью PreDispatchRejectedError не входит.
        if getattr(current, "pre_dispatch", None) is True:
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _requires_facebook_login(exc: BaseException) -> bool:
    """True когда доказанная причина отказа — нужен повторный вход в Facebook (#197).

    Две ветки одной классификации (commit 2d4e02b7): предполёт — вердикт живой
    пробы внутри BrowserReadinessRejectedError; post-dispatch — Graph уже ответил
    кодом разлогина (LoginRequiredError). Оба — money-путь наравне с
    meta_api_mutation, но до фикса ни один из них не проецировал инцидент
    «нужен повторный вход» для campaign_create.
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, LoginRequiredError):
            return True
        if isinstance(
            current, BrowserReadinessRejectedError
        ) and _LOGIN_REQUIRED_READINESS_VERDICT in str(current):
            return True
        seen.add(id(current))
        current = current.__cause__
    return False


async def _finalize_campaign_control_abort(
    engine: AsyncEngine,
    task: Task,
    control: CreatorTaskControl,
    *,
    run_id: str,
    exc: CreatorTaskControlAbort,
    created_ids: dict[str, list[str]] | None = None,
    failed_step: str | None = None,
) -> None:
    if exc.external_started or control.external_started:
        # Прерывание после внешней границы не отменяет уже созданное: перечень идёт
        # в run и в результат задачи, иначе сверять оператору нечего.
        if created_ids is not None:
            await _persist_partial_created_ids(
                engine,
                task=task,
                created_ids=created_ids,
                failed_step=failed_step or exc.reason,
            )
        await finalize_run_failed(
            engine,
            run_id,
            task=task,
            error=f"campaign creation interrupted after external boundary: {exc.reason}",
            created_meta_ids=created_ids,
            task_result=_campaign_unknown_result(
                task,
                run_id=run_id,
                reason=exc.reason,
                created_ids=created_ids,
                failed_step=failed_step,
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
        logger.error("campaign_create: task id=%s run_id=%s не найден", task.id, run_id)
        await _safe_mark_failed(
            engine,
            task,
            f"campaign_run {run_id} не найден",
            result=_campaign_rejected_result(run_id=str(run_id), reason="run_not_found"),
        )
        return

    # Уже терминальный run (succeeded/failed/cancelled) — задача-дубль/повтор после
    # reconciler-таймаута. Не переисполняем money-залив: закрываем задачу.
    if run.status in ("succeeded", "failed", "cancelled"):
        logger.warning(
            "campaign_create: task id=%s run %s уже в терминале (%s) — пропускаю",
            task.id,
            run_id,
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
            run_id,
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
    except CreatorTaskFenceLost as exc:
        _record_stale_fence()
        logger.warning(
            "campaign_create: stale task fence before execution task=%s diagnostics=%s",
            task.id,
            _failure_diagnostics(exc),
        )
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
        logger.error("campaign_create: task id=%s конфиг невалиден: %r", task.id, exc)
        await finalize_run_failed(
            engine,
            run_id,
            task=task,
            error=f"invalid config: {exc!r}",
            task_result=_campaign_rejected_result(
                run_id=run_id,
                reason="invalid_config",
                failed_step="validate",
            ),
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
        except CreatorTaskFenceLost as exc:
            _record_stale_fence()
            logger.warning(
                "campaign_create: queued transition rejected by stale fence task=%s diagnostics=%s",
                task.id,
                _failure_diagnostics(exc, stage="uniquifying"),
            )
            return
        logger.info(
            "campaign_create: task id=%s — run %s отменён до старта (cancel-гонка), пропуск без создания",
            task.id,
            run_id,
        )
        await _safe_mark_failed(
            engine,
            task,
            "run отменён до старта (cancel-гонка) — пропуск без создания",
            result=_campaign_rejected_result(
                run_id=run_id, reason="run_cancelled_before_external_call"
            ),
        )
        await _cleanup_upload_dir(engine, cfg.creo_root, run_id=run_id)
        return

    # Последняя стадия, о которой отчитался execute. Нужна, когда исход придётся
    # закрывать снаружи корутины (отмена по дедлайну) и шага падения не видно.
    last_stage = "uniquifying"

    async def on_progress(snapshot: dict[str, Any]) -> None:
        # Прогресс execute → status + progress run. Стадии execute
        # (uniquifying/uploading/creating) маппятся 1:1 в статус run. Best-effort,
        # не роняет залив (execute ловит).
        nonlocal last_stage
        stage = snapshot.get("stage", "creating")
        last_stage = str(stage)
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
        except Exception:  # noqa: BLE001 — best-effort аудит
            logger.warning(
                "реестр креатива не записан: code=%s run=%s", code, run_id, exc_info=True
            )

    fenced_client = _FencedGraphClient(client, control)
    fenced_uploader = _FencedUploader(uploader, control)
    timeout_seconds = seconds_until_deadline(task.deadline_at)
    # Накопитель созданного живёт ЗДЕСЬ, а не внутри отменяемой корутины: отмена по
    # абсолютному дедлайну и потеря lease прилетают как BaseException, и локальный
    # перечень созданного уехал бы вместе с ней — оператор получил бы UNKNOWN без
    # единого id и чистил бы кабинет вслепую.
    created_so_far: dict[str, list[str]] = {}

    async def _execute() -> Any:
        return await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign=concepts_by_campaign,
            client=fenced_client,
            uploader=fenced_uploader,
            on_progress=on_progress,
            on_creative_created=_record,
            created_sink=created_so_far,
        )

    try:
        with bind_absolute_deadline(task.deadline_at):
            async with asyncio.timeout(timeout_seconds):
                result = await run_with_task_control(control, _execute)
    except CreatorTaskControlAbort as exc:
        await _finalize_campaign_control_abort(
            engine,
            task,
            control,
            run_id=run_id,
            exc=exc,
            created_ids=created_so_far,
            failed_step=last_stage,
        )
        logger.warning(
            "campaign_create: task=%s stopped reason=%s external=%s",
            task.id,
            exc.reason,
            exc.external_started,
        )
        return
    except CreatorTaskFenceLost as exc:
        _record_stale_fence()
        logger.warning(
            "campaign_create: task=%s lost lease; active external work cancelled diagnostics=%s",
            task.id,
            _failure_diagnostics(exc, stage=last_stage),
        )
        return
    except asyncio.TimeoutError:
        if control.external_started:
            # Дедлайн отменяет корутину, но созданное в кабинете от этого не исчезает.
            # Итог остаётся UNKNOWN, а перечень созданного идёт оператору так же, как
            # при partial: чистить вслепую нечего.
            await _persist_partial_created_ids(
                engine,
                task=task,
                created_ids=created_so_far,
                failed_step=last_stage,
            )
            await finalize_run_failed(
                engine,
                run_id,
                task=task,
                error="campaign creation exceeded absolute deadline after external boundary",
                created_meta_ids=created_so_far,
                task_result=_campaign_unknown_result(
                    task,
                    run_id=run_id,
                    reason="absolute_deadline_exceeded",
                    created_ids=created_so_far,
                    failed_step=last_stage,
                ),
                progress={
                    "stage": "failed",
                    "outcome": "UNKNOWN",
                    "reason": "absolute_deadline_exceeded",
                    "failed_step": last_stage,
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
                    failed_step=last_stage,
                ),
            )
        return
    except PartialCreateError as exc:
        # #198: created пуст (ack-lost) — подтверждённых объектов НЕТ, а не «часть
        # создана». Раньше оба исхода печатали один текст «осиротевшие объекты,
        # нужна ручная чистка» и один machine reason (partial_or_ack_lost) — 19.08
        # это отправило оператора искать объекты, которых не существует.
        has_created_ids = any(exc.created_ids.get(kind) for kind in exc.created_ids)
        login_required_account_id = cfg.account.act_id if _requires_facebook_login(exc) else None
        if has_created_ids:
            # Часть объектов уже в Meta — НЕ ретраим (дубли). run=failed + осиротевшие id.
            reason = "partial_confirmed"
            logger.error(
                "campaign_create: task id=%s PARTIAL FAIL — осиротевшие объекты в Meta, "
                "нужна ручная чистка! created_ids=%s step=%s",
                task.id,
                exc.created_ids,
                exc.failed_step,
            )
            # repr исключения сюда не идёт: MetaApiError.__repr__ несёт fbtrace_id и
            # полный текст Graph-ответа. Причина собирается тем же безопасным набором
            # полей, что и в остальных терминальных ветках.
            diagnostics = _failure_diagnostics(exc, stage="creating", failed_step=exc.failed_step)
            error = (
                f"partial_fail (step={exc.failed_step}): проверь Meta вручную: cause={diagnostics}"
            )
        else:
            # POST campaign инициирован, ответ Meta потерян: ни одного id не
            # подтверждено. Чистить нечего, но повтор всё равно запрещён — объект
            # мог реально родиться в Meta без дошедшего ответа.
            reason = "ack_lost_nothing_confirmed"
            # #ack-lost: раньше эта ветка строила статическую строку без причины —
            # исходное исключение (что именно Meta не вернула: код/subcode/endpoint)
            # не было видно нигде. _failure_diagnostics — та же причина, что и в
            # exc.__cause__, в закрытом наборе безопасных полей (см. её docstring).
            diagnostics = _failure_diagnostics(exc, stage=last_stage, failed_step=exc.failed_step)
            logger.error(
                "campaign_create: task id=%s ACK LOST — ответ Meta потерян после "
                "POST кампании, подтверждённых объектов нет, нужна сверка, повтор "
                "запрещён: step=%s diagnostics=%s meta_reason=%r",
                task.id,
                exc.failed_step,
                diagnostics,
                _meta_reason(exc),
                exc_info=True,
            )
            error = (
                f"ack_lost (step={exc.failed_step}): ответ Meta потерян после POST "
                f"кампании: чистить нечего, нужна сверка, повтор запрещён. cause={diagnostics}"
            )
        await _persist_partial_created_ids(
            engine,
            task=task,
            created_ids=exc.created_ids,
            failed_step=exc.failed_step,
            pre_dispatch=exc.pre_dispatch,
            pre_dispatch_reason_code=exc.pre_dispatch_reason_code,
            operator_reason=_operator_failure_reason(
                reason_code=reason,
                failed_step=exc.failed_step,
                diagnostics=diagnostics,
                pre_dispatch_reason_code=exc.pre_dispatch_reason_code,
                exc=exc,
            ),
        )
        await finalize_run_failed(
            engine,
            run_id,
            task=task,
            error=error,
            created_meta_ids=exc.created_ids,
            task_result=_campaign_unknown_result(
                task,
                run_id=run_id,
                reason=reason,
                created_ids=exc.created_ids,
                failed_step=exc.failed_step,
                pre_dispatch=exc.pre_dispatch,
                pre_dispatch_reason_code=exc.pre_dispatch_reason_code,
                diagnostics=diagnostics,
                exc=exc,
            ),
            progress={
                "stage": "failed",
                "outcome": "UNKNOWN",
                "reason": reason,
                "failed_step": exc.failed_step,
            },
            login_required_ad_account_id=login_required_account_id,
        )
        # Концепты НЕ чистим при ошибке — нужны для ретрая (повтор залива тем же config).
        # Заброшенные/просроченные upload-папки подметает upload_storage_loop.
        return
    except Exception as exc:  # noqa: BLE001 — единая маршрутизация по classify
        readiness_rejection = _browser_readiness_rejection(exc)
        if readiness_rejection is not None:
            login_required = _requires_facebook_login(readiness_rejection)

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
                if login_required:
                    # Money-путь наравне с meta_api_mutation (#197): доказанный
                    # login_required проецирует канонический per-cabinet
                    # инцидент в ТОЙ ЖЕ транзакции, что закрывает эту попытку —
                    # оператор узнаёт причину из самого провала, не дожидаясь
                    # отдельного пятиминутного цикла пробы observer'а.
                    await notify_login_required_incident_in_transaction(
                        conn,
                        ad_account_id=cfg.account.act_id,
                    )

            released = await release_after_browser_readiness_rejection(
                engine,
                task=task,
                error=repr(readiness_rejection),
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
        # #200: пересечённая ранее внешняя граница сама по себе НЕ делает исход
        # неоднозначным. begin_external выставляется перед каждым внешним вызовом,
        # начиная с загрузки креативов, поэтому проверка external_started ловила и
        # те отказы, про которые доказано, что запрос не уходил: залив падал на
        # исчерпанном дедлайне до POST, создавал ноль объектов — и оператор всё
        # равно получал карточку ручной сверки. Доказанный отказ до отправки —
        # это REJECTED, сколько бы успешных вызовов ему ни предшествовало: те
        # завершились определённо, а этот не начинался.
        if control.external_started and not _is_proven_pre_dispatch(exc):
            # Раньше эта ветка тоже строила текст без причины (repr(exc) здесь —
            # ловушка: exc может оказаться MetaApiError, чей __repr__ несёт
            # fbtrace_id и полный Graph-текст). _failure_diagnostics — тот же
            # закрытый набор безопасных полей, что и в ветке ack_lost.
            diagnostics = _failure_diagnostics(exc, stage=last_stage)
            logger.error(
                "campaign_create: task id=%s ambiguous failure after external "
                "boundary diagnostics=%s",
                task.id,
                diagnostics,
            )
            await finalize_run_failed(
                engine,
                run_id,
                task=task,
                error=f"ambiguous failure after external boundary: cause={diagnostics}",
                task_result=_campaign_unknown_result(
                    task,
                    run_id=run_id,
                    reason="external_result_ambiguous",
                    diagnostics=diagnostics,
                    exc=exc,
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
                    error=f"transient exhausted before external call: {exc!r}",
                    task_result=_campaign_rejected_result(
                        run_id=run_id,
                        reason="pre_external_attempts_exhausted",
                        failed_step=last_stage,
                        exc=exc,
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
                error=repr(exc),
                attempt_count=task.attempt_count,
                max_attempts=task.max_attempts,
                lease_owner=task.lease_owner,
                lease_token=task.lease_token,
                lane=task.lane,
            )
            if retried:
                logger.warning(
                    "campaign_create: task id=%s → retrying (transient): %r", task.id, exc
                )
            else:
                logger.error(
                    "campaign_create: task id=%s → исчерпаны попытки (transient): %r",
                    task.id,
                    exc,
                )
            return
        # permanent: валидация/Meta permission/policy → run=failed, без retry.
        # exc_info: наружу едет только класс причины (fbtrace_id и текст Graph-ответа
        # утекают через repr), поэтому единственное место, где причина остаётся
        # диагностируемой, — лог воркера.
        logger.error(
            "campaign_create: task id=%s → permanent fail: %r meta_reason=%r",
            task.id,
            exc,
            _meta_reason(exc),
            exc_info=True,
        )
        await finalize_run_failed(
            engine,
            run_id,
            task=task,
            error=f"permanent before external call: {exc!r}",
            task_result=_campaign_rejected_result(
                run_id=run_id,
                reason="permanent_pre_external_failure",
                failed_step=last_stage,
                exc=exc,
            ),
            login_required_ad_account_id=(
                cfg.account.act_id if _requires_facebook_login(exc) else None
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
    logger.info("campaign_create: task id=%s succeeded (run %s)", task.id, run_id)
    await _cleanup_upload_dir(engine, cfg.creo_root, run_id=run_id)


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


async def _cleanup_upload_dir(engine: AsyncEngine, creo_root: str | None, *, run_id: str) -> None:
    """Best-effort удаление папки загруженных концептов на УСПЕХЕ/ОТМЕНЕ прогона.

    Оригиналы фото/видео нужны до успешного залива (уникализированные байты уже ушли в
    Meta) ИЛИ для ретрая после ошибки. Поэтому при ошибке (partial/permanent/exhausted)
    папку НЕ чистим — пользователь может «Повторить залив» тем же config.

    Money-safety (issue #192): набор адресуется общим ``creo_root`` — несколько
    прогонов (multi-cabinet launch) могут смотреть на одну и ту же папку. Прежде
    чем удалять, проверяем, не держит ли её ЕЩЁ ЖИВОЙ или ЕЩЁ ПОВТОРЯЕМЫЙ прогон
    (``referenced_creo_roots``): успех одного прогона не имеет права унести
    исходники, которыми ждёт «Повторить залив» другой. Защита: upload ID
    резолвится только как подпапка внутри корня загрузок. Сбой не роняет задачу.
    """
    if not creo_root:
        return
    try:
        target = _resolve_creo_dir(creo_root).resolve()
        root = _campaign_upload_root().resolve()
        if root not in target.parents:
            return  # путь вне корня загрузок — не наш, не трогаем
        held = await referenced_creo_roots(engine, only_creo_root=creo_root, exclude_run_id=run_id)
        if creo_root in held:
            logger.info(
                "campaign_create: upload-папка %s оставлена — набор нужен другому прогону",
                creo_root,
            )
            return
        shutil.rmtree(target, ignore_errors=True)
        logger.info("campaign_create: upload-папка прогона очищена: %s", target)
    except Exception:  # noqa: BLE001 — best-effort, не роняет обработку задачи
        logger.warning(
            "campaign_create: не удалось очистить upload-папку %r", creo_root, exc_info=True
        )


async def _sweep_stale_upload_dirs(
    engine: AsyncEngine, *, max_age_days: float = _DEFAULT_UPLOAD_MAX_AGE_DAYS
) -> int:
    """Retention по сроку: подметает upload-папки старше max_age_days.

    При ошибке залива папку концептов оставляем для «Повторить залив», поэтому без
    подметания неуспешные/заброшенные папки копились бы. Вызывается периодически
    из ``upload_storage_loop`` (issue #190 п.1) — не только на старте воркера,
    иначе хранилище не убирается до перезапуска. Набор, на который ссылается
    активный или ожидающий повтора прогон, не трогаем независимо от возраста
    (issue #190 п.4) — источник истины тот же ``referenced_creo_roots``, что и у
    точечного удаления на успехе. Best-effort, не роняет цикл.
    """
    root = _campaign_upload_root()
    if not root.exists():
        return 0
    try:
        held = await referenced_creo_roots(engine)
    except Exception:  # noqa: BLE001 — сбой чтения БД не должен ронять подметание
        logger.warning(
            "campaign_create: не удалось прочитать занятые creo_root — подметание по сроку пропущено",
            exc_info=True,
        )
        return 0
    cutoff = time.time() - max_age_days * 86400.0
    removed = 0
    try:
        children = list(root.iterdir())
    except OSError:
        return 0
    for child in children:
        if not child.is_dir() or child.name in held:
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info(
            "campaign_create: retention — удалено %d upload-папок старше %.0fд",
            removed,
            max_age_days,
        )
    return removed


def _upload_dir_size_bytes(path: Path) -> int:
    """Суммарный размер файлов внутри одного upload-набора."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except OSError:
                    continue
    except OSError:
        pass
    return total


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


async def _enforce_upload_volume_cap(
    engine: AsyncEngine, *, max_total_bytes: int = _DEFAULT_UPLOAD_MAX_TOTAL_BYTES
) -> tuple[int, int]:
    """Retention по объёму: держит CAMPAIGN_UPLOAD_ROOT в пределах max_total_bytes.

    При превышении удаляет самые старые НЕзанятые наборы (issue #190 п.2), пока
    объём не уложится в предел или свободных к удалению наборов не останется —
    занятые (``referenced_creo_roots``) не трогаем даже если предел не достигнут
    (issue #190 п.4). Возвращает (сколько наборов удалено, итоговый объём).

    Набор без строки ``campaign_run`` НЕ означает «ничей»: папка появляется на
    диске в ``POST /upload`` раньше, чем создаётся run, а частичная загрузка
    вообще лежит в служебном каталоге ``.{id}.{hex}.uploading`` в этом же корне.
    Поэтому кандидатами становятся только наборы старше
    ``_UPLOAD_CAP_MIN_AGE_SECONDS``, и служебные каталоги не рассматриваются
    вовсе — иначе предел снёс бы загрузку, которую оператор делает прямо сейчас.
    """
    root = _campaign_upload_root()
    if not root.exists():
        return 0, 0
    try:
        children = [
            child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")
        ]
    except OSError:
        return 0, 0
    sizes = {child: _upload_dir_size_bytes(child) for child in children}
    total = sum(sizes.values())
    if total <= max_total_bytes:
        return 0, total
    try:
        held = await referenced_creo_roots(engine)
    except Exception:  # noqa: BLE001 — сбой чтения БД не должен ронять подметание
        logger.warning(
            "campaign_create: не удалось прочитать занятые creo_root — подметание по объёму пропущено",
            exc_info=True,
        )
        return 0, total
    fresh_cutoff = time.time() - _UPLOAD_CAP_MIN_AGE_SECONDS
    candidates = sorted(
        (
            child
            for child in children
            if child.name not in held and _safe_mtime(child) < fresh_cutoff
        ),
        key=_safe_mtime,
    )
    removed = 0
    for child in candidates:
        if total <= max_total_bytes:
            break
        shutil.rmtree(child, ignore_errors=True)
        total -= sizes.get(child, 0)
        removed += 1
    if removed:
        logger.warning(
            "campaign_create: превышен предел хранилища загрузок (%s) — удалено %d "
            "старых наборов, осталось %s",
            human_bytes_ru(max_total_bytes),
            removed,
            human_bytes_ru(max(0, total)),
        )
    return removed, max(0, total)


async def _publish_upload_storage_health(
    engine: AsyncEngine, *, used_bytes: int, max_total_bytes: int
) -> None:
    """Метрика занятого объёма + инцидент при устойчивом превышении (issue #190 п.3).

    Тот же durable incident-plane, что уже держит место под PostgreSQL
    (``apps.cleanup_worker.storage.publish_disk_health``): владелец узнаёт про
    переполнение хранилища загрузок так же, как про нехватку места под БД.
    """
    record_campaign_upload_storage(used_bytes=used_bytes)
    if used_bytes <= max_total_bytes:
        await resolve_recurring_incident(
            engine,
            incident_key=CAMPAIGN_UPLOAD_STORAGE_INCIDENT_KEY,
            audience="owners",
            summary="Хранилище загруженных концептов снова в пределах нормы.",
        )
        return
    percent = (used_bytes / max_total_bytes * 100) if max_total_bytes else 0.0
    await notify_recurring_incident(
        engine,
        incident_key=CAMPAIGN_UPLOAD_STORAGE_INCIDENT_KEY,
        audience="owners",
        event_type="campaign_upload_storage_over_limit",
        severity="warning",
        title="Хранилище загруженных концептов переполнено",
        summary=(
            f"Занято {human_bytes_ru(used_bytes)} из предела "
            f"{human_bytes_ru(max_total_bytes)} ({percent:.0f} %)."
        ),
        lines=(
            "Что делать: проверить, нет ли зависших прогонов с «Повторить залив», "
            "и при необходимости поднять предел CAMPAIGN_UPLOAD_MAX_TOTAL_BYTES.",
        ),
        risk="Диск воркера залива может закончиться — новые загрузки начнут падать.",
        resource_type="storage",
        resource_id="campaign-upload",
    )


def _parsed_env_threshold(name: str, cast, default):
    """Порог из окружения; нераспознанное значение НЕ проглатывается молча.

    Оператор, написавший «5GiB» вместо числа, иначе был бы уверен, что предел
    поднят, а работал бы дефолт — и узнал бы об этом по удалённым наборам.
    Предупреждение называет переменную, что было написано и что применено.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return cast(raw)
    except ValueError:
        logger.warning(
            "campaign_create: %s не распознан (%r) — применён предел по умолчанию %r",
            name,
            raw,
            default,
        )
        return default


def _upload_max_age_days() -> float:
    return _parsed_env_threshold(_UPLOAD_MAX_AGE_DAYS_ENV, float, _DEFAULT_UPLOAD_MAX_AGE_DAYS)


def _upload_max_total_bytes() -> int:
    return _parsed_env_threshold(_UPLOAD_MAX_TOTAL_BYTES_ENV, int, _DEFAULT_UPLOAD_MAX_TOTAL_BYTES)


async def _sweep_upload_storage_once(engine: AsyncEngine) -> None:
    """Один цикл ретеншна upload-хранилища: срок → объём → метрика/инцидент.

    Best-effort: сбой любого шага логируется и не роняет воркер (issue #190).
    """
    await _sweep_stale_upload_dirs(engine, max_age_days=_upload_max_age_days())
    max_total_bytes = _upload_max_total_bytes()
    try:
        _removed, total_bytes = await _enforce_upload_volume_cap(
            engine, max_total_bytes=max_total_bytes
        )
    except Exception:  # noqa: BLE001 — best-effort
        logger.warning("campaign_create: подметание по объёму упало", exc_info=True)
        return
    try:
        await _publish_upload_storage_health(
            engine, used_bytes=total_bytes, max_total_bytes=max_total_bytes
        )
    except Exception:  # noqa: BLE001 — best-effort, метрика/инцидент не роняют воркер
        logger.warning("campaign_create: публикация health хранилища загрузок упала", exc_info=True)


async def upload_storage_loop(engine: AsyncEngine, stop: asyncio.Event) -> None:
    """Периодическая уборка + наблюдаемость CAMPAIGN_UPLOAD_ROOT (issue #190).

    Первый проход — сразу при старте (сохраняет прежнее поведение), дальше —
    каждые ``_UPLOAD_STORAGE_CHECK_INTERVAL_SECONDS`` без перезапуска воркера.
    """
    while not stop.is_set():
        try:
            await _sweep_upload_storage_once(engine)
        except Exception:  # noqa: BLE001 — цикл ретеншна не должен убивать воркер
            logger.exception("campaign_create: цикл ретеншна upload-хранилища упал")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_UPLOAD_STORAGE_CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


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


async def metrics_loop(stop: asyncio.Event, engine: AsyncEngine) -> None:
    """Refresh the process-local Prometheus liveness gauge and its durable twin.

    This coroutine is independent of ``task_loop``: a hang inside the claim
    call below would not stop this tick from firing. That decoupling is
    exactly what hid the 18.08.2026 incident, so the durable heartbeat here
    marks process-alive only — the queue-claiming proof of life is recorded
    from ``task_loop`` itself, not from here (see ``poll_success=True`` call
    below).
    """
    while not stop.is_set():
        mark_worker_heartbeat(WORKER_NAME)
        await record_worker_heartbeat(engine, WORKER_NAME)
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
    last_waiting_reason = ""
    while not stop.is_set():
        try:
            claim = await _claim(engine)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка claim_campaign_task")
            await _sleep_or_stop(stop)
            continue

        # Единственное доказательство, что рабочий цикл реально трогает
        # очередь, а не просто числится живым по отдельной корутине heartbeat
        # (issue #176). Отмечается независимо от того, нашлась ли задача.
        # record_worker_heartbeat уже не пробрасывает ничего, кроме
        # CancelledError, но этот вызов — единственный неограниченный await
        # между claim и началом залива на дедлайн-полосе: локальный guard —
        # defense in depth, а не замена внутреннего (review issue #176 Б1).
        try:
            await record_worker_heartbeat(engine, WORKER_NAME, poll_success=True)
        except Exception:  # noqa: BLE001
            logger.warning("worker heartbeat write failed in task_loop", exc_info=True)

        if claim.queue_empty or claim.task is None:
            # Пустой claim при непустой очереди означает закрытый гейт готовности.
            # Молчать нельзя: задача пролежит до дедлайна, а оператор всё это
            # время видит «в очереди» без причины.
            try:
                block = await explain_browser_claim_block(
                    engine, task_type=TASK_TYPE, lanes=("bulk",)
                )
            except Exception:  # noqa: BLE001
                block = None
                logger.exception("не удалось объяснить ожидание готовности браузера")
            if block is None:
                last_waiting_reason = ""
            else:
                if block.reason_code != last_waiting_reason:
                    logger.warning(
                        "campaign_create: %d задач ждут готовности браузера (%s)",
                        block.waiting,
                        block.reason_code,
                    )
                    last_waiting_reason = block.reason_code
                await _note_waiting_reason(engine, run_ids=block.run_ids, reason=block.human)
            await _sleep_or_stop(stop)
            continue
        last_waiting_reason = ""

        try:
            vision_profile_id = str(claim.browser_profile_id or "").strip()
            if not vision_profile_id:
                raise RuntimeError(
                    "browser-ready campaign claim returned no canonical Vision profile"
                )
            # Залив пинится к той сессии браузера, на которой подтверждена
            # готовность. Пустой session_id означал «выбери сам самую свежую»:
            # между вызовами одного залива предпочитаемая сессия менялась
            # (перезапуск observer'а, восстановление), и залив терял привязку к
            # своей странице. Смена сессии посреди задачи теперь отказ —
            # проверку делает сам клиент перед каждой операцией.
            claimed_session_id = str(claim.browser_session_id or "").strip()
            if not claimed_session_id:
                raise RuntimeError("browser-ready campaign claim returned no browser session")
            previous_session_id = client.session_id
            client.session_id = claimed_session_id
            try:
                with client.operation_authority(
                    caller="campaign_creator",
                    task_id=claim.task.id,
                    lease_owner=claim.task.lease_owner,
                    lease_token=claim.task.lease_token,
                    vision_profile_id=vision_profile_id,
                    browser_readiness_generation=claim.browser_readiness_generation,
                ):
                    # Залив может исполняться минуты (загрузка видео,
                    # медленная обработка Meta) — дольше poll_stale_after_seconds.
                    # Периодический тик доказывает «воркер занят настоящей
                    # работой», а не тем же самым claim'ом минуту назад
                    # (review issue #176 Б2).
                    async with poll_heartbeat_while_running(engine, WORKER_NAME):
                        await process_one_task(engine, claim.task, client=client, uploader=uploader)
            finally:
                client.session_id = previous_session_id
        except Exception:  # noqa: BLE001 — неожиданная ошибка (напр. БД в фазе pre-execute гардов)
            logger.exception(
                "campaign_create: unexpected crash task id=%s — terminal UNKNOWN",
                claim.task.id,
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
            except Exception:  # noqa: BLE001
                logger.exception(
                    "campaign_create: UNKNOWN finalize also failed task=%s", claim.task.id
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
    try:
        await asyncio.gather(
            task_loop(engine, stop, client=meta_client, uploader=uploader),
            metrics_loop(stop, engine),
            upload_storage_loop(engine, stop),
        )
    finally:
        try:
            await meta_client.close()
        except Exception:  # noqa: BLE001
            logger.exception("meta_client.close() упал")
        await engine.dispose()
        logger.info("campaign_creator_worker остановлен")
