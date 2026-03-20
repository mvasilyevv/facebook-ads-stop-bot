from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from apps.api.schemas.common import ControlFlagTarget, TrackingMode


class ControlFlagItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    entity_type: ControlFlagTarget
    entity_external_id: str
    tracking_mode: TrackingMode
    reason: str
    created_by: str
    created_at: datetime
    expires_at: datetime | None = None


class ControlFlagCreateRequest(BaseModel):
    entity_type: ControlFlagTarget
    entity_external_id: str = Field(min_length=1)
    tracking_mode: TrackingMode
    reason: str = Field(min_length=1)
    created_by: str = Field(default="operator", min_length=1)
    expires_at: datetime | None = None
