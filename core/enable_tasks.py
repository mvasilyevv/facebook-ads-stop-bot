# -*- coding: utf-8 -*-
"""Сервисные функции для жизненного цикла задач на включение."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.disable_tasks import is_delivery_disabled
from core.domain import EnableTaskStatus
from core.live_batch import LIVE_BATCH_WINDOW
from core.models import AdSnapshot, EnableRecommendationEvent, EnableTask, ObserverSettings

logger = logging.getLogger(__name__)

ENABLE_TASK_STALE_MINUTES = 5
ENABLE_TASK_STALE_TIMEOUT = timedelta(minutes=ENABLE_TASK_STALE_MINUTES)
ACTIVE_ENABLE_TASK_STATUSES = (
    EnableTaskStatus.PENDING,
    EnableTaskStatus.RUNNING,
    EnableTaskStatus.RETRYING,
)


async def reconcile_enable_tasks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """Согласовывает очередь включений с текущими snapshot и временем жизни задач."""
    current_time = now or datetime.now(UTC)
    stale_before = current_time - ENABLE_TASK_STALE_TIMEOUT
    last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
    cabinet_day_start = await session.scalar(
        select(ObserverSettings.cabinet_day_started_at).where(
            ObserverSettings.singleton_key == "default"
        )
    )
    active_cutoff = last_scan - LIVE_BATCH_WINDOW if last_scan is not None else None

    completed_ids: list[str] = []
    cancelled_ids: list[str] = []
    retried_ids: list[str] = []
    failed_ids: list[str] = []

    if cabinet_day_start is not None:
        previous_day_rows = await session.execute(
            select(EnableTask, EnableRecommendationEvent)
            .join(
                EnableRecommendationEvent,
                EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
                isouter=True,
            )
            .where(
                EnableTask.status.in_(ACTIVE_ENABLE_TASK_STATUSES),
                or_(
                    EnableRecommendationEvent.live_batch_started_at < cabinet_day_start,
                    and_(
                        EnableRecommendationEvent.id.is_(None),
                        EnableTask.created_at < cabinet_day_start,
                    ),
                ),
            )
            .order_by(EnableTask.created_at.asc())
        )
        previous_day_tasks = previous_day_rows.all()
        for task, _event in previous_day_tasks:
            task.status = EnableTaskStatus.CANCELLED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = "Задача отменена: начались новые сутки кабинета"
            cancelled_ids.append(task.fb_ad_id)
            logger.info(
                "Задача %s для %s отменена: начались новые сутки кабинета",
                task.id,
                task.fb_ad_id,
            )

        if previous_day_tasks:
            await session.flush()

    if active_cutoff is not None:
        archived_rows = await session.execute(
            select(EnableTask, AdSnapshot)
            .join(AdSnapshot, AdSnapshot.fb_ad_id == EnableTask.fb_ad_id, isouter=True)
            .where(
                EnableTask.status.in_(ACTIVE_ENABLE_TASK_STATUSES),
                or_(
                    AdSnapshot.id.is_(None),
                    AdSnapshot.last_observed_at.is_(None),
                    AdSnapshot.last_observed_at < active_cutoff,
                ),
            )
        )
        for task, _snapshot in archived_rows.all():
            task.status = EnableTaskStatus.CANCELLED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = "Задача отменена: объявление больше не входит в актуальный живой батч"
            cancelled_ids.append(task.fb_ad_id)
            logger.info(
                "Задача %s для %s отменена: объявление ушло из актуального живого батча",
                task.id,
                task.fb_ad_id,
            )

    if active_cutoff is not None and cancelled_ids:
        await session.flush()

    completed_rows = await session.execute(
        select(EnableTask, AdSnapshot)
        .join(AdSnapshot, AdSnapshot.fb_ad_id == EnableTask.fb_ad_id)
        .where(EnableTask.status.in_(ACTIVE_ENABLE_TASK_STATUSES))
    )
    for task, snapshot in completed_rows.all():
        if is_delivery_disabled(snapshot.delivery_status):
            continue

        task.status = EnableTaskStatus.SUCCEEDED
        task.completed_at = current_time
        task.next_retry_at = None
        task.last_error = None
        completed_ids.append(task.fb_ad_id)
        logger.info(
            "Задача %s для %s завершена автоматически: snapshot уже показывает включённое объявление",
            task.id,
            task.fb_ad_id,
        )

    if completed_ids:
        await session.flush()

    stale_rows = await session.execute(
        select(EnableTask, AdSnapshot)
        .join(AdSnapshot, AdSnapshot.fb_ad_id == EnableTask.fb_ad_id, isouter=True)
        .where(
            EnableTask.status == EnableTaskStatus.RUNNING,
            func.coalesce(EnableTask.updated_at, EnableTask.created_at) <= stale_before,
        )
        .order_by(EnableTask.updated_at.asc(), EnableTask.created_at.asc())
    )
    for task, snapshot in stale_rows.all():
        if snapshot is not None and not is_delivery_disabled(snapshot.delivery_status):
            task.status = EnableTaskStatus.SUCCEEDED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = None
            completed_ids.append(task.fb_ad_id)
            logger.info(
                "Задача %s для %s подтверждена после таймаута: snapshot уже показывает включённое объявление",
                task.id,
                task.fb_ad_id,
            )
            continue

        if task.attempt_count >= task.max_attempts:
            task.status = EnableTaskStatus.FAILED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = "Задача зависла в RUNNING и исчерпала лимит попыток"
            failed_ids.append(task.fb_ad_id)
            logger.error(
                "Задача %s для %s переведена в FAILED: зависла в RUNNING",
                task.id,
                task.fb_ad_id,
            )
            continue

        task.status = EnableTaskStatus.RETRYING
        task.completed_at = None
        task.next_retry_at = current_time
        task.last_error = (
            f"Предыдущая попытка зависла в RUNNING более {ENABLE_TASK_STALE_MINUTES} минут"
        )
        retried_ids.append(task.fb_ad_id)
        logger.warning(
            "Задача %s для %s зависла в RUNNING — возвращаю в RETRYING",
            task.id,
            task.fb_ad_id,
        )

    return {
        "completed": completed_ids,
        "cancelled": cancelled_ids,
        "retried": retried_ids,
        "failed": failed_ids,
    }
