# -*- coding: utf-8 -*-
"""CRUD-функции и helpers для MetaApiMutationTask (outbox-паттерн).

Жизненный цикл задачи:
  DRAFT → PENDING → RUNNING → SUCCESS
                           ↘ FAILED (исчерпаны попытки)
         (retry)  RUNNING → PENDING (если retry_in_seconds задан)
  DRAFT → CANCELLED (по reconciler или ручной отмене)
"""

from __future__ import annotations

import hashlib
import json
import uuid as _uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import MetaApiMutationTask

# Допустимые статусы для claim (берём PENDING + RUNNING-с-retry не трогаем,
# claim берёт только PENDING у кого next_retry_at IS NULL или <= now)
_PENDING_STATUS = "PENDING"
_RUNNING_STATUS = "RUNNING"
_SUCCESS_STATUS = "SUCCESS"
_FAILED_STATUS = "FAILED"
_DRAFT_STATUS = "DRAFT"
_CANCELLED_STATUS = "CANCELLED"


def generate_idempotency_key(
    mutation_kind: str,
    target_id: str,
    payload: dict[str, Any],
) -> str:
    """Детерминированный ключ из (kind, target, payload). SHA256 hex, 64 символа.

    Одинаковые входы всегда дают одинаковый ключ — защита от дублей.
    """
    # Сериализуем payload с сортировкой ключей для детерминизма
    payload_str = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    raw = f"{mutation_kind}:{target_id}:{payload_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


async def create_mutation_task(
    db: AsyncSession,
    *,
    mutation_kind: str,
    target_id: str,
    ad_account_id: str,
    payload: dict[str, Any],
    requested_by: str,
    idempotency_key: str | None = None,
    initial_status: str = "PENDING",
    max_attempts: int = 5,
) -> MetaApiMutationTask:
    """Создать новую mutation-задачу.

    Если задача с таким idempotency_key уже существует — возвращает существующую
    без создания дубликата.

    Args:
        db: AsyncSession
        mutation_kind: тип мутации ("pause_ad", "set_budget", "clone_campaign", ...)
        target_id: FB entity id (ad_id, campaign_id, adset_id)
        ad_account_id: ID рекламного кабинета (обязателен для rate-limit учёта)
        payload: параметры мутации в виде словаря
        requested_by: кто создаёт задачу ("ai_assistant", "manual_tg:<user_id>", ...)
        idempotency_key: если None — генерируется автоматически из (kind, target, payload)
        initial_status: "PENDING" для немедленного исполнения, "DRAFT" для AI-черновиков
        max_attempts: максимальное число попыток выполнения
    """
    # Генерируем ключ если не передан
    if idempotency_key is None:
        idempotency_key = generate_idempotency_key(mutation_kind, target_id, payload)

    # Проверяем дубликат по idempotency_key
    existing = await db.scalar(
        select(MetaApiMutationTask).where(MetaApiMutationTask.idempotency_key == idempotency_key)
    )
    if existing is not None:
        return existing

    task = MetaApiMutationTask(
        mutation_kind=mutation_kind,
        target_id=target_id,
        ad_account_id=ad_account_id,
        payload_json=payload,
        requested_by=requested_by,
        idempotency_key=idempotency_key,
        status=initial_status,
        max_attempts=max_attempts,
        attempt_count=0,
    )
    db.add(task)
    await db.flush()  # присваивает id без коммита — caller делает commit сам
    return task


async def approve_draft_task(
    db: AsyncSession,
    *,
    task_id: _uuid.UUID,
    approved_by: str,
    approval_telegram_message_id: int | None = None,
) -> MetaApiMutationTask:
    """Перевести задачу из DRAFT → PENDING.

    Raises:
        ValueError: если задача не в статусе DRAFT или не найдена.
    """
    task = await db.scalar(select(MetaApiMutationTask).where(MetaApiMutationTask.id == task_id))
    if task is None:
        raise ValueError(f"Задача {task_id} не найдена")
    if task.status != _DRAFT_STATUS:
        raise ValueError(f"Задача {task_id} не в статусе DRAFT (текущий статус: {task.status})")

    now = datetime.now(UTC)
    task.status = _PENDING_STATUS
    task.approved_by = approved_by
    task.approved_at = now
    if approval_telegram_message_id is not None:
        task.approval_telegram_message_id = approval_telegram_message_id
    await db.flush()
    return task


