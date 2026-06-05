# -*- coding: utf-8 -*-
"""meta_api_worker main loop.

Этап 5: реальная диспетчеризация mutations через AuditedMetaApiClient +
dispatch_mutation. До Этапа 5 здесь была заглушка с NotImplementedError.

Состояние процесса:
- heartbeat: Redis worker:heartbeat:meta_api TTL 60s
- reconcile: stuck running + stale drafts (своих task_type)
- idle: spinning poll с asyncio.sleep
- graceful: SIGTERM/SIGINT → завершить текущий цикл и закрыть ресурсы

Маршрутизация ошибок:
- PermanentError / TokenInvalidError / NotFoundError / PermissionError → mark_failed (retry бесполезен)
- RateLimitedError / TemporaryError / SessionUnavailableError → requeue_for_retry
- NotImplementedError (новый mutation_kind без handler) → mark_failed
- MutationValidationError (осознанная валидационная ошибка в handler'е) → mark_failed
- CreateCampaignPartialError → mark_failed + лог осиротевших id (нужна ручная чистка)
- голый ValueError (неожиданный, баг в коде) → requeue (защитный retry, логируется как аномалия)
- любое другое Exception → requeue (защитный retry на transient)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.meta_api.audit import AuditedMetaApiClient
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    MutationValidationError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
    TokenInvalidError,
)
from core.meta_api.errors import (
    PermissionError as MetaPermissionError,
)
from core.meta_api.fsm_sync import sync_fsm_after_mutation
from core.meta_api.mutations import dispatch_mutation
from core.meta_api.mutations.create_campaign import CreateCampaignPartialError
from core.meta_api.queue import (
    claim_pending_task,
    mark_task_failed,
    mark_task_succeeded,
    requeue_task,
)
from core.meta_api.reconciler import reconcile_all
from core.meta_api.schemas import MetaMutationPayload
from core.observer.queries import load_scanning_enabled
from core.pubsub import CHANNEL_TASK_CHANGED
from core.tasks.queue import Task

logger = logging.getLogger("meta_api_worker")

WORKER_NAME = "meta_api"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60
IDLE_SLEEP_SECONDS = 5
RECONCILE_INTERVAL_SECONDS = 60


async def _publish_task_changed(
    redis_client: redis_asyncio.Redis | None,
    *,
    task_id: int,
    task_type: str,
    status: str,
) -> None:
    """Best-effort publish изменения статуса задачи в fb_agent:task:changed."""
    if redis_client is None:
        return
    try:
        payload = json.dumps(
            {
                "task_id": task_id,
                "task_type": task_type,
                "status": status,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_TASK_CHANGED, payload)
    except Exception:
        logger.warning("meta_api_worker: не удалось publish task:changed task_id=%s", task_id)


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


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
) -> dict[str, Any]:
    """Исполнить mutation через dispatch_mutation.

    Доменные ошибки Meta пробрасываются как есть — process_one_task маршрутизирует.
    """
    return await dispatch_mutation(client, payload)


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

# ====================== асимметричный стоп ======================

# mutation_kind, которые ВЫКЛЮЧАЮТ открут (снижают трату) — разрешены даже на паузе.
_DEACTIVATING_KINDS = frozenset({"pause_ad", "pause_campaign"})
# action для bulk_status_change, которые считаются выключающими.
_BULK_PAUSE_ACTIONS = frozenset({"pause", "paused", "disable", "disabled"})


def _is_activating_mutation(payload: MetaMutationPayload) -> bool:
    """True если mutation ВКЛЮЧАЕТ/тратит (на паузе сканирования откладывается).

    Асимметричный стоп пропускает только ВЫКЛЮЧАЮЩИЕ действия (они снижают риск
    открута), всё остальное на паузе блокирует. Выключающие: pause_ad/pause_campaign
    и bulk_status_change с action pause/paused. Всё прочее (activate_*, bulk activate,
    create_campaign, duplicate_campaign, set_adset_budget, set_ad_creative,
    custom_audience) — «не выключающее» → True → откладываем (money-safe: на стопе
    кабинет не трогаем сверх выключения).
    """
    kind = payload.mutation_kind
    if kind in _DEACTIVATING_KINDS:
        return False
    if kind == "bulk_status_change":
        action = str((getattr(payload, "params", None) or {}).get("action", "")).strip().lower()
        return action not in _BULK_PAUSE_ACTIONS
    return True


async def process_one_task(
    engine: AsyncEngine,
    task: Task,
    *,
    client: MetaApiClient | None = None,
    redis_client: redis_asyncio.Redis | None = None,
) -> None:
    """Полный жизненный цикл одной задачи.

    client опционален — если None, mutation не исполнится (для тестов с моками).
    В production main_loop всегда передаёт реальный AuditedMetaApiClient.
    """
    try:
        payload = MetaMutationPayload.from_dict(task.payload)
    except (KeyError, ValueError) as exc:
        logger.error("Невалидный payload в task id=%s: %s", task.id, exc)
        applied = await mark_task_failed(engine, task_id=task.id, error=f"invalid payload: {exc}")
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (invalid payload) не применился "
                "— гонка с другим воркером",
                task.id,
            )
        return

    if client is None:
        # Защитная ветка для случая когда клиент не подан (старые тесты).
        # В production main_loop всегда передаёт клиент.
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error="MetaApiClient не доступен в worker (Vision-сессия?)",
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (no client) не применился "
                "— гонка с другим воркером",
                task.id,
            )
        return

    # Асимметричный стоп: на паузе сканирования откладываем АКТИВИРУЮЩИЕ mutations
    # (activate/bulk activate/create/duplicate/budget/...), пропуская только
    # ВЫКЛЮЧАЮЩИЕ (pause_*, bulk pause) — они снижают риск открута. Отложенная
    # задача уходит в retry и исполнится после снятия паузы; если пауза длится
    # дольше лимита попыток — зафейлится (autostart, инициированный до паузы,
    # осознанно отменяется пользовательским стопом).
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

    logger.info(
        "meta_api: исполняю task id=%s kind=%s target=%s",
        task.id,
        payload.mutation_kind,
        payload.target_id,
    )

    try:
        result = await execute_mutation(payload, client=client)
        applied = await mark_task_succeeded(engine, task_id=task.id, result=result)
        if not applied:
            # Race: другой воркер уже закрыл задачу после reconciler-таймаута.
            # Mutation в Meta мы уже исполнили — повторно не делаем (return).
            logger.warning(
                "meta_api: task id=%s mark_succeeded не применился "
                "(status != running) — гонка с другим воркером, пропускаю",
                task.id,
            )
            return
        logger.info("meta_api: task id=%s succeeded", task.id)
        # Publish изменения статуса в Redis-канал (best-effort)
        await _publish_task_changed(
            redis_client,
            task_id=task.id,
            task_type=task.task_type,
            status="succeeded",
        )
        # FSM-sync: привести ad_alert_state к результату mutation. Идемпотентно и
        # best-effort (не роняет succeeded-контракт). Закрывает money-пробел —
        # без этого FSM застревал в stop_sent при auto-stop через API.
        await sync_fsm_after_mutation(engine, payload)
        return
    except CreateCampaignPartialError as exc:
        # Batch API не атомарен: часть объектов уже создана в Meta.
        # Логируем осиротевшие id — оператор должен удалить их вручную.
        logger.error(
            "meta_api: task id=%s create_campaign partial fail — "
            "осиротевшие объекты в Meta, нужна ручная чистка! "
            "created_ids=%s failed_steps=%s",
            task.id,
            exc.created_ids,
            exc.failed_steps,
        )
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"partial_fail: created_ids={exc.created_ids!r} failed={exc.failed_steps!r}",
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (partial) не применился "
                "— гонка с другим воркером",
                task.id,
            )
        return
    except _PERMANENT_EXCEPTIONS as exc:
        applied = await mark_task_failed(engine, task_id=task.id, error=repr(exc))
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
        retried = await requeue_task(engine, task=task, error=repr(exc))
        if retried:
            logger.warning("meta_api: task id=%s → retrying (temporary): %s", task.id, exc)
        else:
            logger.error("meta_api: task id=%s → exhausted retries (temporary): %s", task.id, exc)
        return
    except ValueError as exc:
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
        retried = await requeue_task(engine, task=task, error=repr(exc))
        if retried:
            logger.warning("meta_api: task id=%s → retrying (unknown): %s", task.id, exc)
        else:
            logger.error("meta_api: task id=%s → final fail (unknown): %s", task.id, exc)


# ====================== sub-loops ======================


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Периодически обновляет worker:heartbeat:meta_api с TTL 60s."""
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def reconcile_loop(engine: AsyncEngine, stop: asyncio.Event) -> None:
    """Reconcile stuck/stale tasks раз в RECONCILE_INTERVAL_SECONDS."""
    while not stop.is_set():
        try:
            stats = await reconcile_all(engine)
            if stats.get("stuck_running") or stats.get("stale_drafts"):
                logger.info("reconcile: %s", stats)
        except Exception:  # noqa: BLE001
            logger.exception("reconcile loop: error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=RECONCILE_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass


async def task_loop(
    engine: AsyncEngine,
    stop: asyncio.Event,
    *,
    client: MetaApiClient,
    redis_client: redis_asyncio.Redis | None = None,
) -> None:
    """Главный цикл claim → execute → mark."""
    while not stop.is_set():
        try:
            claim = await claim_pending_task(engine)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка claim_pending_task")
            try:
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        if claim.queue_empty or claim.task is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        await process_one_task(engine, claim.task, client=client, redis_client=redis_client)


# ====================== entrypoint ======================


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, echo=False)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    # MetaApiClient — eager-init: gRPC channel создаётся без блокировки;
    # реальный fail только при первом ExecuteGraphCall, маршрутизируется в requeue.
    meta_client = _build_meta_client(engine)
    await meta_client.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("meta_api_worker запущен (MetaApiClient ready)")
    try:
        await asyncio.gather(
            task_loop(engine, stop, client=meta_client, redis_client=redis_client),
            heartbeat_loop(redis_client, stop),
            reconcile_loop(engine, stop),
        )
    finally:
        try:
            await meta_client.close()
        except Exception:  # noqa: BLE001
            logger.exception("meta_client.close() упал")
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        await engine.dispose()
        logger.info("meta_api_worker остановлен")
