# -*- coding: utf-8 -*-
"""Логика одного прогона reconciler-воркера.

Что делает:
1. Переводит task_queue.status='running' AND updated_at < now() - 30min → retrying
   (с инкрементом attempt_count: worker крашнулся ДО вызова requeue_for_retry,
   так что инкремент попыток делается внутри reconcile_stuck_running канона).
2. Auto-cancel черновики старше 24h (страховка от cleanup_worker — он раз в сутки).

Реальная SQL-логика живёт в `core.tasks.queue` — здесь только параметры из env и
orchestration. Раньше эти функции дублировались тут, в результате attempt_count
бампался дважды (worker.py +1 и потом requeue_for_retry +1 после нового claim).
"""

from __future__ import annotations

import logging
import os

from sqlalchemy.ext.asyncio import AsyncEngine

from core.tasks.queue import (
    cancel_stale_drafts as _canonical_cancel_stale_drafts,
)
from core.tasks.queue import (
    reconcile_stuck_running as _canonical_reconcile_stuck_running,
)

logger = logging.getLogger(__name__)

_STUCK_TIMEOUT_MIN = int(os.environ.get("RECONCILER_STUCK_TIMEOUT_MIN", "30"))
_DRAFT_TIMEOUT_HOURS = int(os.environ.get("RECONCILER_DRAFT_TIMEOUT_HOURS", "24"))


async def reconcile_stuck_running(engine: AsyncEngine) -> int:
    """Обёртка вокруг core.tasks.queue.reconcile_stuck_running с env-таймаутом.

    Returns: количество переведённых задач.
    """
    stuck_after_seconds = _STUCK_TIMEOUT_MIN * 60
    return await _canonical_reconcile_stuck_running(engine, stuck_after_seconds=stuck_after_seconds)


async def cancel_old_drafts(engine: AsyncEngine) -> int:
    """Обёртка вокруг core.tasks.queue.cancel_stale_drafts с env-таймаутом.

    Returns: количество отменённых draft'ов.
    """
    older_than_seconds = _DRAFT_TIMEOUT_HOURS * 3600
    return await _canonical_cancel_stale_drafts(engine, older_than_seconds=older_than_seconds)


async def run_once(engine: AsyncEngine) -> dict[str, int]:
    """Один прогон reconciler. Возвращает counters."""
    counts: dict[str, int] = {}
    try:
        counts["stuck_to_retrying"] = await reconcile_stuck_running(engine)
    except Exception as exc:
        logger.exception("reconcile_stuck_running failed: %s", exc)
        counts["stuck_to_retrying"] = -1

    try:
        counts["drafts_cancelled"] = await cancel_old_drafts(engine)
    except Exception as exc:
        logger.exception("cancel_old_drafts failed: %s", exc)
        counts["drafts_cancelled"] = -1

    if any(v > 0 for v in counts.values()):
        logger.info("reconciler counts: %s", counts)
    return counts
