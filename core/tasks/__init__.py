# -*- coding: utf-8 -*-
"""Unified task_queue abstraction для всех outbox-воркеров.

Все активные типы живут в одной таблице task_queue с дискриминатором task_type.
Этот модуль — единственный путь
к таблице, чтобы все воркеры наследовали единый retry/idempotency-протокол.
"""

from __future__ import annotations

from core.tasks.queue import (
    Task,
    TaskClaim,
    claim_next_task,
    create_task,
    expire_overdue_tasks,
    mark_failed,
    mark_succeeded,
    reconcile_stuck_running,
    request_task_cancel,
    requeue_for_retry,
    requeue_proven_not_committed,
)

__all__ = [
    "Task",
    "TaskClaim",
    "claim_next_task",
    "create_task",
    "expire_overdue_tasks",
    "mark_failed",
    "mark_succeeded",
    "reconcile_stuck_running",
    "request_task_cancel",
    "requeue_for_retry",
    "requeue_proven_not_committed",
]
