# -*- coding: utf-8 -*-
"""Pydantic-схемы для desktop ads-actions endpoint'ов (snooze + bulk-snooze).

Порт snooze-логики из TMA (/tma/ads/{id}/snooze) на desktop-фронт. Desktop
/dashboard роутеры открыты (без Bearer-guard), как остальной desktop UI;
провенанс действия фиксируется в логах.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Cap размера batch для bulk-snooze. Совпадает с BULK_DISABLE_MAX_IDS:
# bulk action-bar Ads оперирует видимой страницей объявлений; больший batch
# держал бы транзакцию открытой дольше. >50 — фронт шлёт несколько запросов.
BULK_SNOOZE_MAX_IDS = 50

# Максимум снуза в минутах = 24 часа (1440). Дольше суток снузить смысла нет:
# кабинет работает посуточно, на следующий день incident переоценивается заново.
SNOOZE_MAX_MINUTES = 1440


class SnoozeIn(BaseModel):
    """Тело POST /dashboard/ads/{fb_ad_id}/snooze."""

    model_config = ConfigDict(from_attributes=False)

    minutes: int = Field(
        ...,
        ge=1,
        le=SNOOZE_MAX_MINUTES,
        description="Снуз в минутах (1..1440)",
    )


class SnoozeResultOut(BaseModel):
    """Результат одиночного снуза."""

    model_config = ConfigDict(from_attributes=False)

    ok: bool
    fb_ad_id: str
    snoozed_until: str


class BulkSnoozeIn(BaseModel):
    """Тело POST /dashboard/ads/bulk-snooze."""

    model_config = ConfigDict(from_attributes=False)

    fb_ad_ids: list[str] = Field(
        ...,
        min_length=1,
        description="Список Meta numeric ad ID (1..50)",
    )
    minutes: int = Field(
        ...,
        ge=1,
        le=SNOOZE_MAX_MINUTES,
        description="Снуз в минутах (1..1440)",
    )


class BulkSnoozeFailed(BaseModel):
    """Объявление, которое не удалось снузить (нет ad или нет состояния алерта)."""

    fb_ad_id: str
    reason: str


class BulkSnoozeResultOut(BaseModel):
    """Partial-failure ответ bulk-снуза. HTTP 200 даже при частичном успехе."""

    model_config = ConfigDict(from_attributes=False)

    snoozed_until: str
    snoozed: list[str] = Field(default_factory=list)  # fb_ad_id успешно снузленных
    failed: list[BulkSnoozeFailed] = Field(default_factory=list)
