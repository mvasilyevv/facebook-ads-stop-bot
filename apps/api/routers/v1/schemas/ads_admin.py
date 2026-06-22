# -*- coding: utf-8 -*-
"""Схемы для ads_admin (hard-delete объявлений)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BulkDeleteAdsRequest(BaseModel):
    """Список fb_ad_id для удаления (1..500 за запрос)."""

    fb_ad_ids: list[str] = Field(min_length=1, max_length=500)


class BulkDeleteAdsResponse(BaseModel):
    """Фактически удалённые fb_ad_id + отменённые orphan-задачи task_queue."""

    deleted: list[str]
    count: int
    # id отменённых (status='cancelled') active-задач outbox по удалённым ad_id —
    # защита от orphan pause_ad/activate_ad в meta_api_worker.
    cancelled_task_ids: list[int] = Field(default_factory=list)
