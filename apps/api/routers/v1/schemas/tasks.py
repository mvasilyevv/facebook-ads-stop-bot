# -*- coding: utf-8 -*-
"""Pydantic-схемы для task-секций DashboardPage.

Покрывает disable-tasks, enable-tasks и enable-recommendations endpoints.
Статусы хранятся в UPPERCASE (frontend-контракт), маппинг → БД через status_mapper.

Расхождения реальных полей TaskQueue от спека:
  - next_retry_at   (не next_attempt_at)
  - last_error      (не last_error_message)
  - created_by_chat_id (не requested_by_chat_id)
EnableRecommendation:
  - ad_id (UUID FK на fb_ads.id)  — нет прямого fb_ad_id; резолвим через JOIN
  - snapshot_metrics (не metrics_payload)
  - recommendation_level (не reason)
  - live_batch_started_at (дополнительное поле)
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TaskQueueRowOut(BaseModel):
    """Строка task_queue в формате для фронта.

    Поля приведены к frontend-контракту: uppercase status, camelCase-дружелюбные имена.
    Числовой id задачи → str (BigInt безопасен в JS только как строка).
    """

    model_config = ConfigDict(from_attributes=False)

    id: str
    fb_ad_id: str | None = None
    ad_name: str | None = None
    task_type: str
    status: str  # PENDING | RUNNING | RETRYING | FAILED | SUCCEEDED | CANCELLED
    attempt_count: int
    max_attempts: int
    requested_by: str
    # created_by_chat_id хранится в БД, фронт видит как requested_by_chat_id
    requested_by_chat_id: int | None = None
    created_at: datetime
    updated_at: datetime
    next_attempt_at: datetime | None = None  # mapped from next_retry_at
    last_error_message: str | None = None  # mapped from last_error


class DisableTaskCreateIn(BaseModel):
    """Тело POST /dashboard/disable-tasks."""

    model_config = ConfigDict(from_attributes=False)

    fb_ad_id: str = Field(..., description="Meta numeric ad ID")
    requested_by: str = Field(default="api_user", description="Инициатор задачи")
    requested_by_chat_id: int | None = Field(default=None, description="TG chat_id инициатора")
    reason: str = Field(default="manual disable", description="Причина отключения")


# ─────────────────────── bulk disable (money) ────────────────────────────────

# Cap размера batch. 50 — компромисс: bulk action-bar Ads оперирует видимой
# страницей объявлений (обычно 20-50 строк), а каждый ad_id порождает отдельную
# транзакцию INSERT в task_queue. Больший batch удлинял бы HTTP-запрос и держал
# пул соединений; при реальной необходимости >50 фронт шлёт несколько запросов
# с тем же idempotency_token (дубли не создадутся — UNIQUE per-ad ключ).
BULK_DISABLE_MAX_IDS = 50


class BulkDisableIn(BaseModel):
    """Тело POST /dashboard/disable-tasks/bulk (массовое отключение)."""

    model_config = ConfigDict(from_attributes=False)

    fb_ad_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Список Meta numeric ad ID (1..50)",
    )
    reason: str = Field(default="manual bulk disable", description="Причина отключения")
    idempotency_token: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Client-side токен против двойного submit (общий для всего batch)",
    )
    requested_by: str = Field(default="api_user", description="Инициатор (провенанс)")
    requested_by_chat_id: int | None = Field(default=None, description="TG chat_id инициатора")


class BulkDisableSkipped(BaseModel):
    """Объявление, для которого задача уже существовала (дубль idempotency_key)."""

    fb_ad_id: str
    task_id: str | None = None  # id уже существующей задачи, если удалось определить
    reason: str = "duplicate"


class BulkDisableFailed(BaseModel):
    """Объявление, для которого задачу создать не удалось."""

    fb_ad_id: str
    reason: str


class BulkDisableResultOut(BaseModel):
    """Partial-failure ответ bulk-отключения. HTTP 200 даже при частичном успехе."""

    model_config = ConfigDict(from_attributes=False)

    created: list[TaskQueueRowOut] = Field(default_factory=list)
    skipped: list[BulkDisableSkipped] = Field(default_factory=list)
    failed: list[BulkDisableFailed] = Field(default_factory=list)


class EnableTaskRowOut(TaskQueueRowOut):
    """Строка enable-задачи. Идентична disable, только task_type='enable'."""


class EnableRecommendationRowOut(BaseModel):
    """Строка enable_recommendations с JOIN по fb_ads и task_queue.

    Расхождение от спека: поле 'reason' в реальной модели — recommendation_level
    (ok/warning). Возвращаем оба поля для максимальной совместимости с фронтом.
    metrics_payload — алиас snapshot_metrics.
    """

    model_config = ConfigDict(from_attributes=False)

    id: str  # UUID → str
    fb_ad_id: str | None = None
    ad_name: str | None = None
    campaign_name: str | None = None

    # recommendation_level: ok/warning — фронт ожидает поле reason
    reason: str | None = None
    recommendation_level: str | None = None

    # snapshot_metrics хранится в БД, фронт ожидает metrics_payload
    metrics_payload: dict | None = None

    created_at: datetime
    live_batch_started_at: datetime | None = None

    promoted_to_task_id: int | None = None
    promoted_task_status: str | None = None  # UPPERCASE через status_mapper


class EnableRecommendationConfirmIn(BaseModel):
    """Тело POST /dashboard/enable-recommendations/{id}/enable."""

    model_config = ConfigDict(from_attributes=False)

    requested_by: str = Field(default="api_user")
    requested_by_chat_id: int | None = None
