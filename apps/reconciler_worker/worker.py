# -*- coding: utf-8 -*-
"""Логика одного прогона reconciler-воркера.

Что делает:
1. Переводит task_queue.status='running' AND updated_at < now() - 30min → retrying.
2. Auto-cancel черновики старше 24h (это страховка от cleanup_worker — он работает раз в сутки).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_STUCK_TIMEOUT_MIN = int(os.environ.get("RECONCILER_STUCK_TIMEOUT_MIN", "30"))
_DRAFT_TIMEOUT_HOURS = int(os.environ.get("RECONCILER_DRAFT_TIMEOUT_HOURS", "24"))


async def reconcile_stuck_running(engine: AsyncEngine, *, now: datetime | None = None) -> int:
    """Переводит зависшие 'running' в 'retrying'.

    Returns: количество задач переведено.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=_STUCK_TIMEOUT_MIN)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    attempt_count = attempt_count + 1,
                    last_error = COALESCE(last_error, '') || ' [reconciler: stuck timeout]',
                    next_retry_at = :now,
                    updated_at = :now
                WHERE status = 'running'
                  AND updated_at < :cutoff
                """
            ),
            {"cutoff": cutoff, "now": now},
        )
        return result.rowcount or 0


async def cancel_old_drafts(engine: AsyncEngine, *, now: datetime | None = None) -> int:
    """Помечает draft'ы старше 24h как cancelled (страховка от потери)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=_DRAFT_TIMEOUT_HOURS)
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'cancelled',
                    last_error = 'auto-cancelled: draft timeout',
                    completed_at = :now,
                    updated_at = :now
                WHERE status = 'draft'
                  AND created_at < :cutoff
                """
            ),
            {"cutoff": cutoff, "now": now},
        )
        return result.rowcount or 0


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
