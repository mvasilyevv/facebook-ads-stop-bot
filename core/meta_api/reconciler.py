# -*- coding: utf-8 -*-
"""Reconciler для MetaApiMutationTask: очистка протухших черновиков и зависших задач.

Запускается из meta_api_worker раз в минуту. Не вмешивается в PENDING/SUCCESS/FAILED.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import MetaApiMutationTask

logger = logging.getLogger(__name__)

# Статусы (строки, не Enum — модель хранит строки с CHECK constraint)
_DRAFT_STATUS = "DRAFT"
_RUNNING_STATUS = "RUNNING"
_PENDING_STATUS = "PENDING"
_CANCELLED_STATUS = "CANCELLED"


async def reconcile_expired_drafts(
    db: AsyncSession,
    *,
    max_age_hours: int = 24,
) -> int:
    """Перевести все DRAFT задачи старше N часов в CANCELLED.

    Защита от забытых черновиков AI-ассистента: пользователь не подтвердил
    задачу в течение max_age_hours — она автоматически отменяется.

    Args:
        db: AsyncSession
        max_age_hours: срок жизни DRAFT в часах

    Returns:
        Количество переведённых задач.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=max_age_hours)
    result = await db.execute(
        select(MetaApiMutationTask).where(
            MetaApiMutationTask.status == _DRAFT_STATUS,
            MetaApiMutationTask.created_at < cutoff,
        )
    )
    tasks = list(result.scalars())
    if not tasks:
        return 0

    now = datetime.now(UTC)
    for task in tasks:
        task.status = _CANCELLED_STATUS
        task.last_error = f"Черновик автоматически отменён: не подтверждён за {max_age_hours} ч"
        task.completed_at = now
        logger.info(
            "Reconciler: DRAFT задача %s (%s) отменена по истечении %d ч",
            task.id,
            task.mutation_kind,
            max_age_hours,
        )

    await db.flush()
    return len(tasks)


async def reconcile_stuck_running(
    db: AsyncSession,
    *,
    max_running_minutes: int = 30,
) -> int:
    """Перевести RUNNING задачи, зависшие дольше N минут, обратно в PENDING.

    Защита от ситуации когда worker упал в середине исполнения задачи:
    статус остался RUNNING, но никто эту задачу больше не возьмёт.
    Сбрасываем в PENDING чтобы следующий worker цикл подхватил задачу.

    Note: attempt_count не сбрасываем — уже выполненные попытки учитываются.

    Args:
        db: AsyncSession
        max_running_minutes: допустимое время нахождения в статусе RUNNING

    Returns:
        Количество переведённых задач.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=max_running_minutes)

    # Ищем RUNNING задачи у которых updated_at (или created_at при отсутствии updated_at)
    # старше cutoff
    result = await db.execute(
        select(MetaApiMutationTask).where(
            MetaApiMutationTask.status == _RUNNING_STATUS,
            # используем updated_at как proxy для времени перехода в RUNNING
            MetaApiMutationTask.updated_at <= cutoff,
        )
    )
    tasks = list(result.scalars())
    if not tasks:
        return 0

    now = datetime.now(UTC)
    for task in tasks:
        task.status = _PENDING_STATUS
        task.next_retry_at = now  # доступна немедленно
        task.last_error = (
            f"Задача зависла в RUNNING более {max_running_minutes} мин — "
            "возвращена в PENDING для повторной попытки"
        )
        logger.warning(
            "Reconciler: RUNNING задача %s (%s) зависла более %d мин — возвращаю в PENDING",
            task.id,
            task.mutation_kind,
            max_running_minutes,
        )

    await db.flush()
    return len(tasks)


async def reconcile_all(db: AsyncSession) -> dict[str, int]:
    """Запустить все reconcile-шаги. Возвращает счётчики по каждому шагу.

    Используется в meta_api_worker для периодической очистки.
    Все шаги выполняются в одной транзакции (commit делает вызывающий код).

    Returns:
        Словарь {"expired_drafts": N, "stuck_running": N}.
    """
    expired_drafts = await reconcile_expired_drafts(db)
    stuck_running = await reconcile_stuck_running(db)

    if expired_drafts or stuck_running:
        logger.info(
            "Reconciler: отменено черновиков=%d, восстановлено из RUNNING=%d",
            expired_drafts,
            stuck_running,
        )

    return {
        "expired_drafts": expired_drafts,
        "stuck_running": stuck_running,
    }
