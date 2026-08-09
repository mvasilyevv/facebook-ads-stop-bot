# -*- coding: utf-8 -*-
"""Strict Pydantic contracts for offer CRUD and rules."""

from __future__ import annotations

import re
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.money import (
    InvalidCurrencyAmountError,
    UnsupportedCurrencyExponentError,
    currency_exponent,
    require_exact_currency_amount,
    validated_currency_code,
)

# Паттерн для валидации кода оффера: 1-64 символа, A-Z 0-9 _ - .
_CODE_RE = re.compile(r"^[A-Z0-9_\-\.]{1,64}$")

# Числовой ID рекламного кабинета (без префикса act_), 1-32 цифры.
_ACCOUNT_ID_RE = re.compile(r"^\d{1,32}$")

# Код страны ISO-2 (две латинские буквы), напр. DE, KE, BR.
_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")


def _normalize_countries(values: list[str]) -> list[str]:
    """Нормализация гео: трим, upper, дедуп с сохранением порядка.

    Каждый код приводится к ISO-2 upper. Бросает ValueError при невалидном коде —
    защита от опечаток в гео-таргетинге (money-настройка кампании).
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        s = str(raw).strip().upper()
        if not s:
            continue
        if not _COUNTRY_RE.match(s):
            raise ValueError(f"countries: {raw!r} — ожидается код страны ISO-2 (напр. DE)")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _normalize_account_ids(values: list[str]) -> list[str]:
    """Нормализация списка кабинетов: трим, срез act_, дедуп с сохранением порядка.

    Бросает ValueError при нечисловом ID — защита от опечаток в money-настройке.
    """
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        s = str(raw).strip()
        if s.lower().startswith("act_"):
            s = s[4:]
        if not _ACCOUNT_ID_RE.match(s):
            raise ValueError(f"ad_account_ids: {raw!r} — ожидается числовой ID кабинета")
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ─────────────────────── Offer ───────────────────────


class OfferOut(BaseModel):
    """Ответ для одного оффера (GET /offers, POST /offers, PUT /offers/{id})."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    vertical: str | None = None
    # FB Pixel ID оффера (для создания кампаний — событие оптимизации Purchase/FTD).
    pixel_id: str | None = None
    is_active: bool
    # Мульти-кабинет: кабинеты оффера (числовые ID без act_). Scan set = union по активным.
    ad_account_ids: list[str] = Field(default_factory=list)
    # Гео оффера (ISO-2 upper, мультигео). Визард префиллит goal.countries.
    countries: list[str] = Field(default_factory=list)
    # Целевой CPA оффера из правил (offer_rules.cpa_threshold, доллары). Единый CPA:
    # и стоп-пороги, и префилл бида визарда. None — правила/CPA не заданы.
    cpa_threshold: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    created_at: str | None = None  # ISO-строка из ORM datetime
    updated_at: str | None = None

    @classmethod
    def from_orm_offer(
        cls,
        offer: object,
        *,
        ad_account_ids: list[str],
    ) -> "OfferOut":
        """Map an ORM offer plus its explicitly loaded normalized membership."""
        return cls(
            id=offer.id,  # type: ignore[attr-defined]
            code=offer.code,  # type: ignore[attr-defined]
            name=offer.name,  # type: ignore[attr-defined]
            vertical=offer.vertical,  # type: ignore[attr-defined]
            pixel_id=getattr(offer, "pixel_id", None),
            is_active=offer.is_active,  # type: ignore[attr-defined]
            ad_account_ids=sorted(ad_account_ids),
            countries=list(getattr(offer, "countries", None) or []),
            created_at=offer.created_at.isoformat() if offer.created_at else None,  # type: ignore[attr-defined]
            updated_at=offer.updated_at.isoformat() if offer.updated_at else None,  # type: ignore[attr-defined]
        )


