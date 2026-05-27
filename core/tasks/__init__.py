# -*- coding: utf-8 -*-
"""Unified task_queue abstraction для всех outbox-воркеров v2.

Все типы (disable/enable/plan_run/meta_api_mutation/ad_library_scan) живут в одной
таблице task_queue с дискриминатором task_type. Этот модуль — единственный путь
к таблице, чтобы все воркеры наследовали единый retry/idempotency-протокол.
"""

from __future__ import annotations

from core.tasks.queue import (
    Task,
    TaskClaim,
    cancel_stale_drafts,
    claim_next_task,
    create_task,
    mark_failed,
    mark_succeeded,
    reconcile_stuck_running,
    requeue_for_retry,
)

__all__ = [
    "Task",
    "TaskClaim",
    "cancel_stale_drafts",
    "claim_next_task",
    "create_task",
    "mark_failed",
    "mark_succeeded",
    "reconcile_stuck_running",
    "requeue_for_retry",
]
