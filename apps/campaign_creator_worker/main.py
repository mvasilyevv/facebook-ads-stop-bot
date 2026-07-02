# -*- coding: utf-8 -*-
"""campaign_creator_worker main loop — исполнение залива FB-кампаний (Волна 2, Подход A).

Состояние процесса (зеркало meta_api_worker):
- heartbeat: Redis worker:heartbeat:campaign_creator TTL 60s (фоновый таск)
- idle: spinning poll с asyncio.sleep
- graceful: SIGTERM/SIGINT → завершить текущий цикл и закрыть ресурсы

Жизненный цикл одной задачи (task_type='campaign_create'):
    claim (FOR UPDATE SKIP LOCKED) → load CampaignRun по payload.run_id →
    queued → uniquifying → uploading → creating → execute_campaign_spec →
    succeeded (created_meta_ids) | failed.

Money-критичная классификация ошибок (через execute.classify_execution_error):
- permanent (валидация конфига, Meta permission/policy-reject) → run=failed + task mark_failed.
- transient (сеть, rate-limit, Vision unavailable) → run остаётся в работе, task requeue + backoff.
- partial-create (часть объектов уже в Meta) → run=failed + created_meta_ids (осиротевшие),
  task mark_failed БЕЗ retry (повтор = дубль кампании + двойной открут бюджета).

Money-инварианты: кампания всегда PAUSED (кривой запуск не тратит); idempotency_key
задачи (offer+date+хеш структуры) против двойного залива; budget hard-cap в CampaignConfig.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import time
from typing import Any

import redis.asyncio as redis_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.campaign_creator_worker import (
    _campaign_upload_root,
    _resolve_creo_dir,
    finalize_run_failed,
    finalize_run_succeeded,
    load_run,
    parse_run_config,
    resolve_concepts_from_config,
    set_run_status,
)
from apps.campaign_creator_worker import claim_campaign_task as _claim
from core.campaign_builder.builder import build_campaign_spec
from core.campaign_builder.creative_ledger import record_creative
from core.campaign_builder.execute import (
    PartialCreateError,
    classify_execution_error,
    execute_campaign_spec,
)
from core.db import WORKER_ENGINE_KWARGS
from core.meta_api.client import MetaApiClient
from core.meta_api.upload import MediaUploader
from core.tasks.queue import Task, mark_failed, mark_succeeded, requeue_for_retry

logger = logging.getLogger("campaign_creator_worker")

WORKER_NAME = "campaign_creator"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60
IDLE_SLEEP_SECONDS = 5


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


def _build_meta_client() -> MetaApiClient:
    """Сконструировать клиент Marketing API (gRPC к browser-agent)."""
    return MetaApiClient(
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
    )


async def _persist_partial_created_ids(
    engine: AsyncEngine,
    *,
    task_id: int,
    created_ids: dict[str, Any],
    failed_step: str,
) -> None:
    """created_ids partial-провала — в task_queue.result, не только в логи/campaign_run.

    Урок MID-24: у 8 старых failed-задач result был NULL, а id осиротевших объектов
    Meta жили только в ротируемых логах. campaign_run.created_meta_ids уже пишется
    (finalize_run_failed), но разбор очереди смотрит в task_queue — дублируем сюда.
    Пишем ДО mark_failed (guard status='running' тот же); best-effort — сбой записи
    не должен помешать mark_failed.
    """
    payload = {"partial_fail": True, "failed_step": failed_step, "created_ids": created_ids}
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE task_queue SET result = CAST(:r AS JSONB), updated_at = NOW() "
                    "WHERE id = :id AND status = 'running'"
                ),
                {"id": task_id, "r": json.dumps(payload)},
            )
    except Exception:  # noqa: BLE001 — best-effort, mark_failed важнее
        logger.warning(
            "campaign_create: не удалось записать created_ids в task_queue.result (task=%s)",
            task_id,
            exc_info=True,
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
    run_id = (task.payload or {}).get("run_id")
    if not run_id:
        logger.error("campaign_create: task id=%s без run_id в payload", task.id)
        await _safe_mark_failed(engine, task, "invalid payload: нет run_id")
        return

    run = await load_run(engine, str(run_id))
    if run is None:
        logger.error("campaign_create: task id=%s run_id=%s не найден", task.id, run_id)
        await _safe_mark_failed(engine, task, f"campaign_run {run_id} не найден")
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
            await mark_succeeded(engine, task_id=task.id, result={"run_id": str(run_id)})
        else:
            await mark_failed(engine, task_id=task.id, error=f"run уже {run.status}")
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
        await finalize_run_failed(
            engine,
            str(run_id),
            error=(
                f"run уже в работе/с созданными объектами (status={run.status}) — "
                "повторное исполнение запрещено (риск дубля кампании), проверь Meta вручную"
            ),
        )
        await _safe_mark_failed(
            engine, task, f"run уже в работе (status={run.status}) — re-execute запрещён"
        )
        return

    if client is None or uploader is None:
        logger.error("campaign_create: task id=%s — Meta-клиент/uploader не доступен", task.id)
        await set_run_status(engine, str(run_id), "failed")
        await finalize_run_failed(
            engine, str(run_id), error="MetaApiClient/MediaUploader не доступен (Vision?)"
        )
        await _safe_mark_failed(engine, task, "MetaApiClient/MediaUploader не доступен")
        return

    await _execute_run(
        engine, task, run_id=str(run_id), config=run.config, client=client, uploader=uploader
    )


async def _execute_run(
    engine: AsyncEngine,
    task: Task,
    *,
    run_id: str,
    config: dict[str, Any],
    client: MetaApiClient,
    uploader: MediaUploader,
) -> None:
    """Гоняет execute с записью прогресса/статуса и маршрутизацией ошибок."""
    # 1) Валидация конфига (pydantic) — permanent при ошибке.
    try:
        cfg = parse_run_config(config)
        concepts_by_campaign = resolve_concepts_from_config(cfg)
        spec = build_campaign_spec(cfg)
    except Exception as exc:  # noqa: BLE001 — валидация конфига/концептов = permanent
        logger.error("campaign_create: task id=%s конфиг невалиден: %r", task.id, exc)
        await finalize_run_failed(engine, run_id, error=f"invalid config: {exc!r}")
        await _safe_mark_failed(engine, task, f"invalid config: {exc!r}")
        return

    # Атомарный queued→uniquifying (cancel-гонка): если конкурентный cancel успел перевести
    # run в cancelled, переход НЕ пройдёт (expect='queued') → прерываемся ДО любого создания
    # объектов в Meta. Задачу терминируем как succeeded (обработана: run отменён, создавать нечего).
    if not await set_run_status(
        engine, run_id, "uniquifying", progress={"stage": "uniquifying"}, expect="queued"
    ):
        logger.info(
            "campaign_create: task id=%s — run %s отменён до старта (cancel-гонка), пропуск без создания",
            task.id,
            run_id,
        )
        await _safe_mark_failed(
            engine, task, "run отменён до старта (cancel-гонка) — пропуск без создания"
        )
        _cleanup_upload_dir(cfg.creo_root)
        return

    async def on_progress(snapshot: dict[str, Any]) -> None:
        # Прогресс execute → status + progress run. Стадии execute
        # (uniquifying/uploading/creating) маппятся 1:1 в статус run. Best-effort,
        # не роняет залив (execute ловит).
        stage = snapshot.get("stage", "creating")
        run_status = stage if stage in ("uniquifying", "uploading", "creating") else "creating"
        await set_run_status(engine, run_id, run_status, progress=snapshot)

    async def _record(code: str, kind: str, creative_id: str) -> None:
        # Реестр — best-effort аудит: его сбой не должен ронять успешный залив.
        try:
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

    try:
        result = await execute_campaign_spec(
            cfg,
            spec,
            concepts_by_campaign=concepts_by_campaign,
            client=client,
            uploader=uploader,
            on_progress=on_progress,
            on_creative_created=_record,
        )
    except PartialCreateError as exc:
        # Часть объектов уже в Meta — НЕ ретраим (дубли). run=failed + осиротевшие id.
        logger.error(
            "campaign_create: task id=%s PARTIAL FAIL — осиротевшие объекты в Meta, "
            "нужна ручная чистка! created_ids=%s step=%s",
            task.id,
            exc.created_ids,
            exc.failed_step,
        )
        await finalize_run_failed(
            engine,
            run_id,
            error=f"partial_fail (step={exc.failed_step}): проверь Meta вручную: {exc!r}",
            created_meta_ids=exc.created_ids,
        )
        await _persist_partial_created_ids(
            engine, task_id=task.id, created_ids=exc.created_ids, failed_step=exc.failed_step
        )
        await _safe_mark_failed(engine, task, f"partial_fail: {exc!r}")
        # Концепты НЕ чистим при ошибке — нужны для ретрая (повтор залива тем же config).
        # Старые upload-папки подметает retention в cleanup_worker.
        return
    except Exception as exc:  # noqa: BLE001 — единая маршрутизация по classify
        kind = classify_execution_error(exc)
        if kind == "transient":
            # Money-safety: transient по classify_execution_error возможен ТОЛЬКО до
            # инициации POST campaign (irreversible_attempted=False) — объект гарантированно
            # не создан. Сбрасываем run обратно в 'queued' ПЕРЕД requeue: иначе он застрял
            # в 'uniquifying' (set_run_status выше), и re-claim guard в process_one_task
            # («run уже в работе» → failed) зарубил бы легитимный transient-retry.
            await set_run_status(engine, run_id, "queued")
            # Сеть/rate-limit/Vision — задача в requeue с backoff, run снова queued.
            retried = await requeue_for_retry(
                engine,
                task_id=task.id,
                error=repr(exc),
                attempt_count=task.attempt_count,
                max_attempts=task.max_attempts,
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
                await finalize_run_failed(engine, run_id, error=f"transient exhausted: {exc!r}")
                # Концепты НЕ чистим — оставляем для ретрая (retention подметёт старое).
            return
        # permanent: валидация/Meta permission/policy → run=failed, без retry.
        logger.error("campaign_create: task id=%s → permanent fail: %r", task.id, exc)
        await finalize_run_failed(engine, run_id, error=f"permanent: {exc!r}")
        await _safe_mark_failed(engine, task, f"permanent: {exc!r}")
        # Концепты НЕ чистим — оставляем для ретрая (retention подметёт старое).
        return

    # Успех: created_meta_ids в run + task succeeded.
    final_progress = {"stage": "succeeded", **result.created_meta_ids}
    await finalize_run_succeeded(
        engine,
        run_id,
        created_meta_ids=result.created_meta_ids,
        progress=final_progress,
    )
    applied = await mark_succeeded(
        engine, task_id=task.id, result={"run_id": run_id, **result.created_meta_ids}
    )
    if not applied:
        logger.warning(
            "campaign_create: task id=%s mark_succeeded не применился (гонка) — run уже succeeded",
            task.id,
        )
    else:
        logger.info("campaign_create: task id=%s succeeded (run %s)", task.id, run_id)
    _cleanup_upload_dir(cfg.creo_root)


async def _safe_mark_failed(engine: AsyncEngine, task: Task, error: str) -> None:
    """mark_failed с логом гонки (status != running)."""
    applied = await mark_failed(engine, task_id=task.id, error=error)
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
    Защита: удаляем только подпапку внутри корня загрузок (не произвольный путь);
    абсолютные creo_root (legacy/тесты) вне корня — пропускаем. Сбой не роняет задачу.
    """
    if not creo_root:
        return
    try:
        target = _resolve_creo_dir(creo_root).resolve()
        root = _campaign_upload_root().resolve()
        if root not in target.parents:
            return  # путь вне корня загрузок — не наш, не трогаем
        shutil.rmtree(target, ignore_errors=True)
        logger.info("campaign_create: upload-папка прогона очищена: %s", target)
    except Exception:  # noqa: BLE001 — best-effort, не роняет обработку задачи
        logger.warning(
            "campaign_create: не удалось очистить upload-папку %r", creo_root, exc_info=True
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


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Периодически обновляет worker:heartbeat:campaign_creator с TTL 60s."""
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
        except Exception:  # noqa: BLE001
            logger.exception("ошибка claim_campaign_task")
            await _sleep_or_stop(stop)
            continue

        if claim.queue_empty or claim.task is None:
            await _sleep_or_stop(stop)
            continue

        try:
            await process_one_task(engine, claim.task, client=client, uploader=uploader)
        except Exception:  # noqa: BLE001 — неожиданная ошибка (напр. БД в фазе pre-execute гардов)
            # process_one_task сам маршрутизирует ошибки execute (requeue/mark_failed/
            # finalize), но pre-execute гарды (load_run / _run_has_created_meta_ids /
            # set_run_status) делают DB-I/O ВНЕ внутреннего try. Транзиентная ошибка БД
            # там не должна ронять воркер (иначе asyncio.gather падает, heartbeat встаёт,
            # подтверждённый залив теряется). Задача остаётся 'running' → reconciler через
            # 30 мин уведёт её в retrying. Логируем и продолжаем цикл.
            logger.exception(
                "campaign_create: непредвиденная ошибка обработки task id=%s — воркер продолжает",
                claim.task.id,
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
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    meta_client = _build_meta_client()
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
            heartbeat_loop(redis_client, stop),
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
        logger.info("campaign_creator_worker остановлен")