class OfferCreateIn(BaseModel):
    """Тело POST /offers."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=64, description="Уникальный код оффера")
    vertical: str | None = Field(None, max_length=32)
    is_active: bool = True
    # FB Pixel ID оффера (числовой; пусто — не задан).
    pixel_id: str | None = Field(None, max_length=64)
    # Мульти-кабинет: кабинеты оффера, минимум 1 (без них оффер выпадает из скана).
    ad_account_ids: list[str] = Field(..., min_length=1)
    # Гео оффера (ISO-2 upper), дефолт пусто. Визард префиллит гео из этого списка.
    countries: list[str] = Field(default_factory=list)

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        """Код должен содержать только заглавные буквы, цифры, _ - ."""
        if not _CODE_RE.match(v):
            raise ValueError(
                "code должен содержать только A-Z, 0-9, _, -, . и иметь длину 1-64 символа"
            )
        return v

    @field_validator("ad_account_ids")
    @classmethod
    def validate_account_ids(cls, v: list[str]) -> list[str]:
        """Кабинеты: числовые ID, дедуп, минимум 1 после нормализации."""
        normalized = _normalize_account_ids(v)
        if not normalized:
            raise ValueError("ad_account_ids: нужен минимум один кабинет")
        return normalized

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, v: list[str]) -> list[str]:
        """Гео: ISO-2 upper, дедуп. Пустой список допустим."""
        return _normalize_countries(v)


class OfferUpdateIn(BaseModel):
    """Тело PUT /offers/{id}.

    Identity is immutable and therefore is not accepted in this payload.
    All mutable fields are optional.
    """

    model_config = ConfigDict(extra="forbid")

    vertical: str | None = Field(None, max_length=32)
    # FB Pixel ID: None — не трогать; строка (в т.ч. пустая → null) — заменить.
    pixel_id: str | None = Field(None, max_length=64)
    is_active: bool | None = None
    # Мульти-кабинет: None — не трогать; список — заменить (минимум 1 после нормализации).
    ad_account_ids: list[str] | None = None
    # Гео: None — не трогать; список (в т.ч. пустой) — заменить (ISO-2 upper, дедуп).
    countries: list[str] | None = None

    @field_validator("ad_account_ids")
    @classmethod
    def validate_account_ids(cls, v: list[str] | None) -> list[str] | None:
        """None — поле не меняется; список — нормализация + минимум 1 кабинет."""
        if v is None:
            return None
        normalized = _normalize_account_ids(v)
        if not normalized:
            raise ValueError("ad_account_ids: нужен минимум один кабинет")
        return normalized

    @field_validator("countries")
    @classmethod
    def validate_countries(cls, v: list[str] | None) -> list[str] | None:
        """None — поле не меняется; список — ISO-2 upper + дедуп (пустой допустим)."""
        if v is None:
            return None
        return _normalize_countries(v)


# ─────────────────────── OfferRule ───────────────────────


class OfferRuleOut(BaseModel):
    """Ответ на GET /offers/{id}/rules.

    Все пороговые поля nullable — правило неактивно при null.
    Если записи OfferRule для оффера нет — возвращаем объект с offer_id=<id>
    и всеми порогами null (дефолтная структура).
    """

    model_config = ConfigDict(from_attributes=True)

    offer_id: uuid.UUID | None = None

    cpa_threshold: Decimal | None = None
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    frequency_threshold: Decimal | None = None
    # Чувствительность (per-offer, дефолт 80/80). stop_percent_of_rule — стоп = N% от
    # базового правила (CPC-база 2%×CPA и т.д.); warning_percent_of_stop — ворнинг = M% от стопа.
    stop_percent_of_rule: Decimal = Decimal("80")
    warning_percent_of_stop: Decimal = Decimal("80")


class OfferRuleUpsertIn(BaseModel):
    """Тело PUT /offers/{id}/rules — upsert всех пороговых полей.

    Monetary/frequency пороги nullable; заданные значения строго положительны.
    """

    # NULL выключает rule; observer повторяет fail-closed проверку после прямых DB-записей.
    cpa_threshold: Decimal | None = Field(
        None,
        gt=0,
        max_digits=20,
        decimal_places=6,
    )
    currency: str | None = Field(default=None, pattern=r"^[A-Za-z]{3}$")
    frequency_threshold: Decimal | None = Field(None, gt=0)
    # Чувствительность 1–100% (всегда задано, дефолт 80). НЕ nullable — колонки NOT NULL.
    stop_percent_of_rule: Decimal = Field(Decimal("80"), ge=1, le=100)
    warning_percent_of_stop: Decimal = Field(Decimal("80"), ge=1, le=100)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = validated_currency_code(value)
        if normalized is None:
            raise ValueError("unknown ISO 4217 currency")
        try:
            currency_exponent(normalized)
        except UnsupportedCurrencyExponentError as exc:
            raise ValueError("currency has no reviewed exponent") from exc
        return normalized

    @model_validator(mode="after")
    def require_cpa_currency(self) -> "OfferRuleUpsertIn":
        if self.cpa_threshold is not None and self.currency is None:
            raise ValueError("currency is required when cpa_threshold is set")
        if self.cpa_threshold is not None and self.currency is not None:
            exponent = currency_exponent(self.currency)
            try:
                self.cpa_threshold = require_exact_currency_amount(
                    self.cpa_threshold,
                    currency=self.currency,
                    exponent=exponent,
                    field="cpa_threshold",
                    allow_zero=False,
                )
            except InvalidCurrencyAmountError as exc:
                raise ValueError(str(exc)) from exc
        return self


# ─────────────────────── Rule preview (live-расчёт стоимостей) ───────────────────────


class RuleThresholdPreview(BaseModel):
    """Один денежный порог: при какой стоимости сработают стоп и ворнинг."""

    rule: str
    label: str
    base: Decimal
    stop: Decimal
    warning: Decimal


class SpendRangePreview(BaseModel):
    """Диапазон расхода в валюте CPA."""

    rule: str
    label: str
    stop_from: Decimal
    stop_to: Decimal
    warning_from: Decimal


class RulePreviewOut(BaseModel):
    """Превью порогов автостопа для CPA + чувствительности.

    Считается через RuleContext — ТОТ ЖЕ расчёт, что применяет автостоп: цифры в UI
    совпадают с реальными порогами, по которым observer отключает объявления.
    """

    cpa: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    stop_percent_of_rule: Decimal
    warning_percent_of_stop: Decimal
    cost_rules: list[RuleThresholdPreview]
    spend_ranges: list[SpendRangePreview]
    regs_no_dep_stop_count: int
