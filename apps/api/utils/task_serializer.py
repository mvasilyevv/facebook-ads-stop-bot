# -*- coding: utf-8 -*-
"""Сериализация строки task_queue в dict frontend-контракта.

Дублировалось в disable_tasks.py, enable_tasks.py, enable_recommendations.py
(финальный ответ confirm_enable) и dashboard_stats.py (_query_recent_disable_tasks).
Единый источник правды.

Маппинг реальных полей TaskQueue → frontend-контракт (см. schemas/tasks.py):
  next_retry_at        → next_attempt_at
  last_error           → last_error_message
  created_by_chat_id   → requested_by_chat_id
  status (lowercase v2)→ UPPERCASE через status_mapper.to_frontend_task_status

Строка должна содержать атрибуты: id, fb_ad_id, ad_name, task_type, status,
attempt_count, max_attempts, requested_by, created_by_chat_id, created_at,
updated_at, next_retry_at, last_error.

datetime-поля возвращаются как объекты datetime — FastAPI jsonable_encoder
сериализует их в ISO-8601 при отдаче ответа (как для Pydantic-моделей с полем
datetime, так и для dict[str, Any] в DashboardBatchOut).
"""

from __future__ import annotations

from typing import Any

from apps.api.utils.status_mapper import to_frontend_task_status


def task_row_to_out(row: Any) -> dict[str, Any]:
    """Конвертирует строку task_queue в dict для TaskQueueRowOut/EnableTaskRowOut."""
    return {
        "id": str(row.id),
        "fb_ad_id": row.fb_ad_id,
        "ad_name": row.ad_name,
        "task_type": row.task_type,
        "status": to_frontend_task_status(row.status),
        "attempt_count": row.attempt_count,
        "max_attempts": row.max_attempts,
        "requested_by": row.requested_by,
        "requested_by_chat_id": row.created_by_chat_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "next_attempt_at": row.next_retry_at,
        "last_error_message": row.last_error,
    }


__all__ = ["task_row_to_out"]
