# -*- coding: utf-8 -*-
"""Логика одного прогона reconciler-воркера.

Что делает:
1. Переводит task_queue.status='running' AND updated_at < now() - 30min → retrying
   (с инкрементом attempt_count: worker крашнулся ДО вызова requeue_for_retry,
   так что инкремент попыток делается внутри reconcile_stuck_running канона).

Реальная SQL-логика живёт в `core.tasks.queue` — здесь только параметры из env и
orchestration. Раньше эти функции дублировались тут, в результате attempt_count
бампался дважды (worker.py +1 и потом requeue_for_retry +1 после нового claim).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncEngine

from core.meta_api.schemas import IRREVERSIBLE_MUTATION_KINDS
from core.tasks.queue import expire_overdue_tasks as _canonical_expire_overdue_tasks
from core.tasks.queue import (
    fail_stuck_campaign_create as _canonical_fail_stuck_campaign_create,
)
from core.tasks.queue import (
    fail_stuck_duplicate_without_checkpoint as _canonical_fail_stuck_duplicate_without_checkpoint,
)
from core.tasks.queue import (
    prepare_stuck_duplicate_recovery as _canonical_prepare_stuck_duplicate_recovery,
)
from core.tasks.queue import (
    reconcile_stuck_running as _canonical_reconcile_stuck_running,
)

logger = logging.getLogger(__name__)

_STUCK_TIMEOUT_MIN = int(os.environ.get("RECONCILER_STUCK_TIMEOUT_MIN", "30"))


async def prepare_duplicate_recovery(engine: AsyncEngine) -> int:
    """Stale checkpointed duplicate → PAUSE-only recovery claim."""
    return await _canonical_prepare_stuck_duplicate_recovery(
        engine,
        stuck_after_seconds=_STUCK_TIMEOUT_MIN * 60,
    )


async def fail_duplicate_without_checkpoint(engine: AsyncEngine) -> int:
    """Stale duplicate with no created-ID checkpoint → failed, never replayed."""
    return await _canonical_fail_stuck_duplicate_without_checkpoint(
        engine,
        stuck_after_seconds=_STUCK_TIMEOUT_MIN * 60,
    )


async def fail_stuck_campaign_create(engine: AsyncEngine) -> int:
    """Зависшие задачи campaign_create (необратимый залив) → failed (НЕ retry).

    Money-safety: см. core.tasks.queue.fail_stuck_campaign_create. Вызывать ДО
    reconcile_stuck_running. Returns: число помеченных failed.
    """
    stuck_after_seconds = _STUCK_TIMEOUT_MIN * 60
    return await _canonical_fail_stuck_campaign_create(
        engine, stuck_after_seconds=stuck_after_seconds
    )


async def reconcile_stuck_running(engine: AsyncEngine) -> int:
    """Обёртка вокруг core.tasks.queue.reconcile_stuck_running с env-таймаутом.

    duplicate_adset_structure исключается из общего requeue: её stale-состояния
    обрабатывают checkpointed PAUSE-recovery и no-checkpoint UNKNOWN finalizer.

    Returns: количество переведённых задач.
    """
    stuck_after_seconds = _STUCK_TIMEOUT_MIN * 60
    return await _canonical_reconcile_stuck_running(
        engine,
        stuck_after_seconds=stuck_after_seconds,
        exclude_kinds=IRREVERSIBLE_MUTATION_KINDS,
    )


async def expire_overdue(engine: AsyncEngine) -> int:
    """Close queued work that outwaited its queue wait limit.

    Потребитель очереди закрывает свои просроченные задачи сам, на пустом
    claim. Этот проход остаётся общей подметалкой: он видит и те полосы, у
    которых прямо сейчас нет живого воркера.
    """
    return await _canonical_expire_overdue_tasks(engine)


async def run_once(engine: AsyncEngine) -> dict[str, int]:
    """Один прогон reconciler. Возвращает counters."""
    counts: dict[str, int] = {}

    try:
        counts["deadlines_expired"] = await expire_overdue(engine)
    except Exception as exc:
        logger.exception("expire_overdue failed: %s", exc)
        counts["deadlines_expired"] = -1

    # ВАЖЕН ПОРЯДОК: checkpointed duplicate_adset_structure сначала переводим в
    # PAUSE-only recovery. Затем отдельно fail'им duplicate без checkpoint.
    try:
        counts["duplicate_recovery_scheduled"] = await prepare_duplicate_recovery(engine)
    except Exception as exc:
        logger.exception("prepare_duplicate_recovery failed: %s", exc)
        counts["duplicate_recovery_scheduled"] = -1

    try:
        counts["duplicate_without_checkpoint_failed"] = await fail_duplicate_without_checkpoint(
            engine
        )
    except Exception as exc:
        logger.exception("fail_duplicate_without_checkpoint failed: %s", exc)
        counts["duplicate_without_checkpoint_failed"] = -1

    try:
        counts["campaign_create_failed"] = await fail_stuck_campaign_create(engine)
    except Exception as exc:
        logger.exception("fail_stuck_campaign_create failed: %s", exc)
        counts["campaign_create_failed"] = -1

    try:
        counts["stuck_to_retrying"] = await reconcile_stuck_running(engine)
    except Exception as exc:
        logger.exception("reconcile_stuck_running failed: %s", exc)
        counts["stuck_to_retrying"] = -1

    if any(v > 0 for v in counts.values()):
        logger.info("reconciler counts: %s", counts)
    return counts
