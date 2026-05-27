# -*- coding: utf-8 -*-
"""Reconciler для meta_api_mutation задач.

Общий reconciler_worker уже работает по всем task_type (см. CLAUDE.md):
- RUNNING > 30 мин → RETRYING
- DRAFT > 24 ч → CANCELLED

Эти функции — тонкие фильтрованные обёртки на случай, если мы захотим
запускать отдельно (например, более агрессивный таймаут для meta_api).
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_TASK_TYPE = "meta_api_mutation"


async def reconcile_stuck_meta_running(
    engine: AsyncEngine,
    *,
    stuck_after_seconds: int = 1800,
) -> int:
    """Зависшие в RUNNING (worker крашнулся) → RETRYING. Только meta_api_mutation.

    Возвращает число восстановленных строк.
    """
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'retrying',
                    next_retry_at = NOW(),
                    last_error = COALESCE(last_error, '') || ' [stuck timeout reconciled]',
                    updated_at = NOW()
                WHERE task_type = :tt
                  AND status = 'running'
                  AND updated_at < NOW() - make_interval(secs => :sec)
                """
            ),
            {"tt": _TASK_TYPE, "sec": int(stuck_after_seconds)},
        )
        n = int(result.rowcount or 0)
    if n:
        logger.info("meta_api reconciler: %d stuck running → retrying", n)
    return n


async def cancel_stale_meta_drafts(
    engine: AsyncEngine,
    *,
    older_than_seconds: int = 24 * 3600,
) -> int:
    """AI-drafts старше N секунд без подтверждения → CANCELLED. Только meta_api_mutation."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                """
                UPDATE task_queue
                SET status = 'cancelled',
                    completed_at = NOW(),
                    last_error = 'draft expired without confirmation',
                    updated_at = NOW()
                WHERE task_type = :tt
                  AND status = 'draft'
                  AND created_at < NOW() - make_interval(secs => :sec)
                """
            ),
            {"tt": _TASK_TYPE, "sec": int(older_than_seconds)},
        )
        n = int(result.rowcount or 0)
    if n:
        logger.info("meta_api reconciler: %d stale drafts → cancelled", n)
    return n


async def reconcile_all(
    engine: AsyncEngine,
    *,
    stuck_after_seconds: int = 1800,
    draft_older_than_seconds: int = 24 * 3600,
) -> dict[str, int]:
    """Удобный комбайн: оба прохода за один вызов."""
    return {
        "stuck_running": await reconcile_stuck_meta_running(
            engine, stuck_after_seconds=stuck_after_seconds
        ),
        "stale_drafts": await cancel_stale_meta_drafts(
            engine, older_than_seconds=draft_older_than_seconds
        ),
    }
