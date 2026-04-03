# -*- coding: utf-8 -*-
"""Сервисные функции для жизненного цикла задач на отключение."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask

logger = logging.getLogger(__name__)

DISABLE_TASK_STALE_MINUTES = 5
DISABLE_TASK_STALE_TIMEOUT = timedelta(minutes=DISABLE_TASK_STALE_MINUTES)
ACTIVE_DISABLE_TASK_WINDOW = timedelta(minutes=30)
SILENT_DISABLE_INCIDENT_RETRY_LIMIT = 3
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
    - отменяет задачи по объявлениям, которые уже выпали из актуальной скан-сессии;
    - возвращает застрявшие `RUNNING`-задачи в повторную обработку.
    """
    current_time = now or datetime.now(UTC)
    stale_before = current_time - DISABLE_TASK_STALE_TIMEOUT
    last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
    active_cutoff = last_scan - ACTIVE_DISABLE_TASK_WINDOW if last_scan is not None else None

    completed_ids: list[str] = []
    repaired_ids: list[str] = []
    cancelled_ids: list[str] = []
    retried_ids: list[str] = []
    failed_ids: list[str] = []

    completed_rows = await session.execute(
        select(DisableTask, AdSnapshot)
        .join(AdSnapshot, AdSnapshot.ad_id == DisableTask.ad_id)
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

    repaired_rows = await session.execute(
        select(AdSnapshot, DisableTask.id)
        .join(DisableTask, DisableTask.ad_id == AdSnapshot.ad_id)
        .where(
            DisableTask.status == DisableTaskStatus.SUCCEEDED,
            AdSnapshot.delivery_status.in_(DISABLED_DELIVERY_STATUSES),
            AdSnapshot.alert_state != AlertState.DISABLED,
        )
    )
    repaired_seen: set[str] = set()
    for snapshot, task_id in repaired_rows.all():
        if snapshot.fb_ad_id in repaired_seen:
            continue
        snapshot.alert_state = AlertState.DISABLED
        repaired_ids.append(snapshot.fb_ad_id)
        repaired_seen.add(snapshot.fb_ad_id)
        logger.info(
            "Снэпшот %s выровнен в DISABLED по успешной задаче %s",
            snapshot.fb_ad_id,
            task_id,
        )

    if repaired_ids:
        await session.flush()

    if active_cutoff is not None:
        archived_rows = await session.execute(
            select(DisableTask, AdSnapshot)
            .join(AdSnapshot, AdSnapshot.ad_id == DisableTask.ad_id, isouter=True)
            .where(
                DisableTask.status.in_(ACTIVE_DISABLE_TASK_STATUSES),
                or_(
                    AdSnapshot.id.is_(None),
                    AdSnapshot.last_observed_at.is_(None),
                    AdSnapshot.last_observed_at < active_cutoff,
                ),
            )
        )
        for task, snapshot in archived_rows.all():
            task.status = DisableTaskStatus.CANCELLED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = (
                "Задача отменена: объявление больше не входит в актуальную скан-сессию"
            )
            cancelled_ids.append(task.fb_ad_id)
            if snapshot is not None:
                if is_delivery_disabled(snapshot.delivery_status):
                    snapshot.alert_state = AlertState.DISABLED
                else:
                    snapshot.alert_state = AlertState.NORMAL
                    if hasattr(snapshot, "open_state_token"):
                        snapshot.open_state_token = None
            logger.info(
                "Задача %s для %s отменена: объявление ушло в архив текущей скан-сессии",
                task.id,
                task.fb_ad_id,
            )

    if cancelled_ids:
        await session.flush()

    stale_rows = await session.execute(
        select(DisableTask, AdSnapshot)
        .join(AdSnapshot, AdSnapshot.ad_id == DisableTask.ad_id, isouter=True)
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
        "repaired": repaired_ids,
        "cancelled": cancelled_ids,
        "retried": retried_ids,
        "failed": failed_ids,
    }
