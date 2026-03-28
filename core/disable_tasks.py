# -*- coding: utf-8 -*-
"""Сервисные функции для жизненного цикла задач на отключение."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask

logger = logging.getLogger(__name__)

DISABLE_TASK_STALE_MINUTES = 5
DISABLE_TASK_STALE_TIMEOUT = timedelta(minutes=DISABLE_TASK_STALE_MINUTES)
DISABLED_DELIVERY_STATUSES = ("OFF", "NOT_DELIVERING")
ACTIVE_DISABLE_TASK_STATUSES = (
    DisableTaskStatus.PENDING,
    DisableTaskStatus.RUNNING,
    DisableTaskStatus.RETRYING,
)


def is_delivery_disabled(delivery_status: str | None) -> bool:
    """Проверяет, что статус доставки однозначно означает выключенное объявление."""
    return (delivery_status or "").upper() in DISABLED_DELIVERY_STATUSES


async def reconcile_disable_tasks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """Согласовывает очередь отключений с фактическим состоянием объявлений.

    Делает две вещи:
    - завершает активные задачи, если observer уже увидел `OFF`/`NOT_DELIVERING`;
    - возвращает застрявшие `RUNNING`-задачи в повторную обработку.
    """
    current_time = now or datetime.now(UTC)
    stale_before = current_time - DISABLE_TASK_STALE_TIMEOUT

    completed_ids: list[str] = []
    retried_ids: list[str] = []
    failed_ids: list[str] = []

    completed_rows = await session.execute(
        select(DisableTask, AdSnapshot)
        .join(AdSnapshot, AdSnapshot.fb_ad_id == DisableTask.fb_ad_id)
        .where(
            DisableTask.status.in_(ACTIVE_DISABLE_TASK_STATUSES),
            AdSnapshot.delivery_status.in_(DISABLED_DELIVERY_STATUSES),
        )
    )
    for task, snapshot in completed_rows.all():
        task.status = DisableTaskStatus.SUCCEEDED
        task.completed_at = current_time
        task.next_retry_at = None
        task.last_error = None
        snapshot.alert_state = AlertState.DISABLED
        completed_ids.append(task.fb_ad_id)
        logger.info(
            "Задача %s для %s завершена автоматически: объявление уже %s",
            task.id,
            task.fb_ad_id,
            snapshot.delivery_status,
        )

    if completed_ids:
        await session.flush()

    stale_rows = await session.execute(
        select(DisableTask, AdSnapshot)
        .join(AdSnapshot, AdSnapshot.fb_ad_id == DisableTask.fb_ad_id, isouter=True)
        .where(
            DisableTask.status == DisableTaskStatus.RUNNING,
            func.coalesce(DisableTask.updated_at, DisableTask.created_at) <= stale_before,
        )
        .order_by(DisableTask.updated_at.asc(), DisableTask.created_at.asc())
    )
    for task, snapshot in stale_rows.all():
        if snapshot and is_delivery_disabled(snapshot.delivery_status):
            task.status = DisableTaskStatus.SUCCEEDED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = None
            snapshot.alert_state = AlertState.DISABLED
            completed_ids.append(task.fb_ad_id)
            logger.info(
                "Задача %s для %s подтверждена после таймаута: объявление уже %s",
                task.id,
                task.fb_ad_id,
                snapshot.delivery_status,
            )
            continue

        if task.attempt_count >= task.max_attempts:
            task.status = DisableTaskStatus.FAILED
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

        task.status = DisableTaskStatus.RETRYING
        task.completed_at = None
        task.next_retry_at = current_time
        task.last_error = (
            f"Предыдущая попытка зависла в RUNNING более {DISABLE_TASK_STALE_MINUTES} минут"
        )
        retried_ids.append(task.fb_ad_id)
        logger.warning(
            "Задача %s для %s зависла в RUNNING — возвращаю в RETRYING",
            task.id,
            task.fb_ad_id,
        )

    return {
        "completed": completed_ids,
        "retried": retried_ids,
        "failed": failed_ids,
    }
