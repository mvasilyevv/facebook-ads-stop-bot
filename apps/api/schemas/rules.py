from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class RuleItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    title: str
    description: str | None = None
    is_enabled: bool = True
    priority: int = 100
    cpa_multiplier: Decimal | None = None
    updated_at: datetime


class RuleUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    is_enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0)
    cpa_multiplier: Decimal | None = Field(default=None, gt=0)