async def cancel_draft_task(
    db: AsyncSession,
    *,
    task_id: _uuid.UUID,
    cancelled_by: str,
    reason: str = "",
) -> MetaApiMutationTask:
    """Перевести задачу из DRAFT → CANCELLED.

    Raises:
        ValueError: если задача не в статусе DRAFT или не найдена.
    """
    task = await db.scalar(select(MetaApiMutationTask).where(MetaApiMutationTask.id == task_id))
    if task is None:
        raise ValueError(f"Задача {task_id} не найдена")
    if task.status != _DRAFT_STATUS:
        raise ValueError(f"Задача {task_id} не в статусе DRAFT (текущий статус: {task.status})")

    task.status = _CANCELLED_STATUS
    task.last_error = f"Отменено: {reason}" if reason else f"Отменено пользователем {cancelled_by}"
    task.completed_at = datetime.now(UTC)
    await db.flush()
    return task


async def claim_pending_task(db: AsyncSession) -> MetaApiMutationTask | None:
    """SELECT ... FOR UPDATE SKIP LOCKED: атомарный захват одной PENDING-задачи.

    Учитывает next_retry_at — не берёт задачи с next_retry_at > now.
    Возвращает None если нет доступных задач.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        select(MetaApiMutationTask)
        .where(
            MetaApiMutationTask.status == _PENDING_STATUS,
            # next_retry_at IS NULL (первая попытка) или уже наступило
            (MetaApiMutationTask.next_retry_at.is_(None))
            | (MetaApiMutationTask.next_retry_at <= now),
        )
        .order_by(MetaApiMutationTask.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    task = result.scalar_one_or_none()
    if task is None:
        return None

    task.status = _RUNNING_STATUS
    task.attempt_count += 1
    await db.flush()
    return task


async def mark_succeeded(
    db: AsyncSession,
    *,
    task_id: _uuid.UUID,
    result: dict[str, Any] | None = None,
) -> None:
    """Перевести задачу RUNNING → SUCCESS. Записывает completed_at и result_json."""
    task = await db.scalar(select(MetaApiMutationTask).where(MetaApiMutationTask.id == task_id))
    if task is None:
        return

    task.status = _SUCCESS_STATUS
    task.completed_at = datetime.now(UTC)
    task.next_retry_at = None
    task.last_error = None
    if result is not None:
        task.result_json = result
    await db.flush()


async def mark_failed(
    db: AsyncSession,
    *,
    task_id: _uuid.UUID,
    error_message: str,
    error_code: int | None = None,
    error_subcode: int | None = None,
    retry_in_seconds: int | None = None,
) -> MetaApiMutationTask:
    """Перевести задачу RUNNING → FAILED или RUNNING → PENDING (если retry).

    Если retry_in_seconds передан и попытки не исчерпаны — ставит статус PENDING
    с next_retry_at для следующего подхода.
    Если retry_in_seconds=None или попытки исчерпаны — ставит FAILED.

    Args:
        task_id: UUID задачи
        error_message: текст ошибки
        error_code: код ошибки Meta API (опционально)
        error_subcode: subcode ошибки Meta API (опционально)
        retry_in_seconds: задержка перед повтором; None → не retry

    Returns:
        Обновлённая задача.
    """
    task = await db.scalar(select(MetaApiMutationTask).where(MetaApiMutationTask.id == task_id))
    if task is None:
        raise ValueError(f"Задача {task_id} не найдена")

    # Обновляем поля ошибки
    task.last_error = error_message[:2000] if error_message else error_message
    if error_code is not None:
        task.error_code = error_code
    if error_subcode is not None:
        task.error_subcode = error_subcode

    # Определяем: retry или финальный FAILED
    attempts_exhausted = task.attempt_count >= task.max_attempts
    if retry_in_seconds is not None and not attempts_exhausted:
        # Возвращаем в PENDING с отложенным стартом
        task.status = _PENDING_STATUS
        task.next_retry_at = datetime.now(UTC) + timedelta(seconds=retry_in_seconds)
    else:
        # Окончательный провал
        task.status = _FAILED_STATUS
        task.completed_at = datetime.now(UTC)
        task.next_retry_at = None

    await db.flush()
    return task


async def list_tasks_for_account(
    db: AsyncSession,
    *,
    ad_account_id: str,
    status: str | list[str] | None = None,
    limit: int = 100,
) -> list[MetaApiMutationTask]:
    """Получить задачи кабинета с опциональным фильтром по статусу.

    Args:
        db: AsyncSession
        ad_account_id: ID рекламного кабинета
        status: строка или список строк статусов; None — все статусы
        limit: максимальное количество записей в ответе
    """
    query = select(MetaApiMutationTask).where(MetaApiMutationTask.ad_account_id == ad_account_id)
    if status is not None:
        if isinstance(status, str):
            query = query.where(MetaApiMutationTask.status == status)
        else:
            query = query.where(MetaApiMutationTask.status.in_(status))

    query = query.order_by(MetaApiMutationTask.created_at.desc()).limit(limit)
    result = await db.execute(query)
    return list(result.scalars())
