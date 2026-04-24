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
from core.models import AdSnapshot, EnableRecommendationEvent, EnableTask, FbAd
from core.settings_queries import get_observer_settings

logger = logging.getLogger(__name__)

ENABLE_TASK_STALE_MINUTES = 2
ENABLE_TASK_STALE_TIMEOUT = timedelta(minutes=ENABLE_TASK_STALE_MINUTES)
ACTIVE_ENABLE_TASK_STATUSES = (
    EnableTaskStatus.PENDING,
    EnableTaskStatus.RUNNING,
    EnableTaskStatus.RETRYING,
)


def calculate_active_enable_cutoff(
    *,
    now: datetime,
    last_scan: datetime | None,
) -> datetime:
    """Возвращает нижнюю границу актуального живого батча для enable-очереди.

    Если observer давно не обновлял snapshot, нельзя держать enable-задачи
    бесконечно активными только из-за старого last_scan. В таком случае
    опираемся на текущее время и даём self-heal снять устаревшие задачи.
    """
    reference_time = now if last_scan is None else max(now, last_scan)
    return reference_time - LIVE_BATCH_WINDOW


async def reconcile_enable_tasks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """Согласовывает очередь включений с текущими snapshot и временем жизни задач."""
    current_time = now or datetime.now(UTC)
    stale_before = current_time - ENABLE_TASK_STALE_TIMEOUT
    last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
    _obs = await get_observer_settings(session)
    cabinet_day_start = _obs.cabinet_day_started_at if _obs else None
    active_cutoff = calculate_active_enable_cutoff(
        now=current_time,
        last_scan=last_scan,
    )

    completed_ids: list[str] = []
    cancelled_ids: list[str] = []
    retried_ids: list[str] = []
    failed_ids: list[str] = []

    if cabinet_day_start is not None:
        previous_day_rows = await session.execute(
            select(EnableTask, EnableRecommendationEvent, FbAd.fb_ad_id)
            .join(
                EnableRecommendationEvent,
                EnableRecommendationEvent.id == EnableTask.recommendation_event_id,
                isouter=True,
            )
            .join(FbAd, FbAd.id == EnableTask.ad_id)
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
        for task, _event, fb_ad_id in previous_day_tasks:
            task.status = EnableTaskStatus.CANCELLED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = "Задача отменена: начались новые сутки кабинета"
            cancelled_ids.append(fb_ad_id)
            logger.info(
                "Задача %s для %s отменена: начались новые сутки кабинета",
                task.id,
                fb_ad_id,
            )

        if previous_day_tasks:
            await session.flush()

    archived_rows = await session.execute(
        select(EnableTask, AdSnapshot, FbAd.fb_ad_id)
        .join(AdSnapshot, AdSnapshot.ad_id == EnableTask.ad_id, isouter=True)
        .join(FbAd, FbAd.id == EnableTask.ad_id)
        .where(
            EnableTask.status.in_(ACTIVE_ENABLE_TASK_STATUSES),
            or_(
                AdSnapshot.id.is_(None),
                AdSnapshot.last_observed_at.is_(None),
                AdSnapshot.last_observed_at < active_cutoff,
            ),
        )
    )
    for task, _snapshot, fb_ad_id in archived_rows.all():
        task.status = EnableTaskStatus.CANCELLED
        task.completed_at = current_time
        task.next_retry_at = None
        task.last_error = "Задача отменена: объявление больше не входит в актуальный живой батч"
        cancelled_ids.append(fb_ad_id)
        logger.info(
            "Задача %s для %s отменена: объявление ушло из актуального живого батча",
            task.id,
            fb_ad_id,
        )

    if cancelled_ids:
        await session.flush()

    completed_rows = await session.execute(
        select(EnableTask, AdSnapshot, FbAd.fb_ad_id)
        .join(AdSnapshot, AdSnapshot.ad_id == EnableTask.ad_id)
        .join(FbAd, FbAd.id == EnableTask.ad_id)
        .where(EnableTask.status.in_(ACTIVE_ENABLE_TASK_STATUSES))
    )
    for task, snapshot, fb_ad_id in completed_rows.all():
        if is_delivery_disabled(snapshot.delivery_status):
            continue

        task.status = EnableTaskStatus.SUCCEEDED
        task.completed_at = current_time
        task.next_retry_at = None
        task.last_error = None
        completed_ids.append(fb_ad_id)
        logger.info(
            "Задача %s для %s завершена автоматически: snapshot уже показывает включённое объявление",
            task.id,
            fb_ad_id,
        )

    if completed_ids:
        await session.flush()

    stale_rows = await session.execute(
        select(EnableTask, AdSnapshot, FbAd.fb_ad_id)
        .join(AdSnapshot, AdSnapshot.ad_id == EnableTask.ad_id, isouter=True)
        .join(FbAd, FbAd.id == EnableTask.ad_id)
        .where(
            EnableTask.status == EnableTaskStatus.RUNNING,
            func.coalesce(EnableTask.updated_at, EnableTask.created_at) <= stale_before,
        )
        .order_by(EnableTask.updated_at.asc(), EnableTask.created_at.asc())
    )
    for task, snapshot, fb_ad_id in stale_rows.all():
        if snapshot is not None and not is_delivery_disabled(snapshot.delivery_status):
            task.status = EnableTaskStatus.SUCCEEDED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = None
            completed_ids.append(fb_ad_id)
            logger.info(
                "Задача %s для %s подтверждена после таймаута: snapshot уже показывает включённое объявление",
                task.id,
                fb_ad_id,
            )
            continue

        if task.attempt_count >= task.max_attempts:
            task.status = EnableTaskStatus.FAILED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = "Задача зависла в RUNNING и исчерпала лимит попыток"
            failed_ids.append(fb_ad_id)
            logger.error(
                "Задача %s для %s переведена в FAILED: зависла в RUNNING",
                task.id,
                fb_ad_id,
            )
            continue

        task.status = EnableTaskStatus.RETRYING
        task.completed_at = None
        task.next_retry_at = current_time
        task.last_error = (
            f"Предыдущая попытка зависла в RUNNING более {ENABLE_TASK_STALE_MINUTES} минут"
        )
        retried_ids.append(fb_ad_id)
        logger.warning(
            "Задача %s для %s зависла в RUNNING — возвращаю в RETRYING",
            task.id,
            fb_ad_id,
        )

    return {
        "completed": completed_ids,
        "cancelled": cancelled_ids,
        "retried": retried_ids,
        "failed": failed_ids,
    }
