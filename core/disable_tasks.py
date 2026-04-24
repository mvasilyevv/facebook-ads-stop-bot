# -*- coding: utf-8 -*-
"""Сервисные функции для жизненного цикла задач на отключение."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.domain import AlertState, DisableTaskStatus
from core.models import AdSnapshot, DisableTask, FbAd

logger = logging.getLogger(__name__)

DISABLE_TASK_STALE_MINUTES = 2
DISABLE_TASK_STALE_TIMEOUT = timedelta(minutes=DISABLE_TASK_STALE_MINUTES)
ACTIVE_DISABLE_TASK_WINDOW = timedelta(minutes=30)
SILENT_DISABLE_INCIDENT_RETRY_LIMIT = 3
DISABLED_DELIVERY_STATUSES = ("OFF",)
ACTIVE_DISABLE_TASK_STATUSES = (
    DisableTaskStatus.PENDING,
    DisableTaskStatus.RUNNING,
    DisableTaskStatus.RETRYING,
)


def calculate_active_disable_cutoff(
    *,
    now: datetime,
    last_scan: datetime | None,
) -> datetime:
    """Возвращает нижнюю границу актуальной scan-сессии для disable-очереди.

    Если observer давно не сканировал из-за зависшей очереди, нельзя опираться
    только на старый last_scan: иначе PENDING/RUNNING-задачи могут держать UI
    в состоянии «Браузер занят» бесконечно. В таком случае берём окно от
    текущего времени и даём старым задачам корректно уйти в CANCELLED/RETRYING.
    """
    reference_time = now if last_scan is None else max(now, last_scan)
    return reference_time - ACTIVE_DISABLE_TASK_WINDOW


def normalize_delivery_status(delivery_status: str | None) -> str:
    """Приводит статус доставки к каноническому виду, даже если в БД лежит локализованный текст."""
    value = (delivery_status or "").strip()
    if not value:
        return ""

    upper_value = value.upper()
    if upper_value in DISABLED_DELIVERY_STATUSES:
        return upper_value

    lower_value = value.lower()
    if (
        "off" in lower_value
        or "выключ" in lower_value
        or "вимкнен" in lower_value
        or "disabled" in lower_value
    ):
        return "OFF"

    if (
        "not delivering" in lower_value
        or "не достав" in lower_value
        or "не показ" in lower_value
        or "показ кампани" in lower_value
        or "показ кампан" in lower_value
        or "delivery stopped" in lower_value
    ):
        return "NOT_DELIVERING"

    return upper_value


def is_delivery_disabled(delivery_status: str | None) -> bool:
    """Проверяет, что статус доставки однозначно означает выключенный тумблер объявления."""
    return normalize_delivery_status(delivery_status) in DISABLED_DELIVERY_STATUSES


async def reconcile_disable_tasks(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """Согласовывает очередь отключений с фактическим состоянием объявлений.

    Делает две вещи:
    - завершает активные задачи, если observer уже увидел выключенный статус доставки;
    - отменяет задачи по объявлениям, которые уже выпали из актуальной скан-сессии;
    - возвращает застрявшие `RUNNING`-задачи в повторную обработку.
    """
    current_time = now or datetime.now(UTC)
    stale_before = current_time - DISABLE_TASK_STALE_TIMEOUT
    last_scan = await session.scalar(select(func.max(AdSnapshot.last_observed_at)))
    active_cutoff = calculate_active_disable_cutoff(
        now=current_time,
        last_scan=last_scan,
    )

    completed_ids: list[str] = []
    repaired_ids: list[str] = []
    cancelled_ids: list[str] = []
    retried_ids: list[str] = []
    failed_ids: list[str] = []

    completed_rows = await session.execute(
        select(DisableTask, AdSnapshot, FbAd.fb_ad_id)
        .join(AdSnapshot, AdSnapshot.ad_id == DisableTask.ad_id)
        .join(FbAd, FbAd.id == DisableTask.ad_id)
        .where(
            DisableTask.status.in_(ACTIVE_DISABLE_TASK_STATUSES),
        )
    )
    for task, snapshot, fb_ad_id in completed_rows.all():
        if not is_delivery_disabled(snapshot.delivery_status):
            continue
        task.status = DisableTaskStatus.SUCCEEDED
        task.completed_at = current_time
        task.next_retry_at = None
        task.last_error = None
        snapshot.alert_state = AlertState.DISABLED
        completed_ids.append(fb_ad_id)
        logger.info(
            "Задача %s для %s завершена автоматически: объявление уже %s",
            task.id,
            fb_ad_id,
            snapshot.delivery_status,
        )

    if completed_ids:
        await session.flush()

    repaired_rows = await session.execute(
        select(AdSnapshot, DisableTask.id)
        .join(DisableTask, DisableTask.ad_id == AdSnapshot.ad_id)
        .where(
            DisableTask.status == DisableTaskStatus.SUCCEEDED,
            AdSnapshot.alert_state != AlertState.DISABLED,
        )
    )
    repaired_seen: set[str] = set()
    for snapshot, task_id in repaired_rows.all():
        if not is_delivery_disabled(snapshot.delivery_status):
            continue
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

    archived_rows = await session.execute(
        select(DisableTask, AdSnapshot, FbAd.fb_ad_id)
        .join(AdSnapshot, AdSnapshot.ad_id == DisableTask.ad_id, isouter=True)
        .join(FbAd, FbAd.id == DisableTask.ad_id)
        .where(
            DisableTask.status.in_(ACTIVE_DISABLE_TASK_STATUSES),
            or_(
                AdSnapshot.id.is_(None),
                AdSnapshot.last_observed_at.is_(None),
                AdSnapshot.last_observed_at < active_cutoff,
            ),
        )
    )
    for task, snapshot, fb_ad_id in archived_rows.all():
        task.status = DisableTaskStatus.CANCELLED
        task.completed_at = current_time
        task.next_retry_at = None
        task.last_error = "Задача отменена: объявление больше не входит в актуальную скан-сессию"
        cancelled_ids.append(snapshot.fb_ad_id if snapshot is not None else fb_ad_id)
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
            fb_ad_id,
        )

    if cancelled_ids:
        await session.flush()

    stale_rows = await session.execute(
        select(DisableTask, AdSnapshot, FbAd.fb_ad_id)
        .join(AdSnapshot, AdSnapshot.ad_id == DisableTask.ad_id, isouter=True)
        .join(FbAd, FbAd.id == DisableTask.ad_id)
        .where(
            DisableTask.status == DisableTaskStatus.RUNNING,
            func.coalesce(DisableTask.updated_at, DisableTask.created_at) <= stale_before,
        )
        .order_by(DisableTask.updated_at.asc(), DisableTask.created_at.asc())
    )
    for task, snapshot, fb_ad_id in stale_rows.all():
        if snapshot and is_delivery_disabled(snapshot.delivery_status):
            task.status = DisableTaskStatus.SUCCEEDED
            task.completed_at = current_time
            task.next_retry_at = None
            task.last_error = None
            snapshot.alert_state = AlertState.DISABLED
            completed_ids.append(snapshot.fb_ad_id)
            logger.info(
                "Задача %s для %s подтверждена после таймаута: объявление уже %s",
                task.id,
                fb_ad_id,
                snapshot.delivery_status,
            )
            continue

        if task.attempt_count >= task.max_attempts:
            task.status = DisableTaskStatus.FAILED
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

        task.status = DisableTaskStatus.RETRYING
        task.completed_at = None
        task.next_retry_at = current_time
        task.last_error = (
            f"Предыдущая попытка зависла в RUNNING более {DISABLE_TASK_STALE_MINUTES} мин"
        )
        retried_ids.append(fb_ad_id)
        logger.warning(
            "Задача %s для %s зависла в RUNNING — возвращаю в RETRYING",
            task.id,
            fb_ad_id,
        )

    return {
        "completed": completed_ids,
        "repaired": repaired_ids,
        "cancelled": cancelled_ids,
        "retried": retried_ids,
        "failed": failed_ids,
    }
