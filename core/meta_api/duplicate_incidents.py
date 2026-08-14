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
from core.wording import errors_ru, objects_ru

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

    created_text = objects_ru(created_count)
    if stage == "partial":
        title = "Дублирование адсетов прошло не полностью"
        summary = f"Задача #{task_id} · успело создаться {created_text}"
        lines = [
            (
                f"Не удалось выключить {errors_ru(failure_count)} — повторю сам"
                if failure_count
                else "Все созданные объекты уже выключены"
            ),
            "Проверь результат в Ads Manager",
        ]
        risk = (
            "Часть созданных объектов может остаться включённой"
            if failure_count
            else "Структура создана не до конца"
        )
    elif stage == "recovery_scheduled":
        title = "Доделываю дублирование после сбоя"
        summary = f"Задача #{task_id} · известно про {created_text}"
        lines = [
            "Только выключаю созданное, заново ничего не создаю",
            "Проверь Ads Manager, пока не подтвердится выключение",
        ]
        risk = "Часть созданных объектов может остаться включённой"
    elif stage == "recovery_retrying":
        title = "Не удалось выключить созданные объекты"
        summary = (
            f"Задача #{task_id} · {errors_ru(failure_count)} при выключении"
            if failure_count
            else f"Задача #{task_id} · попробую выключить ещё раз"
        )
        lines = [
            "Повторяю только выключение уже созданных объектов",
            "Создание заново не запускается",
        ]
        risk = "Часть созданных объектов может остаться включённой"
    elif stage == "recovery_invalid":
        title = "Не могу разобрать, что успело создаться"
        summary = f"Задача #{task_id} · нужна ручная проверка"
        lines = ["Проверь в Ads Manager все объекты этой задачи"]
        risk = "Фактический статус созданных объектов неизвестен"
    else:
        title = "Результат дублирования неизвестен"
        summary = f"Задача #{task_id} · записи о созданных объектах нет"
        lines = [
            "Повторное создание заблокировано",
            "Проверь результат в Ads Manager",
        ]
        risk = "Facebook мог принять часть запросов до сбоя"

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
            f"Задача #{task_id}: выключено {objects_ru(created_count)}. "
            "Заново ничего не создавалось."
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
