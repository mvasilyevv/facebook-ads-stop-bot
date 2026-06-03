# -*- coding: utf-8 -*-
"""Pydantic-схемы для роутера offers (CRUD + compare + rules).

схема БД: Offer содержит только code/name/vertical/is_active.
Поля country_code, use_vision_creator, notes отсутствуют в ORM —
возвращаются как null для совместимости с фронтовым shape.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Паттерн для валидации кода оффера: 1-64 символа, A-Z 0-9 _ - .
_CODE_RE = re.compile(r"^[A-Z0-9_\-\.]{1,64}$")


# ─────────────────────── Offer ───────────────────────


class OfferOut(BaseModel):
    """Ответ для одного оффера (GET /offers, POST /offers, PUT /offers/{id})."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    vertical: str | None = None
    # Поля отсутствующие в ORM — возвращаем null для стабильного shape фронта.
    country_code: None = None
    is_active: bool
    created_at: str | None = None  # ISO-строка из ORM datetime
    updated_at: str | None = None
    use_vision_creator: None = None
    notes: None = None

    @classmethod
    def from_orm_offer(cls, offer: object) -> "OfferOut":
        """Маппит ORM-объект Offer в OfferOut с учётом доступных полей."""
        return cls(
            id=offer.id,  # type: ignore[attr-defined]
            code=offer.code,  # type: ignore[attr-defined]
            name=offer.name,  # type: ignore[attr-defined]
            vertical=offer.vertical,  # type: ignore[attr-defined]
            is_active=offer.is_active,  # type: ignore[attr-defined]
            created_at=offer.created_at.isoformat() if offer.created_at else None,  # type: ignore[attr-defined]
            updated_at=offer.updated_at.isoformat() if offer.updated_at else None,  # type: ignore[attr-defined]
        )


class OfferCreateIn(BaseModel):
    """Тело POST /offers."""

    code: str = Field(..., min_length=1, max_length=64, description="Уникальный код оффера")
    # name убрано из формы — оффер именуется кодом (name = code в endpoint).
    # Принимаем опционально для обратной совместимости, но значение игнорируем.
    name: str | None = None
    vertical: str | None = Field(None, max_length=32)
    # country_code и notes принимаем но игнорируем (нет в ORM)
    country_code: str | None = None
    use_vision_creator: bool | None = None
    notes: str | None = None

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """Код должен содержать только заглавные буквы, цифры, _ - ."""
        if not _CODE_RE.match(v):
            raise ValueError(
                "code должен содержать только A-Z, 0-9, _, -, . и иметь длину 1-64 символа"
            )
        return v


class OfferUpdateIn(BaseModel):
    """Тело PUT /offers/{id}.

    code — immutable: если передан, игнорируется (не обновляется).
    Все остальные поля optional.
    """

    # Принимаем code, но не применяем его — задокументировано в docstring.
    code: str | None = None
    # name не редактируется — всегда равно коду; поле принимается, но игнорируется.
    name: str | None = None
    vertical: str | None = Field(None, max_length=32)
    is_active: bool | None = None
    country_code: str | None = None
    use_vision_creator: bool | None = None
    notes: str | None = None


# ─────────────────────── OfferCompare ───────────────────────


class OfferCompareRow(BaseModel):
    """Агрегированные метрики одного оффера для /offers/compare."""

    model_config = ConfigDict(from_attributes=True)

    offer_id: uuid.UUID
    offer_code: str
    offer_name: str
    days: int

    spend: Decimal
    leads: int
    registrations: int
    deposits: int
    active_ads_count: int
    stop_alerts_count: int

    # Вычисляемые cost_per_* — null если знаменатель = 0
    cost_per_lead: Decimal | None
    cost_per_registration: Decimal | None
    cost_per_deposit: Decimal | None


# ─────────────────────── OfferRule ───────────────────────


class OfferRuleOut(BaseModel):
    """Ответ на GET /offers/{id}/rules.

    Все пороговые поля nullable — правило неактивно при null.
    Если записи OfferRule для оффера нет — возвращаем объект с offer_id=<id>
    и всеми порогами null (дефолтная структура).
    """

    model_config = ConfigDict(from_attributes=True)

    offer_id: uuid.UUID | None = None

    spend_no_event_threshold: Decimal | None = None
    cpa_threshold: Decimal | None = None
    cpm_threshold: Decimal | None = None
    ctr_threshold: Decimal | None = None
    frequency_threshold: Decimal | None = None
    funnel_ratio_threshold: Decimal | None = None
    # Чувствительность (per-offer, дефолт 80/80). stop_percent_of_rule — стоп = N% от
    # базового правила (CPC-база 2%×CPA и т.д.); warning_percent_of_stop — ворнинг = M% от стопа.
    stop_percent_of_rule: Decimal = Decimal("80")
    warning_percent_of_stop: Decimal = Decimal("80")


class OfferRuleUpsertIn(BaseModel):
    """Тело PUT /offers/{id}/rules — upsert всех пороговых полей.

    Все поля nullable. Отрицательные пороги запрещены (ge=0).
    """

    spend_no_event_threshold: Decimal | None = Field(None, ge=0)
    cpa_threshold: Decimal | None = Field(None, ge=0)
    cpm_threshold: Decimal | None = Field(None, ge=0)
    ctr_threshold: Decimal | None = Field(None, ge=0, le=100)
    frequency_threshold: Decimal | None = Field(None, ge=0)
    funnel_ratio_threshold: Decimal | None = Field(None, ge=0, le=100)
    # Чувствительность 1–100% (всегда задано, дефолт 80). НЕ nullable — колонки NOT NULL.
    stop_percent_of_rule: Decimal = Field(Decimal("80"), ge=1, le=100)
    warning_percent_of_stop: Decimal = Field(Decimal("80"), ge=1, le=100)
