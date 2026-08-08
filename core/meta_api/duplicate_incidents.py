# -*- coding: utf-8 -*-
"""Transactional incident projection for partial ad-set duplication.

This module deliberately exposes only ``AsyncConnection`` helpers.  A caller
cannot persist an incident independently of the fenced task transition that
made the operator warning necessary.
"""

from __future__ import annotations

from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncConnection

from core.telegram.worker_notify import (
    notify_recurring_incident_in_transaction,
    resolve_recurring_incident_in_transaction,
)

DuplicateIncidentStage = Literal[
    "partial",
    "recovery_scheduled",
    "recovery_retrying",
    "recovery_invalid",
    "checkpoint_missing",
]

_INCIDENT_KEY_PREFIX = "task:duplicate-adset:"


def duplicate_incident_key(task_id: int) -> str:
    """Return the stable incident key for one irreversible duplicate task."""
    if task_id <= 0:
        raise ValueError("task_id must be positive")
    return f"{_INCIDENT_KEY_PREFIX}{task_id}"


def _created_object_count(checkpoint: dict[str, Any]) -> int:
    created_ids = checkpoint.get("created_ids")
    if not isinstance(created_ids, dict):
        return 0
    return sum(len(values) for values in created_ids.values() if isinstance(values, list))


def _cleanup_failure_count(checkpoint: dict[str, Any]) -> int:
    failures = checkpoint.get("cleanup_failures")
    return len(failures) if isinstance(failures, list) else 0


def duplicate_requeue_stage(checkpoint: dict[str, Any]) -> DuplicateIncidentStage:
    """Classify an initial partial cleanup separately from later recovery retries."""
    if checkpoint.get("partial_fail") is True and not checkpoint.get("recovery_attempt"):
        return "partial"
    return "recovery_retrying"


async def project_duplicate_incident_in_transaction(
    conn: AsyncConnection,
    *,
    task_id: int,
    checkpoint: dict[str, Any],
    stage: DuplicateIncidentStage,
) -> None:
    """Open or refresh the task incident in the caller's fenced transaction."""
    created_count = _created_object_count(checkpoint)
    failure_count = _cleanup_failure_count(checkpoint)

    if stage == "partial":
        title = "Дублирование адсетов завершилось частично"
        summary = f"Задача #{task_id} · создано объектов: {created_count}"
        lines = [
            (
                "PAUSE-only recovery будет повторён автоматически"
                if failure_count
                else "Все известные созданные объекты подтверждены PAUSED"
            ),
            "Проверь результат вручную в Ads Manager",
        ]
        risk = (
            "Часть созданных объектов может оставаться активной"
            if failure_count
            else "Создание выполнено не полностью"
        )
    elif stage == "recovery_scheduled":
        title = "Запущен crash-recovery дублирования"
        summary = f"Задача #{task_id} · объектов в checkpoint: {created_count}"
        lines = [
            "Поставлен только PAUSE-recovery без повторного создания",
            "Проверь Ads Manager до подтверждённого PAUSED",
        ]
        risk = "Часть созданных объектов может оставаться активной"
    elif stage == "recovery_retrying":
        title = "Crash-recovery: PAUSE не подтверждён"
        summary = (
            f"Задача #{task_id} · ошибок PAUSE: {failure_count}"
            if failure_count
            else f"Задача #{task_id} · recovery будет повторён"
        )
        lines = [
            "Повторяется только PAUSE известных объектов",
            "Исходный create-план не запускается повторно",
        ]
        risk = "Часть созданных объектов может оставаться активной"
    elif stage == "recovery_invalid":
        title = "Crash-recovery не смог прочитать checkpoint"
        summary = f"Задача #{task_id} · требуется ручная проверка"
        lines = ["Проверь все созданные объекты в Ads Manager"]
        risk = "Фактический статус созданных объектов неизвестен"
    else:
        title = "Результат дублирования неизвестен"
        summary = f"Задача #{task_id} · checkpoint созданных объектов отсутствует"
        lines = [
            "Повторное создание заблокировано",
            "Проверь результат вручную в Ads Manager",
        ]
        risk = "Meta могла принять часть запросов до сбоя"

    await notify_recurring_incident_in_transaction(
        conn,
        incident_key=duplicate_incident_key(task_id),
        audience="owners",
        event_type=f"duplicate_adset_{stage}",
        severity="critical",
        title=title,
        summary=summary,
        lines=lines,
        risk=risk,
        resource_type="task",
        resource_id=str(task_id),
    )


async def resolve_duplicate_incident_in_transaction(
    conn: AsyncConnection,
    *,
    task_id: int,
    checkpoint: dict[str, Any],
) -> None:
    """Resolve the existing recovery incident or roll back the task finalizer."""
    created_count = _created_object_count(checkpoint)
    resolved = await resolve_recurring_incident_in_transaction(
        conn,
        incident_key=duplicate_incident_key(task_id),
        audience="owners",
        summary=(
            f"Задача #{task_id}: подтверждено PAUSED объектов — {created_count}. "
            "Повторного создания не было."
        ),
    )
    if not resolved:
        raise RuntimeError(f"duplicate recovery task {task_id} has no active incident to resolve")


__all__ = [
    "DuplicateIncidentStage",
    "duplicate_incident_key",
    "duplicate_requeue_stage",
    "project_duplicate_incident_in_transaction",
    "resolve_duplicate_incident_in_transaction",
]
