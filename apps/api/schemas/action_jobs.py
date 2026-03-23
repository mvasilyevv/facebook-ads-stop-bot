from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from apps.api.schemas.common import ActionJobStatus


class ActionJobItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    fb_ad_id: str
    profile_id: str | None = None
    browser_host_id: str | None = None
    campaign_name: str | None = None
    adset_name: str | None = None
    ad_name: str | None = None
    action_type: str
    status: ActionJobStatus
    priority_score: int
    attempt_count: int
    next_attempt_at: datetime
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
