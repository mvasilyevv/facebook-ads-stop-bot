from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from apps.api.schemas.common import ControlFlagTarget


class OfferRateItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    offer_id: str
    cpa_usd: Decimal
    effective_from: datetime
    effective_to: datetime | None = None
    note: str | None = None
    created_at: datetime


class OfferItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    is_active: bool = True
    current_cpa_usd: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class OfferCreateRequest(BaseModel):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    is_active: bool = True


class OfferRateCreateRequest(BaseModel):
    cpa_usd: Decimal = Field(gt=0)
    effective_from: datetime
    effective_to: datetime | None = None
    note: str | None = None


class OfferBindingItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    entity_type: ControlFlagTarget
    entity_external_id: str
    offer_id: str
    offer_code: str
    priority: int = 0
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class OfferBindingCreateRequest(BaseModel):
    offer_id: str
    priority: int = 0
    is_active: bool = True


class OfferActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str
    offer: OfferItem


class OfferRateActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str
    rate: OfferRateItem


class OfferBindingActionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message: str
    binding: OfferBindingItem
